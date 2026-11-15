"""Step 6: grading after submit. Fake model results only; no API is called."""
import json

import pytest
from psycopg.types.json import Jsonb

from app import auto_execution, auto_grading as grading, auto_worker, grading_activity, model_router as m, quiz_v2 as quiz
from test_ai_automation import setup_ai  # noqa: F401  (fixture)

LEARNER = {"x-api-key": "learner-test-key"}


@pytest.fixture
def learner(db, setup_ai, running):
    uid, paper = running
    m.configure(db, uid, "deepseek-flash", "high", stopped=False, daily_cny=1, monthly_cny=5, task_cny=1, trial_cny=5)
    return uid, paper


def submit(db, uid, paper, text="Synthetic answer with an incomplete mechanism"):
    quiz.submit(db, uid, paper["paper_id"],
                {q["id"]: ("A" if q["question_type"] == "single_choice" else text) for q in paper["questions"]})


def verdict(job, *, uncertain=False, points=None):
    q = job["payload"]["question"]
    scores = points if points is not None else [0 for _ in q["rubric"]]
    return dict(
        real_api=True,
        response=dict(
            criterion_points=scores,
            reasons=["Synthetic reason for this criterion, not real grading" for _ in q["rubric"]],
            total=sum(scores),
            uncertain=uncertain,
            explanation="Synthetic explanation of the judgement, not a scientific assessment",
            knowledge_tags=q["knowledge_tags"],
        ),
    )


def finish(db, job, result):
    db.execute("UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s", (Jsonb(result), job["id"]))


def grading_jobs(db, qid=None):
    return [j for j in db.execute("SELECT * FROM ai_jobs WHERE kind='grading' ORDER BY id").fetchall()
            if qid is None or j["payload"]["question_id"] == qid]


def row(db, paper_id, qid):
    return db.execute("SELECT a.* FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id WHERE t.paper_id=%s AND a.question_id=%s",
                      (paper_id, qid)).fetchone()


# ---- what is graded how --------------------------------------------------------------------


def test_objective_answers_are_scored_at_once_and_subjective_ones_wait_for_the_ai(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    result = quiz.result(db, uid, paper["paper_id"])
    kinds = {a["question_type"]: [] for a in result["answers"]}
    for a in result["answers"]:
        kinds[a["question_type"]].append(a["points"])
    assert all(p is not None for p in kinds["single_choice"])  # scored on submit
    assert all(p is None for p in kinds["term"] + kinds["short_answer"])  # waiting, not zero
    assert result["status"] == "pending_review" and result["score"] is None and result["pending"] > 0


def test_the_grading_request_carries_only_rubric_cited_excerpts_and_the_typed_answer(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    jobs = grading.schedule(db, uid, paper["paper_id"])
    assert jobs
    for job in jobs:
        p = job["payload"]
        assert set(p) == {"instructions", "schema", "paper_id", "question_id", "attempt_id", "answer_sha256",
                          "question", "evidence", "answer"}
        assert set(p["answer"]) == {"text", "blanks"}  # no image ids, flags or other fields
        snapshot = db.execute("SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s AND question_id=%s",
                              (paper["paper_id"], p["question_id"])).fetchone()["snapshot"]
        assert [e["text"] for e in p["evidence"]] == [e["excerpt"] for e in snapshot["evidence"]]  # excerpts, not whole units
        assert len(p["evidence"]) <= auto_execution.MAX_EVIDENCE_UNITS
        auto_execution.bounded_material(p)  # within the hard per-request cap
        assert p["instructions"] == grading.GRADING_INSTRUCTIONS


def test_a_disputed_fill_blank_goes_to_the_ai_instead_of_being_scored_wrong(db, learner):
    uid, paper = learner
    for q in paper["questions"]:  # automatically checked questions: a mismatch is a dispute, not a zero
        if q["question_type"] == "fill_blank":
            db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x') ON CONFLICT DO NOTHING", (q["id"],))
    submit(db, uid, paper, text="a scientifically equivalent but differently worded answer")
    fills = [j for j in grading.schedule(db, uid, paper["paper_id"]) if j["payload"]["question"]["question_type"] == "fill_blank"]
    assert fills
    for job in fills:
        saved = row(db, paper["paper_id"], job["payload"]["question_id"])
        assert saved["points"] is None and saved["feedback"]["semantic_judgment_pending"] is True


# ---- uncertainty: one automatic second look, then pending with a reason -----------------


def first_job(db, learner_state):
    uid, paper = learner_state
    submit(db, uid, paper)
    job = grading.schedule(db, uid, paper["paper_id"])[0]
    return uid, paper, job


def test_an_uncertain_result_is_reassessed_once_and_a_clear_second_result_is_applied(db, learner):
    uid, paper, job = first_job(db, learner)
    qid = job["payload"]["question_id"]
    finish(db, job, verdict(job, uncertain=True))
    auto_worker.dispatch_completed(db)
    jobs = grading_jobs(db, qid)
    assert [j["job_key"].endswith(":reassessment") for j in jobs] == [False, True]
    assert jobs[1]["payload"]["instructions"].startswith(grading.REASSESS_INSTRUCTIONS)
    assert row(db, paper["paper_id"], qid)["points"] is None  # still not zero
    finish(db, jobs[1], verdict(jobs[1], points=[c["points"] for c in jobs[1]["payload"]["question"]["rubric"]]))
    auto_worker.dispatch_completed(db)
    graded = row(db, paper["paper_id"], qid)
    assert graded["points"] == jobs[1]["payload"]["question"]["max_points"] and graded["graded_by"] == "AI自动核验"
    assert len(grading_jobs(db, qid)) == 2  # no third attempt


def test_two_uncertain_results_leave_the_answer_pending_with_a_reason_never_zero(db, learner):
    uid, paper, job = first_job(db, learner)
    qid = job["payload"]["question_id"]
    for _ in range(2):
        pending = [j for j in grading_jobs(db, qid) if j["state"] == "pending"]
        finish(db, pending[0], verdict(pending[0], uncertain=True))
        auto_worker.dispatch_completed(db)
    assert len(grading_jobs(db, qid)) == 2
    auto_worker.dispatch_completed(db)
    assert len(grading_jobs(db, qid)) == 2  # nothing more is ever queued for it
    assert row(db, paper["paper_id"], qid)["points"] is None
    shown = {q["question_id"]: q for q in grading_activity.progress(db, uid, paper["paper_id"])["questions"]}[qid]
    assert shown["state"] == "blocked" and shown["points"] is None
    assert shown["pending_reason"] == "uncertain_after_second_assessment"
    assert shown["pending_detail"].startswith("Synthetic explanation")
    assert quiz.result(db, uid, paper["paper_id"])["score"] is None


def test_a_malformed_result_gets_the_same_single_second_look(db, learner):
    uid, paper, job = first_job(db, learner)
    qid = job["payload"]["question_id"]
    finish(db, job, dict(real_api=True, response={"criterion_points": [99], "total": 99}))
    auto_worker.dispatch_completed(db)
    assert len(grading_jobs(db, qid)) == 2 and row(db, paper["paper_id"], qid)["points"] is None


# ---- no double charge ----------------------------------------------------------------------


def test_repeating_the_submit_or_the_queueing_never_creates_a_second_paid_job(db, learner, monkeypatch):
    uid, paper = learner
    submit(db, uid, paper)
    first = {j["id"] for j in grading.schedule(db, uid, paper["paper_id"])}
    submit(db, uid, paper, text="a different late payload that must be ignored")
    for _ in range(3):
        assert {j["id"] for j in grading.schedule(db, uid, paper["paper_id"])} == first
    for key, value in dict(QUIZ_AUTO_GRADE_ON_SUBMIT="1", AI_AUTOMATION_ENABLED="1",
                           DEEPSEEK_PRIVATE_DATA_AUTHORIZED="1", DEEPSEEK_API_AUTHORIZED="1").items():
        monkeypatch.setenv(key, value)
    grading_activity.try_queue(db, uid, paper["paper_id"])
    grading_activity.try_queue(db, uid, paper["paper_id"])
    assert {j["id"] for j in grading_jobs(db)} == first
    assert db.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"] == 0  # nothing reserved by queueing


def test_a_graded_answer_is_never_overwritten_by_a_late_result(db, learner):
    uid, paper, job = first_job(db, learner)
    finish(db, job, verdict(job))
    job = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    grading.apply(db, job)
    assert grading.apply(db, job) == "already_applied"
    other = {**job, "id": job["id"] + 9999}
    with pytest.raises(ValueError, match="historical_score"):
        grading.apply(db, other)


# ---- visible results -------------------------------------------------------------------------


def test_the_result_shows_every_criterion_score_with_its_reason(db, learner):
    uid, paper, job = first_job(db, learner)
    q = job["payload"]["question"]
    scores = [c["points"] if i == 0 else 0 for i, c in enumerate(q["rubric"])]
    finish(db, job, verdict(job, points=scores))
    auto_worker.dispatch_completed(db)
    answer = next(a for a in quiz.result(db, uid, paper["paper_id"])["answers"] if a["question_id"] == job["payload"]["question_id"])
    record = answer["feedback"]
    assert [c["points"] for c in record["criteria"]] == scores
    assert len(record["criterion_reasons"]) == len(q["rubric"]) and all(len(r) >= 10 for r in record["criterion_reasons"])
    assert record["comment"].startswith("Synthetic explanation") and answer["points"] == sum(scores)


# ---- answers that cannot be graded automatically -----------------------------------------------


def test_an_over_long_answer_is_not_sent_and_stays_pending_with_a_reason(db, learner):
    uid, paper = learner
    submit(db, uid, paper, text="长" * (grading.MAX_ANSWER_CHARS + 1))
    assert grading.schedule(db, uid, paper["paper_id"]) == []
    shown = grading_activity.progress(db, uid, paper["paper_id"])
    reasons = {q["pending_reason"] for q in shown["questions"] if q["points"] is None}
    assert reasons == {"answer_too_long_for_automatic_grading"}
    assert all(j["state"] == "blocked" and j["error_code"] == "answer_too_long_for_automatic_grading" for j in grading_jobs(db))
    assert db.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"] == 0  # no model call was ever reserved


@pytest.mark.parametrize(
    "points,job,photo,allowed,why,expected",
    [
        (3, None, False, True, None, None),
        (None, None, True, True, None, "photo_answers_not_supported"),
        (None, None, False, True, None, "waiting_to_be_queued"),
        (None, None, False, False, "owner_ai_settings_stopped", "owner_ai_settings_stopped"),
        (None, {"state": "retrying", "error_code": None, "job_key": "k", "detail": None}, False, True, None, "queued_for_ai_grading"),
        (None, {"state": "running", "error_code": None, "job_key": "k", "detail": None}, False, True, None, "ai_grading_in_progress"),
        (None, {"state": "blocked", "error_code": "budget_exceeded", "job_key": "k", "detail": None}, False, True, None, "ai_budget_exhausted"),
        (None, {"state": "blocked", "error_code": "automation_stopped", "job_key": "k", "detail": None}, False, True, None, "ai_grading_stopped"),
        (None, {"state": "blocked", "error_code": "invalid_or_uncertain_result", "job_key": "g", "detail": "x"}, False, True, None, "reassessing"),
        (None, {"state": "blocked", "error_code": "provider_http_400", "job_key": "g", "detail": None}, False, True, None, "ai_grading_failed"),
    ],
)
def test_every_pending_state_has_a_plain_reason(points, job, photo, allowed, why, expected):
    assert grading_activity.pending_reason(points, job, photo, allowed, why)["pending_reason"] == expected


# ---- typed answers only ----------------------------------------------------------------------------


def test_photo_answers_are_off_unless_explicitly_enabled(client, db, running, monkeypatch):
    monkeypatch.delenv("QUIZ_PHOTO_ANSWERS_ENABLED", raising=False)
    uid, paper = running
    term = next(q for q in paper["questions"] if q["question_type"] == "term")
    from app import training_sessions as sessions

    with pytest.raises(ValueError, match="Photo answers are turned off"):
        sessions.valid_value(db, paper["paper_id"], term["id"], {"text": "", "blanks": [], "image_ids": [1]})
    assert sessions.valid_value(db, paper["paper_id"], term["id"], {"text": "typed answer"})["text"] == "typed answer"
    response = client.post(f"/v2/papers/{paper['paper_id']}/images/{term['id']}?expected_version=0", content=b"x", headers=LEARNER)
    assert response.status_code == 410 and "type your answer" in response.json()["detail"]
    assert client.get("/v2/training-specification", headers=LEARNER).json()["photo_answers_enabled"] is False
    monkeypatch.setenv("QUIZ_PHOTO_ANSWERS_ENABLED", "1")
    assert client.get("/v2/training-specification", headers=LEARNER).json()["photo_answers_enabled"] is True


def test_the_grading_request_never_contains_image_data(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    for job in grading.schedule(db, uid, paper["paper_id"]):
        assert "image" not in json.dumps(job["payload"]).lower().replace("images are", "")


# ---- "这题有问题" -------------------------------------------------------------------------------------


def first_subjective(paper):
    return next(q for q in paper["questions"] if q["question_type"] == "term")


def test_reporting_a_question_removes_it_from_this_papers_score_and_marks_it(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    target = first_subjective(paper)
    before = quiz.result(db, uid, paper["paper_id"])
    summary = quiz.report_question(db, uid, paper["paper_id"], target["id"], "题干有歧义")
    assert summary["excluded_points"] == target["max_points"] == 4
    after = quiz.result(db, uid, paper["paper_id"])
    assert after["nominal_max_points"] == before["max_points"]
    assert after["max_points"] == before["max_points"] - 4
    assert after["excluded"] == {"question_ids": [target["id"]], "points": 4}
    flagged = [a for a in after["answers"] if a["excluded"]]
    assert [a["question_id"] for a in flagged] == [target["id"]]
    assert after["pending"] == before["pending"] - 1  # it no longer holds the whole paper's score back
    assert quiz.report_question(db, uid, paper["paper_id"], target["id"])["excluded_points"] == 4  # idempotent
    assert db.execute("SELECT count(*) AS n FROM question_reports").fetchone()["n"] == 1
    assert db.execute("SELECT note FROM question_reports").fetchone()["note"] == "题干有歧义"


def test_a_reported_answer_is_not_graded_or_paid_for(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    target = first_subjective(paper)
    quiz.report_question(db, uid, paper["paper_id"], target["id"])
    jobs = grading.schedule(db, uid, paper["paper_id"])
    assert target["id"] not in {j["payload"]["question_id"] for j in jobs}
    shown = {q["question_id"]: q for q in grading_activity.progress(db, uid, paper["paper_id"])["questions"]}[target["id"]]
    assert shown["state"] == "excluded" and shown["excluded"] and shown["pending_reason"] is None


def test_the_score_completes_once_every_other_answer_is_graded(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    target = first_subjective(paper)
    quiz.report_question(db, uid, paper["paper_id"], target["id"])
    for job in grading.schedule(db, uid, paper["paper_id"]):
        finish(db, job, verdict(job, points=[c["points"] for c in job["payload"]["question"]["rubric"]]))
    auto_worker.dispatch_completed(db)
    result = quiz.result(db, uid, paper["paper_id"])
    assert result["status"] == "graded" and result["pending"] == 0
    assert result["score"] == sum(a["points"] for a in result["answers"] if not a["excluded"])
    assert result["max_points"] == result["nominal_max_points"] - 4


def test_a_reported_question_has_no_review_card_and_is_never_selected_for_that_learner(db, learner):
    from app import memory
    from app.common import ensure_user
    from app.question_quality import Blueprint

    uid, paper = learner
    submit(db, uid, paper)
    wrong = next(q for q in paper["questions"] if q["question_type"] == "single_choice")
    quiz.report_question(db, uid, paper["paper_id"], wrong["id"])
    assert db.execute("SELECT count(*) AS n FROM fsrs_cards WHERE question_id=%s", (wrong["id"],)).fetchone()["n"] == 0
    assert wrong["id"] not in {r["question_id"] for r in memory.due(db, uid)}

    # Two fresh learners see the same bank. The one who reported `wrong` (any paper may anchor the row)
    # must be short exactly that question; the other gets a full paper.
    spec = Blueprint.model_validate(db.execute("SELECT blueprint FROM papers_v2 WHERE id=%s", (paper["paper_id"],)).fetchone()["blueprint"])
    reporter, other = ensure_user(db, "learner-who-reported"), ensure_user(db, "learner-who-did-not")
    full = quiz.create_paper(db, other, spec)
    assert full["count"] == 28
    db.execute("INSERT INTO question_reports(paper_id,question_id,user_id) VALUES (%s,%s,%s)", (full["paper_id"], wrong["id"], reporter))
    with pytest.raises(quiz.BankShortage) as short:
        quiz.create_paper(db, reporter, spec)
    missing = [(x["subject"], x["question_type"], x["missing"]) for x in short.value.shortages if x["missing"]]
    assert missing == [(wrong["subject"], "single_choice", 1)]


def test_reporting_requires_an_owned_started_paper_and_one_of_its_questions(db, learner):
    uid, paper = learner
    with pytest.raises(ValueError, match="does not belong"):
        quiz.report_question(db, uid, paper["paper_id"], 99999999)
    other = db.execute("INSERT INTO users(username) VALUES ('someone-else') RETURNING id").fetchone()["id"]
    with pytest.raises(LookupError):
        quiz.report_question(db, other, paper["paper_id"], paper["questions"][0]["id"])


def test_report_endpoint_and_history_use_the_reduced_total(client, db, running):
    uid, paper = running
    submit(db, uid, paper)
    term = first_subjective(paper)
    response = client.post(f"/v2/papers/{paper['paper_id']}/questions/{term['id']}/report", json={"note": "不对"}, headers=LEARNER)
    assert response.status_code == 200 and response.json()["excluded_points"] == 4
    record = client.get("/v2/history", headers=LEARNER).json()["training"][0]
    assert record["excluded"] == 1 and record["max_points"] == paper["max_points"] - 4
    assert client.get(f"/v2/papers/{paper['paper_id']}", headers=LEARNER).json()["reported_question_ids"] == [term["id"]]
