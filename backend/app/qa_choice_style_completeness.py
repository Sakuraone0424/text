"""QA-only complete-choice style inventory from source-locked genuine DOCX rows.

A parser-safe question START is not necessarily a complete single-choice
example. A-D may be stored in later Word paragraphs or omitted by OCR.
No private question wording, choices, or solutions are emitted in this report.
"""
import json
import os

from app.auto_generation import complete_choice_style_unit_ids
from app.source_intake import SCHEMA, isolated_db
from app.source_policy import current_source_sql, pinned_source_bytes
from app.transcript_analysis import STYLE_SOURCE_BLOCKERS
from app.exam_style_readiness import BOUNDARY_ISSUES
from app.training_spec_v2 import SUBJECTS
from app.source_policy import COLLECTION


def inventory(rows, load_continuation):
    results = {
        subject: {
            "parser_safe_starts": 0,
            "complete_A_to_D_choice_examples": 0,
            "incomplete_or_unreadable_options": 0,
            "blocked_before_option_check": 0,
        }
        for subject in SUBJECTS
    }
    # Exactly the 52 owner-supplied subject/year transcription slots; a
    # missing or unreadable question contributes zero, never a synthetic item.
    by_year = {
        (item["subject"], year): {
            "parser_safe_starts": 0, "complete_A_to_D_choice_examples": 0,
            "incomplete_or_unreadable_options": 0,
            "blocked_before_option_check": 0,
        }
        for item in COLLECTION["files"] if item["kind"] == "exam_transcription"
        for year in range(item["year_start"], item["year_end"] + 1)
    }
    missing_year = {subject: 0 for subject in SUBJECTS}
    for row in rows:
        subject = row["subject"]
        if subject not in results:
            raise ValueError("qa_invalid_choice_subject")
        if not pinned_source_bytes(row["file_id"], row["source_sha256"]):
            raise ValueError("qa_choice_style_not_source_locked")
        slot = by_year.get((subject, row["year"]))
        if slot is None:
            # Retain these in the subject-level blocked count for reconciliation.
            missing_year[subject] += 1
        risks = set(row["concerns"] or []) | set(row["quality_flags"] or [])
        if not row["year"] or risks & (STYLE_SOURCE_BLOCKERS | BOUNDARY_ISSUES):
            results[subject]["blocked_before_option_check"] += 1
            if slot is not None:
                slot["blocked_before_option_check"] += 1
            continue
        results[subject]["parser_safe_starts"] += 1
        if slot is not None:
            slot["parser_safe_starts"] += 1
        chunks = load_continuation(row["document_id"], row["ordinal"])
        parts = complete_choice_style_unit_ids(chunks)
        if parts and parts[0] == row["unit_id"]:
            results[subject]["complete_A_to_D_choice_examples"] += 1
            if slot is not None:
                slot["complete_A_to_D_choice_examples"] += 1
        else:
            results[subject]["incomplete_or_unreadable_options"] += 1
            if slot is not None:
                slot["incomplete_or_unreadable_options"] += 1
    return {
        "status": "real_exam_single_choice_full_option_inventory_candidate_only",
        "by_subject": results,
        "unassigned_or_outside_year_choice_starts": missing_year,
        "by_source_year": [
            {"subject": subject, "year": year, **counts}
            for (subject, year), counts in sorted(by_year.items())
        ],
        "by_source_year_is_52_real_source_slots": len(by_year) == 52,
        "both_subjects_have_complete_choice_style_examples": all(
            item["complete_A_to_D_choice_examples"] > 0
            for item in results.values()
        ),
        "answer_correctness_or_science_approved": False,
        "textbook_or_exam_question_content_logged": False,
    }


def inspect(conn):
    rows = conn.execute(
        "SELECT t.unit_id,t.document_id,su.ordinal,d.file_id,"
        "d.sha256 AS source_sha256,d.subject,t.year,t.concerns,su.quality_flags "
        "FROM exam_transcript_candidates t "
        "JOIN source_documents d ON d.id=t.document_id "
        "JOIN source_units su ON su.id=t.unit_id "
        "WHERE " + current_source_sql("d", kind="exam_transcription")
        + " AND t.question_type='single_choice' "
        "AND t.question_number IS NOT NULL "
        "ORDER BY d.subject,t.year,t.unit_id"
    ).fetchall()

    def load(document_id, ordinal):
        return conn.execute(
            "SELECT id,text,quality_flags FROM source_units "
            "WHERE document_id=%s AND ordinal >= %s "
            "AND ordinal < %s + 8 ORDER BY ordinal LIMIT 8",
            (document_id, ordinal, ordinal),
        ).fetchall()

    return inventory(rows, load)


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise SystemExit("qa_choice_audit_refuses_non_qa")
    if os.environ.get("QA_IMPORT_ENABLED") != "0":
        raise SystemExit("qa_choice_audit_requires_upload_disabled")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_choice_audit_requires_isolated_schema")
    with isolated_db(schema, "staging", readonly=True) as conn:
        report = inspect(conn)
    print("QA_SINGLE_CHOICE_STYLE_COMPLETENESS=" + json.dumps(
        report, sort_keys=True, separators=(",", ":")
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
