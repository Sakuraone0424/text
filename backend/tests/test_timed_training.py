from datetime import timedelta
import io

from PIL import Image
import pytest
from pydantic import ValidationError
from app import quiz_v2 as q
from app import training_sessions as sessions
from app.common import ensure_user
from app.question_quality import Blueprint, Question
from app.training_spec import POINTS, validate_items
from test_api_v2 import LEARNER, REVIEWER


def test_single_current_product_contract_and_retired_legacy_routes(client):
    from app.training_spec import default_blueprint

    spec = client.get("/v2/training-specification")
    assert spec.status_code == 200
    value = spec.json()
    assert (value["questions"], value["points"], value["duration_seconds"]) == (28, 150, 3600)
    assert (value["questions_per_subject"], value["points_per_subject"]) == (14, 75)
    expected = dict(single_choice=3, fill_blank=3, term=4, short_answer=3, essay=1)
    assert value["quotas"] == {s: expected for s in ("biochemistry", "cell_biology")}
    assert value["points_by_type"] == dict(single_choice=2, fill_blank=3, term=4, short_answer=8, essay=20)
    assert default_blueprint() == {k: value[k] for k in ("specification", "quotas")}
    for route in ("/", "/training"):
        page = client.get(route).text
        assert "唯一训练规格：28题" in page and "各14题75分" in page
        assert "3道单选" in page and "3道填空" in page and "4道名词解释" in page
        assert "3道简答" in page and "1道论述" in page
        assert "60题" not in page and "双模式" not in page
    assert client.get("/quiz/today", headers=LEARNER).status_code == 410
    assert client.post("/quiz/submit", headers=LEARNER).status_code == 410


def test_daily_sixty_and_mode_switch_requests_are_rejected(client):
    data = {
        "specification": "daily-60",
        "quotas": {s: {"single_choice": 15, "fill_blank": 15} for s in ("biochemistry", "cell_biology")},
    }
    assert client.post("/v2/papers", headers=LEARNER, json=data).status_code == 422
    from app.training_spec import default_blueprint

    data = {**default_blueprint(), "mode": "daily-60"}
    assert client.post("/v2/papers", headers=LEARNER, json=data).status_code == 422


def test_chatgpt_adapter_uses_only_formal_blueprint():
    from app.chatgpt_adapter import TrainingAdapter
    from app.training_spec import default_blueprint

    class Transport:
        def request(self, method, path, *, json):
            assert method == "POST" and path == "/v2/papers"
            assert json == default_blueprint()
            class Response:
                def raise_for_status(self): pass
                def json(self): return {"prepared": True}
            return Response()

    assert TrainingAdapter(Transport()).prepare() == {"prepared": True}


def expire(db, pid):
    now = sessions.server_now(db)
    db.execute(
        "UPDATE training_sessions SET started_at=%s,deadline=%s WHERE paper_id=%s",
        (now - timedelta(hours=2), now - timedelta(hours=1), pid),
    )


def test_strict_150_point_specification(db, bank, blueprint):
    paper = q.create_paper(db, ensure_user(db, "new"), blueprint)
    assert (
        paper["count"] == 28
        and paper["max_points"] == 150
        and paper["duration_seconds"] == 3600
    )
    assert paper["questions"] == [] and paper["timing"]["status"] == "not_started"
    rows = db.execute("SELECT snapshot FROM paper_items_v2").fetchall()
    validate_items([r["snapshot"] for r in rows])
    for subject in ("biochemistry", "cell_biology"):
        selected = [r["snapshot"] for r in rows if r["snapshot"]["subject"] == subject]
        assert len(selected) == 14 and sum(r["max_points"] for r in selected) == 75


@pytest.mark.parametrize("started", [False, True])
def test_first_paper_remains_reachable_after_midnight(db, bank, blueprint, monkeypatch, started):
    from datetime import date

    uid = ensure_user(db, "cross-day-owner")
    monkeypatch.setattr(q, "today_cn", lambda: date(2026, 10, 28))
    first = q.create_paper(db, uid, blueprint)
    if started:
        sessions.start(db, uid, first["paper_id"])
    monkeypatch.setattr(q, "today_cn", lambda: date(2026, 10, 29))
    restored = q.create_paper(db, uid, blueprint)
    assert restored["paper_id"] == first["paper_id"]
    assert restored["timing"]["status"] == ("in_progress" if started else "not_started")
    assert db.execute("SELECT count(*) AS n FROM papers_v2 WHERE user_id=%s", (uid,)).fetchone()["n"] == 1


@pytest.mark.parametrize("kind", ["count", "points", "duration"])
def test_blueprint_cannot_silently_change_specification(blueprint, kind):
    data = blueprint.model_dump()
    if kind == "count":
        data["quotas"]["biochemistry"].update(single_choice=4, term=3)
    elif kind == "points":
        data["points"] = {**POINTS, "essay": 12}
    else:
        data["duration_seconds"] = 9000
    with pytest.raises(ValidationError):
        Blueprint.model_validate(data)


def test_reject_wrong_question_points_and_rubric(question_factory):
    data = question_factory(kind="essay").model_dump()
    data["max_points"] = 12
    data["rubric"][0]["points"] = 12
    with pytest.raises(ValidationError):
        Question.model_validate(data)
    data = question_factory(kind="essay").model_dump()
    data["rubric"][0]["points"] = 19
    with pytest.raises(ValidationError):
        Question.model_validate(data)


def test_start_is_explicit_and_idempotent(db, bank, blueprint):
    uid = ensure_user(db, "test-owner")
    paper = q.create_paper(db, uid, blueprint)
    pid = paper["paper_id"]
    with pytest.raises(ValueError, match="Start"):
        q.submit(db, uid, pid, {})
    first = sessions.start(db, uid, pid)
    second = sessions.start(db, uid, pid)
    assert (
        first["started_at"] == second["started_at"]
        and first["deadline"] == second["deadline"]
    )
    assert (first["deadline"] - first["started_at"]).total_seconds() == 3600
    assert q.load_paper(db, uid, pid)["questions"]


def test_autosave_restore_and_multidevice_conflict(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    saved = sessions.save(db, uid, pid, qid, {"text": "A"}, 0)
    assert saved["version"] == 1
    with pytest.raises(ValueError, match="version_conflict"):
        sessions.save(db, uid, pid, qid, {"text": "B"}, 0)
    restored = q.load_paper(db, uid, pid)
    assert restored["timing"]["drafts"][str(qid)]["text"] == "A"
    assert restored["timing"]["deadline"] == paper["timing"]["deadline"]
    with pytest.raises(ValueError, match="version_conflict"):
        q.submit(db, uid, pid, {}, 0)
    assert q.submit(db, uid, pid, {}, 1)["score"] == 2


def test_partial_and_blank_answers_are_zero_but_subjective_pending(db, running):
    uid, paper = running
    subjective = next(i for i in paper["questions"] if i["question_type"] == "term")
    result = q.submit(
        db, uid, paper["paper_id"], {subjective["id"]: "Actual explanation"}
    )
    assert (
        result["pending"] == 1
        and result["score"] is None
        and result["max_points"] == 150
    )
    assert (
        len(result["answers"]) == 28
        and sum(a["points"] == 0 for a in result["answers"]) == 27
    )


def test_empty_submission_is_accepted_once(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    result = q.submit(db, uid, pid, {})
    assert result["score"] == 0 and result["pending"] == 0
    repeated = q.submit(db, uid, pid, {})
    assert repeated["attempt_id"] == result["attempt_id"] and repeated["score"] == 0
    late = q.submit(db, uid, pid, {paper["questions"][0]["id"]: "A"})
    assert late["attempt_id"] == result["attempt_id"] and late["score"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM attempts_v2").fetchone()["n"] == 1
    assert db.execute("SELECT COUNT(*) AS n FROM fsrs_events").fetchone()["n"] == 0


def test_worker_submits_disconnected_user_and_ignores_late_text(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    sessions.save(db, uid, pid, qid, {"text": "A"}, 0)
    expire(db, pid)
    assert sessions.expire_due(db) == 1
    assert sessions.expire_due(db) == 0
    result = q.result(db, uid, pid)
    assert result["score"] == 2 and result["pending"] == 0
    with pytest.raises(ValueError):
        sessions.save(db, uid, pid, qid, {"text": "B"}, 1)
    assert q.result(db, uid, pid)["score"] == 2


def test_late_submit_uses_only_predeadline_saved_answers(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    expire(db, pid)
    result = q.submit(db, uid, pid, {qid: "A"})
    assert result["score"] == 0


def test_late_save_commits_timeout_without_accepting_draft(client, db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    expire(db, pid)
    response = client.put(
        f"/v2/papers/{pid}/drafts/{qid}",
        headers=LEARNER,
        json={"expected_version": 0, "value": {"text": "A"}},
    )
    assert response.status_code == 200 and not response.json()["accepted"]
    assert client.get(f"/v2/papers/{pid}/result", headers=LEARNER).json()["score"] == 0


def image_bytes(fmt="PNG"):
    out = io.BytesIO()
    Image.new("RGB", (12, 12), "white").save(out, format=fmt)
    return out.getvalue()


def test_image_upload_privacy_pending_confirmation_and_deadline(
    client, db, running, monkeypatch
):
    uid, paper = running
    pid = paper["paper_id"]
    qid = next(i["id"] for i in paper["questions"] if i["question_type"] == "term")
    response = client.post(
        f"/v2/papers/{pid}/images/{qid}?expected_version=0",
        headers=LEARNER,
        content=image_bytes(),
    )
    assert response.status_code == 200 and response.json()["accepted"]
    image_id = response.json()["image_id"]
    assert (
        client.get(f"/v2/images/{image_id}", headers=LEARNER).headers["cache-control"]
        == "no-store"
    )
    monkeypatch.setenv("QUIZ_USERNAME", "stranger")
    assert client.get(f"/v2/images/{image_id}", headers=LEARNER).status_code == 404
    monkeypatch.setenv("QUIZ_USERNAME", "test-owner")
    result = client.post(
        f"/v2/papers/{pid}/submit", headers=LEARNER, json={"expected_version": 1}
    ).json()
    assert result["pending"] == 1 and result["score"] is None
    grade_path = f"/v2/papers/{pid}/answers/{qid}/grade"
    payload = {
        "criterion_points": [4],
        "feedback": "Verified against rubric",
        "reviewer": "expert",
    }
    assert client.post(grade_path, headers=REVIEWER, json=payload).status_code == 409
    assert (
        client.post(
            f"/v2/images/{image_id}/transcription",
            headers=REVIEWER,
            json={
                "text": "Unclear",
                "confidence": 0.2,
                "confirmed": False,
                "reviewer": "expert",
            },
        ).status_code
        == 200
    )
    assert client.post(grade_path, headers=REVIEWER, json=payload).status_code == 409
    assert (
        client.post(
            f"/v2/images/{image_id}/transcription",
            headers=REVIEWER,
            json={
                "text": "Manually confirmed content",
                "confidence": 1,
                "confirmed": True,
                "reviewer": "expert",
            },
        ).status_code
        == 200
    )
    assert client.post(grade_path, headers=REVIEWER, json=payload).json()["score"] == 4


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_supported_images_are_decoded(fmt):
    from app.images import verify_image

    assert verify_image(image_bytes(fmt)).startswith("image/")


def test_invalid_image_and_timed_out_upload(client, db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = next(i["id"] for i in paper["questions"] if i["question_type"] == "essay")
    url = f"/v2/papers/{pid}/images/{qid}?expected_version=0"
    assert (
        client.post(url, headers=LEARNER, content=b"<script>bad</script>").status_code
        == 422
    )
    expire(db, pid)
    response = client.post(url, headers=LEARNER, content=image_bytes())
    assert not response.json()["accepted"]
    assert db.execute("SELECT COUNT(*) AS n FROM answer_images").fetchone()["n"] == 0


def test_multiblank_scoring_per_blank(
    client, db, bank, blueprint, question_factory, passing_audit
):
    # Replace one published single-blank question with an independently reviewed revision.
    row = db.execute(
        "SELECT id FROM reviewed_questions WHERE content->>'question_type'='fill_blank' LIMIT 1"
    ).fetchone()
    db.execute(
        "UPDATE reviewed_questions SET status='withdrawn' WHERE id=%s", (row["id"],)
    )
    question = question_factory(kind="fill_blank")
    data = question.model_dump()
    data.update(
        stem="Two independent synthetic scientific blanks ____ and ____ "
        + __import__("uuid").uuid4().hex,
        accepted_answers=[],
        blanks=[
            {"accepted_answers": ["ATP"], "points": 1},
            {"accepted_answers": ["ADP", "二磷酸腺苷"], "points": 2},
        ],
    )
    qid = q.create_question(db, Question.model_validate(data))["id"]
    q.review_question(db, qid, passing_audit)
    q.publish_question(db, qid)
    uid = ensure_user(db, "test-owner")
    pid = q.create_paper(db, uid, blueprint)["paper_id"]
    sessions.start(db, uid, pid)
    response = client.post(
        f"/v2/papers/{pid}/submit",
        headers=LEARNER,
        json={"answers": {str(qid): {"blanks": ["ATP", "wrong"]}}},
    )
    assert response.status_code == 200
    result = response.json()
    answer = next(a for a in result["answers"] if a["question_id"] == qid)
    assert result["score"] == 1 and answer["feedback"]["blank_points"] == [1, 0]


def test_detailed_grading_audit_is_append_only(db, running):
    import psycopg

    uid, paper = running
    pid = paper["paper_id"]
    qid = next(
        i["id"] for i in paper["questions"] if i["question_type"] == "short_answer"
    )
    q.submit(db, uid, pid, {qid: "My actual answer"})
    result = q.grade(
        db,
        pid,
        qid,
        [4],
        "Two points need more explanation",
        "expert",
        details={
            "concept_errors": ["Incorrect mechanism"],
            "suggestions": ["Review source section"],
        },
    )
    graded = next(a for a in result["answers"] if a["question_id"] == qid)
    assert graded["feedback"]["criteria"][0]["max_points"] == 8
    assert graded["feedback"]["missing_points"] and graded["feedback"]["concept_errors"]
    before = db.execute("SELECT COUNT(*) AS n FROM fsrs_events").fetchone()["n"]
    with pytest.raises(ValueError):
        q.grade(db, pid, qid, [8], "Overwrite attempt", "expert")
    assert db.execute("SELECT COUNT(*) AS n FROM fsrs_events").fetchone()["n"] == before
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute("DELETE FROM grading_events")


def test_upload_retry_does_not_duplicate_images_or_versions(client, db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = next(i["id"] for i in paper["questions"] if i["question_type"] == "essay")
    url = f"/v2/papers/{pid}/images/{qid}?expected_version=0"
    first = client.post(url, headers=LEARNER, content=image_bytes()).json()
    retried = client.post(url, headers=LEARNER, content=image_bytes()).json()
    assert (
        first["image_id"] == retried["image_id"]
        and first["version"] == retried["version"] == 1
    )
    assert db.execute("SELECT COUNT(*) AS n FROM answer_images").fetchone()["n"] == 1


def test_deadline_crossed_during_submission_drops_new_payload(db, running, monkeypatch):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    deadline = paper["timing"]["deadline"]
    calls = []

    def clock(conn):
        calls.append(1)
        return (
            deadline - timedelta(seconds=1)
            if len(calls) == 1
            else deadline + timedelta(seconds=1)
        )

    monkeypatch.setattr(sessions, "server_now", clock)
    result = q.submit(db, uid, pid, {qid: "A"})
    assert result["score"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM answer_drafts").fetchone()["n"] == 0


def test_owned_reflection_taxonomy_is_idempotent_and_does_not_regrade(
    client, db, running, monkeypatch
):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    q.submit(db, uid, pid, {})
    url = f"/v2/papers/{pid}/answers/{qid}/reflection"
    payload = {
        "event_key": "actual-reflection",
        "category": "misread_question",
        "note": "I missed the question wording",
    }
    assert client.post(url, headers=LEARNER, json=payload).json()["recorded"]
    assert client.post(url, headers=LEARNER, json=payload).json()["replayed"]
    assert (
        client.post(
            url, headers=LEARNER, json={**payload, "category": "invented"}
        ).status_code
        == 422
    )
    monkeypatch.setenv("QUIZ_USERNAME", "stranger")
    assert client.post(url, headers=LEARNER, json=payload).status_code == 404
    assert q.result(db, uid, pid)["score"] == 0


def test_truncated_jpeg_is_rejected_before_storage():
    from app.images import verify_image

    with pytest.raises(ValueError):
        verify_image(image_bytes("JPEG")[:-10])


def test_learning_views_are_owned_and_archive_configuration_is_truthful(
    client, db, running, monkeypatch
):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    q.submit(db, uid, pid, {qid: "B"})
    assert (
        client.get(f"/v2/memory/{qid}/solution", headers=LEARNER).json()["answer"]
        == "A"
    )
    assert client.get("/v2/stats/weekly", headers=LEARNER).json()["chapters"]
    assert not client.get("/v2/archive/status", headers=LEARNER).json()["configured"]
    assert (
        client.put(
            "/v2/archive/consent", headers=LEARNER, json={"enabled": True}
        ).status_code
        == 200
    )
    assert (
        client.get("/v2/archive/status", headers=LEARNER).json()["jobs"][0]["state"]
        == "pending"
    )
    monkeypatch.setenv("QUIZ_USERNAME", "stranger")
    assert client.get(f"/v2/memory/{qid}/solution", headers=LEARNER).status_code == 404
    assert client.get("/v2/stats/weekly", headers=LEARNER).json()["chapters"] == []


def test_prestart_server_rejects_drafts_images_and_submit_without_writes(db, bank, blueprint):
    """Existing server gates are independently verified; no DB schema/data fix required."""
    from app.images import store_image
    uid = ensure_user(db, "prestart-owner")
    paper = q.create_paper(db, uid, blueprint)
    pid = paper["paper_id"]
    qid = db.execute("SELECT question_id FROM paper_items_v2 WHERE paper_id=%s LIMIT 1", (pid,)).fetchone()["question_id"]
    assert q.load_paper(db, uid, pid)["questions"] == []
    for action in (
        lambda: sessions.save(db, uid, pid, qid, {"text": "A"}, 0),
        lambda: store_image(db, uid, pid, qid, b"image", "image/png", 0),
        lambda: sessions.submit_training(db, uid, pid, {qid: {"text": "A"}}, 0),
    ):
        with pytest.raises(ValueError, match="Start the training"):
            action()
    for table in ("training_sessions", "answer_drafts", "answer_images", "attempts_v2"):
        assert db.execute(f"SELECT count(*) AS n FROM {table} WHERE paper_id=%s", (pid,)).fetchone()["n"] == 0
    started = sessions.start(db, uid, pid)
    assert started["drafts"] == {} and started["version"] == 0
    assert q.load_paper(db, uid, pid)["questions"]
