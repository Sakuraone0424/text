"""Short-lived leased automation tick, activated only with explicit API consent."""

import os
import sys
from app.db import get_db
from app import ai_jobs, auto_quality, model_router, model_compatibility, auto_grading
from app import auto_knowledge, auto_generation, auto_exams, auto_insights
from app.harness_gateway import execute
from app.worker import tick, integrations_tick


def dispatch_completed(conn):
    rows = conn.execute(
        "SELECT * FROM ai_jobs WHERE state='succeeded' AND dispatched_at IS NULL ORDER BY id LIMIT 50 FOR UPDATE SKIP LOCKED"
    ).fetchall()
    for job in rows:
        try:
            with conn.transaction():
                if job["kind"] in ("science_review", "answer_review"):
                    auto_quality.accept_round(conn, job)
                    phases = conn.execute(
                        "SELECT count(DISTINCT phase) AS n FROM auto_quality_events WHERE question_id=%s AND content_sha256=%s AND passed AND phase IN ('science','answer')",
                        (
                            job["payload"]["question_id"],
                            job["payload"]["content_sha256"],
                        ),
                    ).fetchone()["n"]
                    if phases == 2:
                        auto_quality.promote(conn, job["payload"]["question_id"])
                elif job["kind"] == "compatibility":
                    model_compatibility.accept(conn, job)
                elif job["kind"] == "repair":
                    qid = auto_quality.repair(
                        conn,
                        job["payload"]["question_id"],
                        job["result"]["response"]["question"],
                        job["result"]["response"]["reason"],
                    )
                    auto_quality.schedule_checks(conn, job["user_id"], qid)
                elif job["kind"] in ("textbook_analysis", "knowledge_extraction"):
                    auto_knowledge.accept(conn, job)
                elif job["kind"] == "generation":
                    new = auto_generation.accept(conn, job)
                    auto_quality.schedule_checks(conn, job["user_id"], new)
                elif job["kind"] == "exam_analysis":
                    auto_exams.accept(conn, job)
                elif job["kind"] == "grading":
                    auto_grading.apply(conn, job)
                elif job["kind"] in ("explanation", "study_plan"):
                    auto_insights.accept(conn, job)
                else:
                    raise ValueError("unsupported_dispatch_kind")
        except (ValueError, KeyError, LookupError):
            conn.execute(
                "UPDATE ai_jobs SET state='blocked',error_code='invalid_or_uncertain_result' WHERE id=%s",
                (job["id"],),
            )
            if job["kind"] in ("science_review", "answer_review"):
                conn.execute(
                    "UPDATE auto_question_states SET updated_at=clock_timestamp(),status='blocked',reason='invalid_or_uncertain_result' WHERE question_id=%s",
                    (job["payload"]["question_id"],),
                )
            elif job["kind"] == "repair":
                conn.execute(
                    "UPDATE auto_question_states SET updated_at=clock_timestamp(),status='blocked',reason='invalid_repair_result' WHERE question_id=%s",
                    (job["payload"]["question_id"],),
                )
            elif job["kind"] == "grading" and ":reassessment" not in job["job_key"]:
                payload = {
                    **job["payload"],
                    "instructions": "Independently reassess the original answer and rubric from supplied evidence, without using any previous score. Return the declared Grade schema, set uncertain=true if unresolved.",
                }
                ai_jobs.enqueue(
                    conn,
                    job["user_id"],
                    job["job_key"] + ":reassessment",
                    "grading",
                    payload,
                )
        conn.execute(
            "UPDATE ai_jobs SET dispatched_at=clock_timestamp() WHERE id=%s",
            (job["id"],),
        )


def plan(conn, uid):
    auto_quality.retire_stale(conn)
    for row in conn.execute(
        "SELECT p.question_id FROM candidate_review_packets p LEFT JOIN auto_question_states s ON s.question_id=p.question_id WHERE NOT EXISTS(SELECT 1 FROM candidate_revision_links l WHERE l.old_key=p.candidate_key) AND (s.question_id IS NULL OR s.status='candidate') ORDER BY p.question_id LIMIT 10"
    ).fetchall():
        auto_quality.schedule_checks(conn, uid, row["question_id"])
    for row in conn.execute(
        "SELECT question_id FROM auto_question_states WHERE status='blocked' AND revision_count<2 AND reason NOT IN ('unresolvable_ocr_or_image_dependency','source_file_integrity_failed') ORDER BY question_id LIMIT 3"
    ).fetchall():
        try:
            q, refs, style = auto_quality.evidence(conn, row["question_id"])
        except ValueError:
            continue
        payload = dict(
            question_id=row["question_id"],
            question=q,
            evidence=refs,
            style=style,
            instructions="Repair the question using only the supplied actual excerpts. Return JSON {question: complete Question object, reason: detailed correction rationale}. No invented citation, no human approval. A new version will undergo both independent rounds again.",
            schema=auto_quality.Question.model_json_schema(),
        )
        ai_jobs.enqueue(
            conn, uid, "repair:" + str(row["question_id"]), "repair", payload
        )
        conn.execute(
            "UPDATE auto_question_states SET updated_at=clock_timestamp(),status='repairing' WHERE question_id=%s AND EXISTS(SELECT 1 FROM ai_jobs j WHERE j.job_key=%s AND j.state IN ('pending','running','retrying'))",
            (row["question_id"], "repair:" + str(row["question_id"])),
        )
    for row in conn.execute(
        "SELECT DISTINCT p.id FROM papers_v2 p JOIN attempts_v2 t ON t.paper_id=p.id JOIN answers_v2 a ON a.attempt_id=t.id WHERE p.user_id=%s AND a.points IS NULL ORDER BY p.id LIMIT 10",
        (uid,),
    ).fetchall():
        auto_grading.schedule(conn, uid, row["id"])


def main():
    from app.scheduled_backup import tick as private_backup_tick

    private_backup_tick()  # Independent of model authorization or availability.
    if os.environ.get("AI_AUTOMATION_ENABLED") != "1":
        return
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").lower() == "production":
        raise PermissionError("Nonproduction environment required")
    if not os.environ.get("QUIZ_DB_SCHEMA", "").startswith("source_staging_"):
        raise PermissionError("Explicit isolated source schema required")
    model_router.authorized()
    tick()
    integrations_tick()  # Prepare at the scheduled tick before any long model task.
    private = os.environ.get("DEEPSEEK_PRIVATE_DATA_AUTHORIZED") == "1"
    with get_db() as conn:
        dispatch_completed(conn)
        users = conn.execute("SELECT * FROM ai_settings").fetchall()
        if (
            any(u["auto_discover"] for u in users)
            and model_router.catalog(conn)["stale"]
        ):
            model_router.refresh(conn)
        for user in users:
            model_compatibility.auto_upgrade(conn, user["user_id"])
            if (
                not user["stopped"]
                and private
                and os.environ.get("AI_QUEUE_AUTOPLAN") == "1"
            ):
                try:
                    with conn.transaction():
                        plan(conn, user["user_id"])
                        if os.environ.get("AI_FULL_CORPUS_ENABLED") == "1":
                            auto_knowledge.plan_small_batch(conn, user["user_id"])
                            auto_generation.plan(conn, user["user_id"])
                            auto_exams.plan(conn, user["user_id"])
                        auto_insights.plan(conn, user["user_id"])
                except ValueError:
                    pass  # Not admitted is not a success; job/probe status remains visible.
        # Retry budget/stopped jobs only when user explicitly enabled new attempts.
        conn.execute(
            "UPDATE ai_jobs j SET state='retrying' FROM ai_settings s WHERE s.user_id=j.user_id AND NOT s.stopped AND j.state='blocked' AND j.error_code IN ('budget_exceeded','automation_stopped')"
        )
    ai_jobs.run_once(get_db, execute)
    with get_db() as conn:
        dispatch_completed(conn)
    integrations_tick()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("AI automation failed (" + type(exc).__name__ + ")", file=sys.stderr)
        sys.exit(1)
