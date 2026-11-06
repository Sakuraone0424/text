"""Answers stuck without a score: found, put back in the queue, explained in Chinese, and asked again on request."""
import pytest

from app import auto_grading as grading, grading_activity, grading_recovery as recovery, quiz_v2 as quiz
from test_api_v2 import LEARNER as API_LEARNER
from test_auto_grading import learner, submit  # noqa: F401  (fixtures)
from test_ai_automation import setup_ai  # noqa: F401


def block_all(db, code):
    db.execute("UPDATE ai_jobs SET state='blocked',error_code=%s WHERE kind='grading'", (code,))


def states(db):
    return sorted({(r["state"], r["error_code"]) for r in db.execute("SELECT state,error_code FROM ai_jobs WHERE kind='grading'").fetchall()})


@pytest.mark.parametrize("code", ["budget_exceeded", "verified_cny_price_required", "daily_budget_exceeded", "legacy_currency_job_requires_requeue",
                                  "provider_http_400", "worker_restarted", "released_on_shutdown"])
def test_a_grading_task_blocked_for_a_reason_that_has_passed_goes_back_in_the_queue(db, learner, code):
    uid, paper = learner
    submit(db, uid, paper)
    assert grading.schedule(db, uid, paper["paper_id"])
    block_all(db, code)
    result = recovery.requeue(db, uid)
    assert result["requeued"] > 0
    assert states(db) == [("retrying", None)]


def test_an_answer_too_long_stays_blocked(db, learner):
    uid, paper = learner
    submit(db, uid, paper, text="长" * 4000)
    grading.schedule(db, uid, paper["paper_id"])
    assert recovery.requeue(db, uid, final_too=True).get("too_long")
    assert all(s == ("blocked", "answer_too_long_for_automatic_grading") for s in states(db))


def test_an_answer_that_never_got_a_task_gets_one(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='grading'").fetchone()["n"] == 0
    result = recovery.requeue(db, uid)
    assert result["new_task"] > 0 and db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='grading' AND state='pending'").fetchone()["n"] == result["new_task"]
    assert recovery.requeue(db, uid) == {"already_waiting": result["new_task"]}            # nothing written twice


def test_an_undecided_answer_is_asked_again_once_by_the_recovery_and_whenever_the_learner_presses_the_button(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    jobs = grading.schedule(db, uid, paper["paper_id"])
    block_all(db, "invalid_or_uncertain_result")
    ordinary = recovery.requeue(db, uid)                       # the housekeeping alone leaves a final "undecided" alone
    assert ordinary.get("left_undecided") == len(jobs) and ordinary.get("new_task") is None
    first = recovery.recover(db, uid)
    assert first["recovery"]["new_task"] == len(jobs)
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE job_key LIKE '%%:regrade%%' AND state='pending'").fetchone()["n"] == len(jobs)
    again = recovery.recover(db, uid)                          # the one-off recovery does not run twice
    assert "recovery" not in again
    db.execute("UPDATE ai_jobs SET state='blocked',error_code='invalid_or_uncertain_result' WHERE kind='grading'")
    pressed = recovery.requeue(db, uid, paper_id=paper["paper_id"], final_too=True)      # the button: always asks again
    assert pressed["new_task"] == len(jobs)


def test_the_report_names_every_unfinished_answer_with_its_reason_and_task_state(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    grading.schedule(db, uid, paper["paper_id"])
    block_all(db, "budget_exceeded")
    rows = recovery.report(db, uid)
    assert rows["rows"] and {r["pending_reason"] for r in rows["rows"]} == {"ai_budget_exhausted"}
    assert all(r["job_state"] == "blocked" and r["error_code"] == "budget_exceeded" and r["question_type"] for r in rows["rows"])
    assert rows["by_reason"] == {"ai_budget_exhausted": len(rows["rows"])}


def test_every_pending_reason_has_a_chinese_text(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    grading.schedule(db, uid, paper["paper_id"])
    for code, reason in [("budget_exceeded", "ai_budget_exhausted"), ("automation_stopped", "ai_grading_stopped"), ("invalid_or_uncertain_result", "reassessing"),
                         ("provider_http_503", "ai_grading_failed")]:
        block_all(db, code)
        questions = grading_activity.progress(db, uid, paper["paper_id"])["questions"]
        pending = [q for q in questions if q["points"] is None]
        assert pending and all(q["pending_reason"] == reason and q["pending_text"] and not q["pending_text"].isascii() for q in pending), (code, pending[0])
    assert all(not v.isascii() for v in recovery.REASON_TEXT.values())


def test_the_result_shows_the_objective_part_while_subjective_answers_wait(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    result = quiz.result(db, uid, paper["paper_id"])
    assert result["score"] is None and result["subjective_pending"] > 0
    assert result["objective_max"] > 0 and result["objective_points"] <= result["objective_max"]
    assert result["grading_late"]["count"] == 0                                          # just handed in: not late


def test_late_grading_is_reported_after_half_an_hour(db, learner):
    uid, paper = learner
    submit(db, uid, paper)
    assert recovery.late(db, uid, paper["paper_id"])["count"] == 0
    db.execute("UPDATE attempts_v2 SET submitted_at=submitted_at-interval '40 minutes' WHERE paper_id=%s", (paper["paper_id"],))
    late = recovery.late(db, uid, paper["paper_id"])
    assert late["count"] > 0 and late["minutes"] == 30
    assert quiz.result(db, uid, paper["paper_id"])["grading_late"]["count"] == late["count"]


def test_the_regrade_buttons_queue_the_paper_or_one_question_again(client, db, learner):
    uid, paper = learner
    pid = paper["paper_id"]
    quiz.submit(db, uid, pid, {q["id"]: ("A" if q["question_type"] == "single_choice" else "一个作答") for q in paper["questions"]})
    pending = [r["question_id"] for r in db.execute("SELECT question_id FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id WHERE t.paper_id=%s AND a.points IS NULL", (pid,)).fetchall()]
    assert pending
    one = client.post(f"/v2/papers/{pid}/regrade/{pending[0]}", headers=API_LEARNER)
    assert one.status_code == 200 and one.json().get("new_task") == 1
    whole = client.post(f"/v2/papers/{pid}/regrade", headers=API_LEARNER)
    assert whole.status_code == 200 and whole.json()["new_task"] == len(pending) - 1 and whole.json()["already_waiting"] == 1
    assert client.post("/v2/papers/999999/regrade", headers=API_LEARNER).status_code == 404
