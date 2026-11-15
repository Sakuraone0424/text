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


def pending_reason(points, job, photo_pending, queue_allowed, queue_why):
    """Why an answer has no score yet. A pending answer is never a zero."""
    if points is not None:
        return {"pending_reason": None, "pending_detail": None}
    detail = None
    if photo_pending:
        reason = "photo_answers_not_supported"
    elif job is None:
        reason = "waiting_to_be_queued" if queue_allowed else queue_why
    elif job["state"] in ("pending", "retrying"):
        reason = "queued_for_ai_grading"
    elif job["state"] == "running":
        reason = "ai_grading_in_progress"
    elif job["state"] == "succeeded":
        reason = "ai_result_being_applied"
    elif job["error_code"] in ("budget_exceeded", "daily_budget_exceeded", "monthly_budget_exceeded"):
        reason = "ai_budget_exhausted"
    elif job["error_code"] == "automation_stopped":
        reason = "ai_grading_stopped"
    elif job["error_code"] == "answer_too_long_for_automatic_grading":
        reason = "answer_too_long_for_automatic_grading"
    elif job["error_code"] == "invalid_or_uncertain_result":
        # The first uncertain result is re-assessed once automatically; this is the final one.
        reason = "uncertain_after_second_assessment" if job["job_key"].endswith(":reassessment") else "reassessing"
        detail = job["detail"]
    else:
        reason = "ai_grading_failed"
    return {"pending_reason": reason, "pending_detail": detail}


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
        "SELECT id,state,error_code,attempts,started_at,finished_at,job_key,"
        "left(result #>> '{response,explanation}',300) AS detail,"
        "payload->>'question_id' AS question_id FROM ai_jobs "
        "WHERE user_id=%s AND kind='grading' AND payload->>'paper_id'=%s "
        "ORDER BY id DESC",
        (user_id, str(paper_id)),
    ).fetchall()
    recent = {}
    for job in jobs:
        recent.setdefault(str(job["question_id"]), job)
    allowed, why = auto_queue_allowed(conn, user_id)
    reported = {
        r["question_id"]
        for r in conn.execute("SELECT question_id FROM question_reports WHERE paper_id=%s", (paper_id,)).fetchall()
    }
    snapshots = {r["question_id"]: r["snapshot"] for r in conn.execute(
        "SELECT question_id,snapshot FROM paper_items_v2 WHERE paper_id=%s", (paper_id,)).fetchall()}
    questions = []
    pending = 0
    for row in rows:
        job = recent.get(str(row["question_id"]))
        if row["points"] is None and row["question_id"] not in reported:
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
        reason = (
            {"pending_reason": None, "pending_detail": None}
            if row["question_id"] in reported
            else pending_reason(row["points"], job, photo_pending, allowed, why)
        )
        if reason["pending_reason"]:
            from app import grading_recovery

            snapshot = (snapshots.get(row["question_id"]) or {})
            if job is None and not grading_recovery.has_rubric(snapshot):
                reason["pending_reason"] = "no_scoring_points"      # a past-exam entry without scoring points
            reason["pending_text"] = grading_recovery.reason_text(reason["pending_reason"])
        # No answer text, handwritten image bytes, private prompt or raw logs.
        questions.append({
            "position": row["position"],
            "question_id": row["question_id"],
            "question_type": row["question_type"],
            "excluded": row["question_id"] in reported,
            "state": "excluded" if row["question_id"] in reported else "graded" if row["points"] is not None else (
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
            **reason,
        })
    return {
        "paper_id": paper_id,
        "total": len(rows),
        "graded": len(rows) - pending - len(reported & {r["question_id"] for r in rows if r["points"] is None}),
        "pending": pending,
        "complete": pending == 0,
        "late": __import__("app.grading_recovery", fromlist=["late"]).late(conn, user_id, paper_id) if pending else dict(count=0, minutes=30),
        "auto_queue_available": allowed,
        "auto_queue_unavailable_reason": why,
        "jobs": {
            state: sum(j["state"] == state for j in recent.values())
            for state in ("pending", "retrying", "running", "succeeded", "blocked", "cancelled")
        },
        "questions": questions,
    }
