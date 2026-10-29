"""Isolated PostgreSQL test: 60q full flow with synthetic fixtures only.

For this *test only*, synthetic candidate state is injected after publication
and the independent-review predicate is stubbed. Production refuses unverified
published material; this test validates the exam/score/transaction mechanics.
"""
from datetime import date, timedelta

from app.common import ensure_user
from app import quiz_v2, training_sessions, auto_quality
from app.question_quality import Question, Blueprint
from app.training_spec_v2 import COUNTS, POINTS, SPECIFICATION, default_blueprint


def test_sixty_question_prepare_start_submit_readback(db, question_factory, passing_audit, monkeypatch):
    bank_ids = []
    for subject in ("biochemistry", "cell_biology"):
        for kind, count in COUNTS.items():
            for _ in range(count):
                old = question_factory(subject, kind)
                raw = old.model_dump()
                raw["max_points"] = POINTS[kind]
                raw["rubric"] = [{"description": "Synthetic fixture criterion", "points": POINTS[kind]}]
                updated = Question.model_validate(raw)
                qid = quiz_v2.create_question(db, updated)["id"]
                assert quiz_v2.review_question(db, qid, passing_audit)["passed"]
                quiz_v2.publish_question(db, qid)
                db.execute(
                    "INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready',%s)",
                    (qid, "synthetic-only-test"),
                )
                bank_ids.append(qid)
    assert len(bank_ids) == 60
    monkeypatch.setattr(auto_quality, "ready", lambda c, qid: qid in bank_ids)
    uid = ensure_user(db, "v2-test-student")
    spec = Blueprint.model_validate({
        "specification": SPECIFICATION, "quotas": default_blueprint()["quotas"],
    })
    prepared = quiz_v2.create_paper(db, uid, spec, day=date(2026, 12, 1))
    pid = prepared["paper_id"]
    assert prepared["count"] == 60
    assert prepared["max_points"] == 150
    assert prepared["mode"] == "reviewed_60"
    assert prepared["questions"] == []
    running = training_sessions.start(db, uid, pid)
    assert running["status"] == "in_progress"
    revealed = quiz_v2.load_paper(db, uid, pid)
    assert len(revealed["questions"]) == 60
    assert sum(q["max_points"] for q in revealed["questions"]) == 150
    assert all("answer" not in q for q in revealed["questions"])
    assert all("rubric" not in q for q in revealed["questions"])
    choice = next(q for q in revealed["questions"] if q["question_type"] == "single_choice")
    name = next(q for q in revealed["questions"] if q["question_type"] == "term")
    blank = next(q for q in revealed["questions"] if q["question_type"] == "fill_blank")
    short = next(q for q in revealed["questions"] if q["question_type"] == "short_answer")
    answers = {
        choice["id"]: {"text": "A"},
        blank["id"]: {"text": "fixture solution"},
        name["id"]: {"text": "Synthetic definition answer"},
        short["id"]: {"text": "Synthetic explanation of the underlying process"},
    }
    outcome = quiz_v2.submit(db, uid, pid, answers, expected_version=0)
    assert outcome["pending"] == 2
    assert outcome["score"] is None
    assert outcome["max_points"] == 150
    assert len(outcome["answers"]) == 60
    assert len(db.execute(
        "SELECT * FROM paper_items_v2 WHERE paper_id=%s", (pid,)
    ).fetchall()) == 60
    assert len(db.execute(
        "SELECT * FROM answers_v2 WHERE attempt_id=%s", (outcome["attempt_id"],)
    ).fetchall()) == 60
    repeated = quiz_v2.submit(db, uid, pid, {}, expected_version=0)
    assert repeated["attempt_id"] == outcome["attempt_id"]
    assert repeated["pending"] == 2
    assert repeated["score"] is None
