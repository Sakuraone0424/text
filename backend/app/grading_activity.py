"""Owner-scoped grading activity; read-only and not a substitute for actual grades.

Automatic grading is queued only under explicit private-AI authorization,
opt-in, configured model and budget. Status never implies a completed score.
"""

import os


def auto_queue_allowed(conn, user_id):
    if os.environ.get("QUIZ_AUTO_GRADE_ON_SUBMIT") != "1":
        return False, "submit_auto_grading_disabled"
    if os.environ.get("AI_AUTOMATION_ENABLED") != "1":
        return False, "ai_worker_disabled"
    if os.environ.get("DEEPSEEK_PRIVATE_DATA_AUTHORIZED") != "1":
        return False, "private_data_authorization_required"
    if os.environ.get("DEEPSEEK_API_AUTHORIZED") != "1":
        return False, "provider_authorization_required"
    row = conn.execute(
        "SELECT stopped FROM ai_settings WHERE user_id=%s", (user_id,)
    ).fetchone()
    if not row or row["stopped"]:
        return False, "owner_ai_settings_stopped"
    return True, None


def try_queue(conn, user_id, paper_id):
    """Commit answer first; enqueue idempotently, never make grading block submission."""
    try:
        # A failure rolls back *only* this savepoint; the submitted attempt and
        # scores are already written in the enclosing transaction.
        with conn.transaction():
            allowed, why = auto_queue_allowed(conn, user_id)
            if not allowed:
                return {"queued": False, "reason": why}
            from app import auto_grading
            jobs = auto_grading.schedule(conn, user_id, paper_id)
    except Exception:
        # Database, provider configuration and job-serialization failures must
        # never roll back an otherwise valid completed exam.
        return {"queued": False, "reason": "automatic_queue_unavailable"}
    return {"queued": bool(jobs), "jobs": len(jobs), "reason": None if jobs else "no_eligible_text_answers"}


def progress(conn, user_id, paper_id):
    owner = conn.execute(
        "SELECT p.id FROM papers_v2 p JOIN attempts_v2 t ON t.paper_id=p.id "
        "WHERE p.id=%s AND p.user_id=%s", (paper_id, user_id)
    ).fetchone()
    if not owner:
        raise LookupError("Submitted paper not found")
    rows = conn.execute(
        "SELECT a.question_id,a.points,a.graded_by,i.snapshot->>'question_type' AS question_type, "
        "i.position,a.answer FROM attempts_v2 t JOIN answers_v2 a ON a.attempt_id=t.id "
        "JOIN paper_items_v2 i ON i.paper_id=t.paper_id AND i.question_id=a.question_id "
        "WHERE t.paper_id=%s ORDER BY i.position",
        (paper_id,),
    ).fetchall()
    jobs = conn.execute(
        "SELECT id,state,error_code,attempts,started_at,finished_at,"
        "payload->>'question_id' AS question_id FROM ai_jobs "
        "WHERE user_id=%s AND kind='grading' AND payload->>'paper_id'=%s "
        "ORDER BY id DESC",
        (user_id, str(paper_id)),
    ).fetchall()
    recent = {}
    for job in jobs:
        recent.setdefault(str(job["question_id"]), job)
    allowed, why = auto_queue_allowed(conn, user_id)
    questions = []
    pending = 0
    for row in rows:
        job = recent.get(str(row["question_id"]))
        if row["points"] is None:
            pending += 1
        # Images remain pending until a VERIFIED vision pipeline has transcribed
        # and independently checked them. Never fabricate recognition or score.
        import json
        try:
            answer_data = json.loads(row["answer"])
        except (ValueError, TypeError):
            answer_data = {}
        photo_pending = bool(
            row["points"] is None and isinstance(answer_data, dict)
            and answer_data.get("image_ids") and job is None
        )
        # No answer text, handwritten image bytes, private prompt or raw logs.
        questions.append({
            "position": row["position"],
            "question_id": row["question_id"],
            "question_type": row["question_type"],
            "state": "graded" if row["points"] is not None else (
                (job["state"] if job else "vision_pipeline_unavailable" if photo_pending else "pending_automatic_queue")
            ),
            "points": row["points"],
            "reviewer_kind": (
                "AI" if row["graded_by"] == "AI自动核验"
                else "historical_or_rule" if row["graded_by"] else None
            ),
            "attempts": job["attempts"] if job else 0,
            "error_code": job["error_code"] if job else (
                "verified_handwriting_recognition_required" if photo_pending else None
            ),
        })
    return {
        "paper_id": paper_id,
        "total": len(rows),
        "graded": len(rows) - pending,
        "pending": pending,
        "complete": pending == 0,
        "auto_queue_available": allowed,
        "auto_queue_unavailable_reason": why,
        "jobs": {
            state: sum(j["state"] == state for j in recent.values())
            for state in ("pending", "retrying", "running", "succeeded", "blocked", "cancelled")
        },
        "questions": questions,
    }
