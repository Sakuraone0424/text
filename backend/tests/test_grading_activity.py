"""Owner-scoped grading activity must report real stored grades, never fake model progress."""

from app import quiz_v2 as quiz
from app.grading_activity import auto_queue_allowed
from test_api_v2 import LEARNER


def test_grading_activity_requires_submitted_owner(client, db, running, monkeypatch):
    uid, paper = running
    pid = paper["paper_id"]
    assert client.get(f"/v2/papers/{pid}/grading-activity", headers=LEARNER).status_code == 404
    term = next(q for q in paper["questions"] if q["question_type"] == "term")
    answer = quiz.submit(db, uid, pid, {term["id"]: "synthetic answer for pending review"})
    result = client.get(f"/v2/papers/{pid}/grading-activity", headers=LEARNER)
    assert result.status_code == 200
    progress = result.json()
    assert progress["total"] == 28
    assert progress["graded"] == 27
    assert progress["pending"] == 1
    assert progress["complete"] is False
    pending = next(r for r in progress["questions"] if r["question_id"] == term["id"])
    assert pending["state"] == "pending_manual_or_queue"
    assert pending["points"] is None
    assert progress["auto_queue_available"] is False
    assert "answer" not in pending and "payload" not in pending
    monkeypatch.setenv("QUIZ_USERNAME", "different-owner")
    assert client.get(f"/v2/papers/{pid}/grading-activity", headers=LEARNER).status_code == 404


def test_grading_activity_unstarted_does_not_enqueue_or_spend(db, running, monkeypatch):
    uid, paper = running
    monkeypatch.delenv("QUIZ_AUTO_GRADE_ON_SUBMIT", raising=False)
    allowed, reason = auto_queue_allowed(db, uid)
    assert not allowed and reason == "submit_auto_grading_disabled"
    assert db.execute("SELECT count(*) AS n FROM ai_jobs").fetchone()["n"] == 0
