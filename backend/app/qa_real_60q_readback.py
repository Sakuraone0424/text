"""Independent read-only QA proof of a REAL 60q / 150pt / 60min attempt.

A synthetic pytest can exercise the predicate, but it cannot populate the
independent QA acceptance count. The live path requires actual submitted
answers, completed evidence-bound AI subjective grades, and FSRS mistake
events; textbook question text never leaves the QA database or reaches logs.
"""
from collections import Counter
import json
import os

from app.source_intake import SCHEMA, isolated_db
from app.source_policy import pinned_science_evidence
from app.training_spec_v2 import (
    SPECIFICATION, SUBJECTS, COUNTS, TOTAL_QUESTIONS, TOTAL_POINTS,
    validate_blueprint, validate_items,
)


def _answered(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return False
    return bool(
        isinstance(value, dict)
        and (
            str(value.get("text") or "").strip()
            or any(str(part).strip() for part in (value.get("blanks") or []))
            or value.get("image_ids")
        )
    )


def assess_attempt(paper, rows, *, qualified_ids, fsrs_failure_keys,
                   verified_ai_grade_ids):
    """Prove the full event sequence; return only sanitized failure reason."""
    def failed(reason):
        return {"real_60q_end_to_end_verified": False, "reason": reason}

    if paper.get("blueprint", {}).get("specification") != SPECIFICATION:
        return failed("not_new_60q_specification")
    try:
        validate_blueprint(paper["blueprint"])
    except (KeyError, ValueError, TypeError):
        return failed("wrong_60q_blueprint")
    if (
        paper.get("attempt_id") is None
        or paper.get("attempt_submitted_at") is None
        or paper.get("finalized_at") is None
        or paper.get("session_submitted_at") is None
        or paper.get("started_at") is None
        or paper.get("deadline") is None
    ):
        return failed("actual_session_or_finalized_submission_missing")
    if (paper["deadline"] - paper["started_at"]).total_seconds() != 3600:
        return failed("session_does_not_last_exactly_3600_seconds")
    if not (
        paper["started_at"] <= paper["attempt_submitted_at"] <= paper["deadline"]
        and paper["session_submitted_at"] == paper["attempt_submitted_at"]
        and paper["finalized_at"] >= paper["attempt_submitted_at"]
    ):
        return failed("attempt_submission_outside_server_clock_window")
    if len(rows) != TOTAL_QUESTIONS:
        return failed("not_exactly_60_persisted_question_answers")
    positions = [r["position"] for r in rows]
    ids = [r["question_id"] for r in rows]
    if positions != list(range(1, TOTAL_QUESTIONS + 1)) or len(set(ids)) != TOTAL_QUESTIONS:
        return failed("duplicate_or_missing_paper_item_positions")
    try:
        validate_items([r["snapshot"] for r in rows])
    except (KeyError, TypeError, ValueError, AttributeError):
        return failed("snapshot_scoring_or_subject_quota_mismatch")
    if any(qid not in qualified_ids for qid in ids):
        return failed("one_or_more_questions_not_independently_verified")
    expected_failures = set()
    independent_ai_subjective_grades = 0
    total = 0
    subjects = Counter()
    for row in rows:
        q = row["snapshot"]
        if not pinned_science_evidence(q):
            return failed("snapshot_textbook_source_not_locked")
        if not _answered(row.get("answer")):
            return failed("unanswered_questions_cannot_prove_full_grading_cycle")
        points = row.get("points")
        if type(points) is not int or not 0 <= points <= q["max_points"]:
            return failed("grading_not_complete_or_points_invalid")
        if q["question_type"] in ("term", "short_answer"):
            feedback = row.get("feedback")
            if (row.get("graded_by") != "AI自动核验"
                or not isinstance(feedback, dict)
                or not feedback.get("ai_job_id")
                or not feedback.get("model_id")
                or row["question_id"] not in verified_ai_grade_ids):
                return failed("independent_ai_subjective_grade_provenance_missing")
            independent_ai_subjective_grades += 1
        if points < q["max_points"]:
            expected_failures.add(
                f"graded-failure:{paper['attempt_id']}:{row['question_id']}"
            )
        subjects[q["subject"]] += q["max_points"]
        total += points
    # At least one real mistake is needed to prove FSRS mistake->review wiring.
    if not expected_failures:
        return failed("no_real_mistake_to_verify_fsrs_remediation")
    if not expected_failures.issubset(fsrs_failure_keys):
        return failed("graded_mistake_missing_fsrs_review_event")
    if (subjects != Counter({subj: 75 for subj in SUBJECTS})
        or independent_ai_subjective_grades != 10
        or not 0 <= total <= TOTAL_POINTS):
        return failed("wrong_subject_points_or_subjective_ai_grade_count")
    return {
        "real_60q_end_to_end_verified": True,
        "status": "real_full_60q_150pt_60min_evidence_grading_fsrs_readback_verified",
        "questions": TOTAL_QUESTIONS,
        "max_points": TOTAL_POINTS,
        "duration_seconds": 3600,
        "independently_model_graded_subjective_questions": independent_ai_subjective_grades,
        "confirmed_fsrs_mistake_events": len(expected_failures),
        "source_text_logged": False,
    }


def inspect(conn):
    papers = conn.execute(
        "SELECT p.id,p.user_id,p.blueprint,t.id AS attempt_id,"
        "t.submitted_at AS attempt_submitted_at,t.finalized_at,"
        "s.started_at,s.deadline,s.submitted_at AS session_submitted_at "
        "FROM papers_v2 p LEFT JOIN attempts_v2 t ON t.paper_id=p.id "
        "LEFT JOIN training_sessions s ON s.paper_id=p.id "
        "WHERE p.blueprint->>'specification'=%s ORDER BY p.id DESC LIMIT 20",
        (SPECIFICATION,),
    ).fetchall()
    failures = Counter()
    verified = 0
    for paper in papers:
        rows = conn.execute(
            "SELECT i.position,i.question_id,i.snapshot,a.answer,"
            "a.points,a.feedback,a.graded_by "
            "FROM paper_items_v2 i LEFT JOIN answers_v2 a "
            "ON a.attempt_id=%s AND a.question_id=i.question_id "
            "WHERE i.paper_id=%s ORDER BY i.position",
            (paper["attempt_id"], paper["id"]),
        ).fetchall()
        verified_ids = set()
        if len(rows) == TOTAL_QUESTIONS and paper["attempt_id"] is not None:
            from app.auto_quality import ready

            # This is the actual two-independent-real-model-event validator,
            # not a mere status label or a synthetic pytest fixture.
            for item in rows:
                qid = item["question_id"]
                published = conn.execute(
                    "SELECT status FROM reviewed_questions WHERE id=%s",
                    (qid,),
                ).fetchone()
                if published and published["status"] == "published" and ready(conn, qid):
                    verified_ids.add(qid)
        fsrs_keys = set()
        if paper["attempt_id"] is not None:
            recorded = conn.execute(
                "SELECT event_key FROM fsrs_events "
                "WHERE user_id=%s AND event_key LIKE %s",
                (paper["user_id"], f"graded-failure:{paper['attempt_id']}:%"),
            ).fetchall()
            fsrs_keys = {r["event_key"] for r in recorded}
        verified_ai_grade_ids = set()
        if paper["attempt_id"] is not None:
            from app.ai_jobs import content_sha
            for item in rows:
                snapshot = item["snapshot"]
                if snapshot["question_type"] not in ("term", "short_answer"):
                    continue
                feedback = item.get("feedback")
                if not isinstance(feedback, dict) or not feedback.get("ai_job_id"):
                    continue
                job = conn.execute(
                    "SELECT state,kind,result,payload,settings_snapshot "
                    "FROM ai_jobs WHERE id=%s",
                    (feedback["ai_job_id"],),
                ).fetchone()
                try:
                    current_answer_sha = content_sha(
                        json.loads(item["answer"])
                        if isinstance(item["answer"], str) else item["answer"]
                    )
                except (TypeError, ValueError):
                    current_answer_sha = None
                if (job and job["kind"] == "grading"
                    and job["state"] == "succeeded"
                    and job["result"].get("real_api") is True
                    and job["payload"].get("paper_id") == paper["id"]
                    and job["payload"].get("attempt_id") == paper["attempt_id"]
                    and job["payload"].get("question_id") == item["question_id"]
                    and job["payload"].get("answer_sha256") == current_answer_sha
                    and job["payload"].get("question") == snapshot
                    and job["settings_snapshot"].get("model_id") == feedback.get("model_id")):
                    verified_ai_grade_ids.add(item["question_id"])
        result = assess_attempt(
            paper, rows, qualified_ids=verified_ids, fsrs_failure_keys=fsrs_keys,
            verified_ai_grade_ids=verified_ai_grade_ids,
        )
        if result["real_60q_end_to_end_verified"]:
            verified += 1
        else:
            failures[result["reason"]] += 1
    return {
        "status": "read_only_real_qa_training_evidence_not_synthetic",
        "v2_papers_inspected": len(papers),
        "inspection_limit": 20,
        "real_60q_end_to_end_verification_count": verified,
        "real_qualified_60_question_end_to_end_completed": verified > 0,
        "rejected_real_papers_by_reason": dict(sorted(failures.items())),
        "history_or_grade_tables_mutated": False,
        "private_answers_or_question_text_logged": False,
    }


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise SystemExit("qa_real_exam_gate_refuses_non_qa")
    if os.environ.get("QA_IMPORT_ENABLED") != "0":
        raise SystemExit("qa_real_exam_gate_requires_upload_disabled")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_real_exam_gate_requires_isolated_schema")
    with isolated_db(schema, "staging", readonly=True) as conn:
        report = inspect(conn)
    print("QA_REAL_60Q_FULL_CYCLE_GATE=" + json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
