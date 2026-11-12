"""Grading by hand: any answer of a handed-in paper can be scored (or its score changed) by the learner.

- a pending written answer is graded for the first time (标注「手动判分」);
- an answer that already has a score can be changed (标注「已手动调整」); the AI's / original score stays on record and can be looked at;
- the score counts, the mistake classification (60 % / 85 %) and the review cards follow it;
- an AI regrade never overwrites a manual score; only the learner's own "hand it back to the AI" removes it and queues a new grading.
"""
import json
from psycopg.types.json import Jsonb

from app import grading_recovery, memory, quiz_v2

MANUAL = "手动判分"
ADJUSTED = "已手动调整"


def criteria_of(q):
    """The scoring points to tick: the rubric; else the blanks; else the one 'correct' point."""
    if q.get("rubric"):
        return [dict(description=c["description"], points=c["points"]) for c in q["rubric"]]
    if q.get("blanks"):
        return [dict(description=f"第 {n} 空", points=b["points"]) for n, b in enumerate(q["blanks"], 1)]
    return [dict(description="答对", points=q["max_points"])]


def _answer(conn, uid, paper_id, question_id):
    quiz_v2.owned_paper(conn, uid, paper_id)
    row = conn.execute(
        "SELECT a.*,i.snapshot,t.id AS attempt FROM attempts_v2 t JOIN answers_v2 a ON a.attempt_id=t.id "
        "JOIN paper_items_v2 i ON i.paper_id=t.paper_id AND i.question_id=a.question_id WHERE t.paper_id=%s AND a.question_id=%s",
        (paper_id, question_id)).fetchone()
    if not row:
        raise LookupError("submitted_answer_not_found")
    return row


def _plain(answer):
    try:
        value = json.loads(answer)
    except (TypeError, ValueError):
        return str(answer or "")
    if not isinstance(value, dict):
        return str(value)
    return value.get("text") or "；".join(b for b in value.get("blanks", []) if b) or ("（手写图片）" if value.get("image_ids") else "")


def label(row, manual):
    if row["points"] is None:
        return None
    if manual and manual["points"] is not None:
        return ADJUSTED if manual["ai_points"] is not None else MANUAL
    return None


def view(conn, uid, paper_id, question_id):
    row = _answer(conn, uid, paper_id, question_id)
    q = row["snapshot"]
    manual = conn.execute("SELECT * FROM manual_grades WHERE attempt_id=%s AND question_id=%s", (row["attempt"], question_id)).fetchone()
    criteria = criteria_of(q)
    feedback = row["feedback"] or {}
    given = feedback.get("criterion_points")
    if given is None and row["points"] is not None and len(criteria) == 1:
        given = [row["points"]]
    reference = q.get("answer")
    options = q.get("options") or {}
    ai = None
    if manual and manual["ai_points"] is not None:
        ai = dict(points=manual["ai_points"], graded_by=manual["ai_graded_by"], comment=(manual["ai_feedback"] or {}).get("comment"),
                  criterion_points=(manual["ai_feedback"] or {}).get("criterion_points"))
    return dict(
        paper_id=paper_id, question_id=question_id, question_type=q["question_type"], stem=q["stem"], options=options,
        your_answer=_plain(row["answer"]),
        reference=f"{reference}. {options.get(reference, '')}" if q["question_type"] == "single_choice" and reference in options else reference,
        criteria=criteria, criterion_points=given, points=row["points"], max_points=q["max_points"],
        graded_by=row["graded_by"], label=label(row, manual), note=(manual or {}).get("note"),
        ai_original=ai, released=bool(manual and manual["released_at"] and manual["points"] is None),
    )


def _validate(q, criterion_points):
    criteria = criteria_of(q)
    if (not isinstance(criterion_points, list) or len(criterion_points) != len(criteria)
            or any(not quiz_v2.valid_points(p) or p > c["points"] for p, c in zip(criterion_points, criteria))):
        raise ValueError("scores_must_match_the_scoring_points")
    return criteria


def save(conn, uid, paper_id, question_id, criterion_points, note=None):
    row = _answer(conn, uid, paper_id, question_id)
    q = row["snapshot"]
    criteria = _validate(q, criterion_points)
    note = (note or "").strip()[:500] or None
    total = sum(criterion_points)
    existing = conn.execute("SELECT * FROM manual_grades WHERE attempt_id=%s AND question_id=%s", (row["attempt"], question_id)).fetchone()
    record = dict(
        manual=True, criterion_points=criterion_points, comment=note,
        criteria=[dict(description=c["description"], points=p, max_points=c["points"]) for p, c in zip(criterion_points, criteria)],
        criterion_reasons=[note or "手动判分"] * len(criteria),
    )
    if q.get("blanks") and not q.get("rubric"):
        record["blank_points"] = list(criterion_points)
    if row["points"] is None:
        original = None
        if q.get("rubric") and q["question_type"] != "fill_blank":
            quiz_v2.grade(conn, paper_id, question_id, criterion_points, note or "手动判分", MANUAL, details=dict(manual=True))
        else:
            _write(conn, row, total, record, MANUAL)
    else:
        original = (row["points"], row["feedback"], row["graded_by"])
        _write(conn, row, total, record, row["graded_by"] if row["graded_by"] in (MANUAL, ADJUSTED) else ADJUSTED)
    # the AI's / original score is kept the first time it is replaced
    keep = existing["ai_points"] if existing and existing["ai_points"] is not None else (original[0] if original and original[2] not in (MANUAL, ADJUSTED) else None)
    keep_fb = existing["ai_feedback"] if existing and existing["ai_points"] is not None else (original[1] if original and keep is not None else None)
    keep_by = existing["ai_graded_by"] if existing and existing["ai_points"] is not None else (original[2] if original and keep is not None else None)
    conn.execute(
        "INSERT INTO manual_grades(attempt_id,question_id,user_id,points,criterion_points,note,ai_points,ai_feedback,ai_graded_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (attempt_id,question_id) DO UPDATE SET points=EXCLUDED.points,criterion_points=EXCLUDED.criterion_points,note=EXCLUDED.note,"
        "ai_points=COALESCE(manual_grades.ai_points,EXCLUDED.ai_points),ai_feedback=COALESCE(manual_grades.ai_feedback,EXCLUDED.ai_feedback),"
        "ai_graded_by=COALESCE(manual_grades.ai_graded_by,EXCLUDED.ai_graded_by),released_at=NULL,updated_at=clock_timestamp()",
        (row["attempt"], question_id, uid, total, Jsonb(criterion_points), note, keep, Jsonb(keep_fb) if keep_fb is not None else None, keep_by),
    )
    conn.execute("INSERT INTO grading_events(attempt_id,question_id,reviewer,record) VALUES (%s,%s,%s,%s)", (row["attempt"], question_id, MANUAL, Jsonb(record)))
    memory.sync_score_card(conn, uid, row["attempt"], question_id)
    return view(conn, uid, paper_id, question_id)


def _write(conn, row, total, record, grader):
    conn.execute("SELECT set_config('app.manual_grade','1',true)")
    try:
        conn.execute("UPDATE answers_v2 SET points=%s,feedback=%s,graded_by=%s WHERE attempt_id=%s AND question_id=%s",
                     (total, Jsonb(record), grader, row["attempt"], row["question_id"]))
    finally:
        conn.execute("SELECT set_config('app.manual_grade','',true)")


def hand_to_ai(conn, uid, paper_id, question_id):
    """The learner takes the manual score away and lets the AI grade the answer again (a new grading task)."""
    row = _answer(conn, uid, paper_id, question_id)
    manual = conn.execute("SELECT * FROM manual_grades WHERE attempt_id=%s AND question_id=%s", (row["attempt"], question_id)).fetchone()
    if not manual or manual["points"] is None:
        raise ValueError("no_manual_score_to_hand_back")
    conn.execute("SELECT set_config('app.manual_grade','1',true)")
    try:
        conn.execute("UPDATE answers_v2 SET points=NULL,feedback=NULL,graded_by=NULL WHERE attempt_id=%s AND question_id=%s", (row["attempt"], question_id))
    finally:
        conn.execute("SELECT set_config('app.manual_grade','',true)")
    conn.execute("UPDATE manual_grades SET points=NULL,released_at=clock_timestamp(),updated_at=clock_timestamp() WHERE attempt_id=%s AND question_id=%s", (row["attempt"], question_id))
    return grading_recovery.requeue(conn, uid, paper_id=paper_id, question_id=question_id, final_too=True)
