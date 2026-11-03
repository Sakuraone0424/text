from copy import deepcopy
from datetime import timedelta
import pytest
from app import quiz_v2 as q, training_sessions as sessions, memory


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
