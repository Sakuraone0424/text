"""Short-lived leased automation tick, activated only with explicit API consent."""

import httpx
import os
import sys
from app.db import get_db
from app import ai_jobs, auto_quality, model_router, model_compatibility, auto_grading
from app import auto_knowledge, auto_generation, auto_exams, auto_insights
from app.harness_gateway import execute
from app.worker import tick, integrations_tick


_SAFE_VALIDATOR_ERRORS = {
    "Rubric knowledge tags must belong to the question": "rubric_tag_mismatch",
    "Rubric points must sum to max_points": "rubric_points_mismatch",
    "max_points must match a supported versioned training specification": "invalid_versioned_points",
    "Single choice requires A-D options and one answer key": "incomplete_choice_or_key",
    "Empty option": "empty_choice_option",
    "Options must be distinct": "duplicate_choice_options",
    "Only single choice accepts options": "unexpected_options",
    "Multi-blank questions use blanks only": "invalid_blank_representation",
    "Every blank needs a matching placeholder and points summing to max_points": "blank_count_or_points_mismatch",
    "Fill blank requires explicit nonempty accepted answers": "missing_accepted_answers",
    "Canonical answer must be accepted": "canonical_fill_answer_missing",
    "Fill blank requires exactly one ____ blank": "blank_marker_count",
    "accepted_answers is only for fill_blank": "unexpected_accepted_answers",
    "A valid ISBN-10 or ISBN-13 checksum is required": "invalid_isbn_checksum",
}
_SAFE_QUALITY_CODES = frozenset((
    "unknown_question_type", "unresolved_placeholder",
    "unavailable_figure_reference", "unhelpful_all_or_none_distractor",
    "missing_unique_answer_rationale", "missing_accepted_answer_set",
    "model_answer_too_terse_for_subjective_marking",
    "insufficient_independent_scoring_criteria",
))
_SAFE_PACKET_ERRORS = {
    "Textbook quote, subject or version identity mismatch": "evidence_identity_or_quote_mismatch",
    "Each rubric point needs explicit cited source analysis": "rubric_source_analysis_missing",
    "At least one located transcription reference required": "style_transcription_missing",
}


def safe_dispatch_failure(exc):
    """Diagnostic reason codes only; never include a model's generated prose."""
    import re
    from pydantic import ValidationError
    if isinstance(exc, ValidationError):
        errors = []
        for item in exc.errors(include_input=False, include_context=True)[:10]:
            reason = str((item.get("ctx") or {}).get("error", ""))
            entry = {"type": item["type"],
                     "field": str(item["loc"][0]) if item["loc"] else "root"}
            if reason in _SAFE_VALIDATOR_ERRORS:
                entry["reason"] = _SAFE_VALIDATOR_ERRORS[reason]
            errors.append(entry)
        return {"code": "output_schema_violation", "errors": errors}
    msg = str(exc)
    prefix = "generated_question_quality_gate_failed:"
    if msg.startswith(prefix):
        reasons = msg[len(prefix):].split(",")
        if reasons and len(reasons) <= 10 and all(x in _SAFE_QUALITY_CODES for x in reasons):
            return {"code": "generated_question_quality_gate_failed",
                    "reasons": reasons}
    if msg in _SAFE_PACKET_ERRORS:
        return {"code": _SAFE_PACKET_ERRORS[msg]}
    return {"code": msg if re.fullmatch(r"[a-z_]{1,100}", msg) else "invalid_result"}


def dispatch_completed(conn, scope_id=None, scope_kind="limited_trial"):
    if scope_kind not in ("limited_trial", "review_campaign", "qa_automation"):
        raise ValueError("unknown_execution_scope")
    scope_filter = (
        " AND settings_snapshot->'" + scope_kind + "'->>'scope_id'=%s" if scope_id else ""
    )
    rows = conn.execute(
        "SELECT * FROM ai_jobs WHERE state='succeeded' AND dispatched_at IS NULL"
        + scope_filter
        + " ORDER BY id LIMIT 50 FOR UPDATE SKIP LOCKED",
        (scope_id,) if scope_id else (),
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
        except (ValueError, KeyError, LookupError) as exc:
            if "qa_automation" in job["settings_snapshot"]:
                detail = safe_dispatch_failure(exc)
                conn.execute("UPDATE ai_jobs SET result=jsonb_set(COALESCE(result,'{}'::jsonb),"
                             "'{dispatch_failure}',%s) WHERE id=%s", (ai_jobs.Jsonb(detail),job["id"]))
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
    os.environ["QUIZ_SERVICE_ROLE"] = (
        "worker"  # Short jobs never own the API backup volume.
    )
    if os.environ.get("AI_AUTOMATION_ENABLED") != "1":
        return
    from app.deployment_target import verify_isolated_target

    verify_isolated_target()
    if not os.environ.get("QUIZ_DB_SCHEMA", "").startswith("source_staging_"):
        raise PermissionError("Explicit isolated source schema required")
    model_router.authorized()
    tick()
    integrations_tick()  # Prepare at the scheduled tick before any long model task.
    private = os.environ.get("DEEPSEEK_PRIVATE_DATA_AUTHORIZED") == "1"
    with get_db() as conn:
        dispatch_completed(conn)
        users = conn.execute("SELECT * FROM ai_settings").fetchall()
        # Public-page refresh is not a paid inference, never uploads private material.
        if (
            users
            and not conn.execute(
                "SELECT 1 FROM ai_cny_prices WHERE verified_at>clock_timestamp()-interval '12 hours' LIMIT 1"
            ).fetchone()
        ):
            try:
                from app.cny_prices import refresh

                with conn.transaction():
                    refresh(conn)
            except (ValueError, httpx.HTTPError):
                pass  # Missing/expired/conflicting prices block new calls at claim().
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
