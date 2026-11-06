"""Short chapter / weak-point practice: objective questions only, checked at once, never part of a paper.

Practice answers feed chapter accuracy but not the daily paper, its scores or the history trends.
"""
from psycopg.types.json import Jsonb

from app import learning_stats as stats
from app.question_quality import normalize_answer

MAX_SET = 20
SUBJECTIVE = ("term", "short_answer", "essay")      # graded by the AI, not at once


def public(content, qid):
    """The question as the learner may see it before answering (no answer, no rubric)."""
    out = dict(
        id=qid, subject=content["subject"], question_type=content["question_type"], stem=content["stem"],
        max_points=content["max_points"], chapter=(content.get("evidence") or [{}])[0].get("chapter"),
    )
    if content["question_type"] == "single_choice":
        out["options"] = content["options"]
    if content["question_type"] == "fill_blank":
        out["blank_count"] = len(content.get("blanks") or []) or 1
    return out


def score(content, value):
    """(points, per_blank) for one objective answer; exact accepted-answer matching only."""
    q = content
    text = (value.get("text") or "").strip()
    if q["question_type"] == "single_choice":
        return (q["max_points"] if text.upper() == q["answer"] else 0), None
    if q.get("blanks"):
        supplied = value.get("blanks") or []
        per = [
            b["points"] if n < len(supplied) and normalize_answer(supplied[n]) in {normalize_answer(a) for a in b["accepted_answers"]} else 0
            for n, b in enumerate(q["blanks"])
        ]
        return sum(per), per
    ok = normalize_answer(text) in {normalize_answer(a) for a in q["accepted_answers"]}
    return (q["max_points"] if ok else 0), None


def start(conn, uid, mode, subject=None, chapter=None, size=None, question_type=None, year=None, seconds=None):
    """All kinds of practice live in practice_center (the old chapter / weak-point practice is two of its six kinds)."""
    from app import practice_center

    return practice_center.start(conn, uid, mode, subject, chapter, question_type, year, seconds, size)


def owned(conn, uid, set_id):
    row = conn.execute("SELECT * FROM practice_sets WHERE id=%s AND user_id=%s", (set_id, uid)).fetchone()
    if not row:
        raise LookupError("practice_set_not_found")
    return row


def answer(conn, uid, set_id, question_id, value, seconds=None):
    practice = owned(conn, uid, set_id)
    if question_id not in practice["question_ids"]:
        raise ValueError("question_not_in_this_set")
    if not isinstance(value, dict) or set(value) - {"text", "blanks"}:
        raise ValueError("invalid_answer")
    if conn.execute("SELECT 1 FROM practice_answers WHERE set_id=%s AND question_id=%s", (set_id, question_id)).fetchone():
        raise ValueError("already_answered")
    content = conn.execute("SELECT content FROM reviewed_questions WHERE id=%s", (question_id,)).fetchone()["content"]
    if seconds is not None and not (type(seconds) is int and 0 <= seconds <= 7200):
        raise ValueError("invalid_time")
    if content["question_type"] in SUBJECTIVE:
        # term / short answer / essay: no score yet; the AI grades it like a paper answer and writes the score (and the mistake card) later
        if not (value.get("text") or "").strip():
            raise ValueError("answer_required")
        conn.execute(
            "INSERT INTO practice_answers(set_id,question_id,answer,points,max_points,seconds) VALUES (%s,%s,%s,NULL,%s,%s)",
            (set_id, question_id, Jsonb(value), content["max_points"], seconds),
        )
        from app import auto_grading

        job = auto_grading.schedule_practice(conn, uid, set_id, question_id, content, value)
        return dict(
            question_id=question_id, correct=None, points=None, max_points=content["max_points"], pending=True, queued=job is not None,
            answer=content.get("answer"), accepted_answers=[], blank_points=None,
            why=[c["description"] for c in content.get("rubric", [])][:6],
        )
    points, per_blank = score(content, value)
    conn.execute(
        "INSERT INTO practice_answers(set_id,question_id,answer,points,max_points,seconds) VALUES (%s,%s,%s,%s,%s,%s)",
        (set_id, question_id, Jsonb(value), points, content["max_points"], seconds),
    )
    if points < content["max_points"]:
        from app import memory

        memory.record_practice_failure(conn, uid, question_id, set_id)        # a missed practice question joins the mistake book
    return dict(
        question_id=question_id, correct=points == content["max_points"], points=points, max_points=content["max_points"],
        answer=content["answer"], accepted_answers=content.get("accepted_answers") or [], blank_points=per_blank,
        why=[c["description"] for c in content.get("rubric", [])][:3],
    )


def summary(conn, uid, set_id):
    practice = owned(conn, uid, set_id)
    answered = {
        r["question_id"]: r
        for r in conn.execute("SELECT * FROM practice_answers WHERE set_id=%s", (set_id,)).fetchall()
    }
    done = list(answered.values())
    return dict(
        set_id=set_id, mode=practice["mode"], total=len(practice["question_ids"]), answered=len(done),
        points=sum(r["points"] or 0 for r in done), max_points=sum(r["max_points"] for r in done), pending=sum(1 for r in done if r["points"] is None),
        correct=sum(1 for r in done if r["points"] is not None and r["points"] == r["max_points"]),
        remaining=[qid for qid in practice["question_ids"] if qid not in answered],
    )
