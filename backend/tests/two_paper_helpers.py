"""A synthetic bank for the two daily papers (review predicates stubbed: mechanics tests only, never production)."""
from app import auto_quality, quiz_v2, source_policy
from app.ai_jobs import content_sha
from app.question_quality import Question
from app.training_spec_v5 import COUNTS, POINTS, SUBJECTS
from spec_helpers import rubric


def add_question(db, question_factory, passing_audit, subject, kind, points=None, status_ready=True):
    raw = question_factory(subject, kind).model_dump()
    raw["max_points"] = points if points is not None else POINTS[kind]
    raw["rubric"] = rubric(kind, raw["max_points"])
    if raw.get("blanks"):
        raw["blanks"] = [dict(b, points=1) for b in raw["blanks"]]
    qid = quiz_v2.create_question(db, Question.model_validate(raw))["id"]
    assert quiz_v2.review_question(db, qid, passing_audit)["passed"]
    quiz_v2.publish_question(db, qid)
    if status_ready:
        content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready',%s)", (qid, content_sha(content)))
    return qid


def seed_bank(db, question_factory, passing_audit, monkeypatch, types=None, days=1):
    """`days` days' worth of every (subject, type) in `types` (default all five), all ready and pinned."""
    ids = []
    for subject in SUBJECTS:
        for kind, count in COUNTS.items():
            if types and kind not in types:
                continue
            for _ in range(count * days):
                ids.append(add_question(db, question_factory, passing_audit, subject, kind))
    monkeypatch.setattr(auto_quality, "ready", lambda c, qid: qid in ids)
    monkeypatch.setattr(source_policy, "pinned_science_evidence", lambda content: True)
    return ids
