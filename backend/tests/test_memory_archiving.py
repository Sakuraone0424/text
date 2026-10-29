from copy import deepcopy
from datetime import timedelta
import pytest
from app import quiz_v2 as q, training_sessions as sessions, memory, archiving


def test_fsrs_real_feedback_updates_memory_and_replays_once(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    q.submit(db, uid, pid, {qid: "B"})
    row = db.execute(
        "SELECT * FROM fsrs_cards WHERE user_id=%s AND question_id=%s", (uid, qid)
    ).fetchone()
    assert (
        row["card"]["stability"] is not None and row["card"]["difficulty"] is not None
    )
    assert row["due"] < sessions.server_now(db) + timedelta(days=1)
    db.execute(
        "UPDATE fsrs_cards SET due=clock_timestamp() WHERE user_id=%s AND question_id=%s",
        (uid, qid),
    )
    after = memory.review(db, uid, qid, 3, "actual-feedback", row["version"])
    assert after["card"]["last_review"] and after["card"] != row["card"]
    assert memory.review(db, uid, qid, 3, "actual-feedback", row["version"])["replayed"]
    with pytest.raises(ValueError):
        memory.review(db, uid, qid, 1, "actual-feedback", row["version"])
    assert (
        db.execute(
            "SELECT COUNT(*) AS n FROM fsrs_events WHERE question_id=%s", (qid,)
        ).fetchone()["n"]
        == 2
    )


def test_retests_count_in_28_and_are_explicitly_labelled(db, running, blueprint):
    uid, paper = running
    pid = paper["paper_id"]
    # All answered incorrectly -> all are genuinely due FSRS cards.
    answers = {
        i["id"]: "B" if i["question_type"] == "single_choice" else "wrong"
        for i in paper["questions"]
    }
    q.submit(db, uid, pid, answers)
    for item in paper["questions"]:
        if item["question_type"] not in ("single_choice", "fill_blank"):
            q.grade(db, pid, item["id"], [0], "Incorrect observed answer", "expert")
    db.execute("UPDATE fsrs_cards SET due=clock_timestamp()")
    next_paper = q.create_paper(
        db,
        uid,
        blueprint,
        __import__("app.common", fromlist=["today_cn"]).today_cn() + timedelta(days=1),
    )
    sessions.start(db, uid, next_paper["paper_id"])
    restored = q.load_paper(db, uid, next_paper["paper_id"])
    assert len(restored["questions"]) == 28 and all(
        i["is_retest"] for i in restored["questions"]
    )
    assert restored["max_points"] == 150


class FakeNotion:
    def __init__(self):
        self.data = {}
        self.writes = 0
        self.fail = False
        self.mismatch = False

    def write(self, key, payload, remote_id=None):
        self.writes += 1
        if self.fail:
            raise ValueError("transient")
        self.data[key] = deepcopy(payload)
        return key

    def read(self, key):
        return {} if self.mismatch else deepcopy(self.data[key])


def consent(db, uid, value=True):
    db.execute(
        "INSERT INTO archive_consent VALUES (%s,%s) ON CONFLICT(user_id) DO UPDATE SET enabled=EXCLUDED.enabled",
        (uid, value),
    )


def test_archive_requires_consent_retry_readback_and_deduplication(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    provider = FakeNotion()
    q.submit(db, uid, pid, {})
    assert not archiving.process_one(db, provider) and not provider.writes
    consent(db, uid)
    archiving.enqueue(db, uid, pid)
    provider.fail = True
    archiving.process_one(db, provider)
    row = db.execute("SELECT * FROM archive_jobs").fetchone()
    assert row["state"] == "failed" and not row["verified_at"]
    provider.fail = False
    provider.mismatch = True
    db.execute("UPDATE archive_jobs SET next_retry=clock_timestamp()")
    archiving.process_one(db, provider)
    assert db.execute("SELECT state FROM archive_jobs").fetchone()["state"] == "failed"
    provider.mismatch = False
    db.execute("UPDATE archive_jobs SET next_retry=clock_timestamp()")
    archiving.process_one(db, provider)
    assert (
        db.execute("SELECT state FROM archive_jobs").fetchone()["state"] == "verified"
    )
    archiving.enqueue(db, uid, pid)
    archiving.process_one(db, provider)
    assert (
        len(provider.data) == 1
        and db.execute("SELECT COUNT(*) AS n FROM archive_jobs").fetchone()["n"] == 1
    )
    consent(db, uid, False)
    archiving.enqueue(db, uid, pid)
    assert not archiving.process_one(db, provider)


def test_unconfigured_notion_is_disabled(monkeypatch):
    monkeypatch.delenv("NOTION_SYNC_ENABLED", raising=False)
    assert archiving.configured_adapter() is None
    monkeypatch.setenv("NOTION_SYNC_ENABLED", "1")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    assert archiving.configured_adapter() is None


def test_chatgpt_adapter_contract_uses_actual_v2_api(client, bank):
    from app.chatgpt_adapter import TrainingAdapter

    client.headers.update({"x-api-key": "learner-test-key"})
    adapter = TrainingAdapter(client)
    from app.training_spec import default_blueprint as historical_28q_blueprint
    prepared = adapter.prepare(historical_blueprint=historical_28q_blueprint())
    assert prepared["count"] == 28
    assert not prepared["questions"]
    active = adapter.start(prepared["paper_id"])
    qid = active["questions"][0]["id"]
    pid = active["paper_id"]
    saved = adapter.save(pid, qid, {"text": "A"}, 0)
    assert adapter.restore(pid)["timing"]["drafts"][str(qid)]["text"] == "A"
    assert adapter.submit(pid, saved["version"])["score"] == 2
    assert adapter.result(pid)["max_points"] == 150


def test_notion_http_adapter_is_idempotent_and_readback_checks_chunks():
    import json
    import httpx

    blocks = [
        {
            "id": "user-note",
            "paragraph": {
                "rich_text": [{"plain_text": "User-authored note must remain"}]
            },
        }
    ]
    calls = []
    pages = []

    def handle(request):
        calls.append((request.method, request.url.path))
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [{"id": p} for p in pages]})
        if request.method == "POST" and request.url.path.endswith("/pages"):
            pages.append("page-1")
            return httpx.Response(200, json={"id": "page-1"})
        if request.method == "PATCH":
            for block in body["children"]:
                assert (
                    len(block["paragraph"]["rich_text"][0]["text"]["content"]) <= 2000
                )
                blocks.append({"id": str(len(blocks)), **block})
            return httpx.Response(200, json={})
        if request.method == "GET":
            return httpx.Response(200, json={"results": blocks, "has_more": False})
        raise AssertionError("Unexpected write or delete")

    provider = archiving.NotionAdapter(
        "synthetic", "db-test", transport=httpx.MockTransport(handle)
    )
    payload = {"answers": ["Synthetic " + ('"\\\n' * 1000)], "score": 150}
    remote = provider.write("quiz-attempt:1", payload)
    assert archiving.digest(provider.read(remote)) == archiving.digest(payload)
    before = len(blocks)
    provider.write("quiz-attempt:1", payload)
    assert len(pages) == 1 and len(blocks) == before and blocks[0]["id"] == "user-note"
    assert not any(method == "DELETE" for method, _ in calls)
    provider.close()


def test_archive_contains_observed_mastery_change_and_selection_basis(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    consent(db, uid)
    qid = next(
        i["id"] for i in paper["questions"] if i["question_type"] == "single_choice"
    )
    q.submit(db, uid, pid, {qid: "B"})
    job = db.execute("SELECT payload FROM archive_jobs").fetchone()["payload"]
    assert job["paper_date"] == paper["date"]
    assert job["selection_analysis"]["algorithm_version"] == "knowledge-priority/1.0"
    change = job["knowledge_mastery_change"]
    assert change["status"] == "recorded_estimates"
    assert "not_causal_gain" in change["meaning"]
    observed = [r for r in change["topics"] if r["answer_samples_delta"] > 0]
    assert len(observed) == 1
    assert observed[0]["answer_samples_delta"] == 1
    assert observed[0]["gap_delta"] > 0
    assert all(
        r["answer_samples_delta"] == 0 for r in change["topics"] if r not in observed
    )
    provider = FakeNotion()
    archiving.process_one(db, provider)
    assert (
        db.execute("SELECT state FROM archive_jobs").fetchone()["state"] == "verified"
    )
    assert next(iter(provider.data.values()))["knowledge_mastery_change"] == change
