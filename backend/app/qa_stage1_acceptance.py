"""Strict, read-only stage-one QA acceptance of source/issue provenance.

A successful result means *every* flagged OCR risk, embedded DOCX media
object, and unmapped chapter page has a stable, source-locked ledger entry with
an explicit state. It does NOT mean the visual/scientific risks were resolved.
No source text or private ZIP content is emitted to logs.
"""
from collections import Counter
import json
import os

from app.exam_media_audit import audit_bytes
from app.qa_chapter_review import rows_from_report
from app.qa_post_import_audit import summarize as source_integrity
from app.qa_seed_pending_review import build_rows
from app.source_intake import SCHEMA, isolated_db
from app.source_policy import COLLECTION, pinned_source_bytes
from app.source_readiness import audit as source_audit

RISK_STATES = frozenset({
    "pending", "rejected_repair_required",
    "two_distinct_reviews_recorded_not_automatic_science_approval",
})
CHAPTER_STATES = frozenset({
    "pending", "reviewed_context_not_automatically_science_approved",
})


def reconcile(source, expected_risks, actual_risks, expected_chapters, actual_chapters):
    """Reject missing, duplicate, changed or untracked immutable QA ledger rows."""
    integrity = source_integrity(source)  # verifies 6 SHA/readbacks, pages, units and 31 media
    expected_risk_by_key = {row[0]: row for row in expected_risks}
    if len(expected_risk_by_key) != len(expected_risks):
        raise ValueError("qa_duplicate_expected_risk_fingerprint")
    observed = {}
    counts = Counter()
    for row in actual_risks:
        key = row["issue_key"]
        if key in observed:
            raise ValueError("qa_duplicate_stored_risk_fingerprint")
        if row["review_state"] not in RISK_STATES:
            raise ValueError("qa_risk_has_no_valid_tracking_state")
        observed[key] = (
            key, row["source_key"], row["source_sha256"],
            row["source_unit_id"], row["source_ocr_page"], row["text_sha256"],
            row["media_member"], row["media_sha256"], row["risk_flag"],
        )
        counts[row["review_state"]] += 1
    if expected_risk_by_key != observed:
        raise ValueError("qa_source_risk_ledger_provenance_or_coverage_mismatch")

    expected_chapter_by_key = {row[0]: row for row in expected_chapters}
    if len(expected_chapter_by_key) != len(expected_chapters):
        raise ValueError("qa_duplicate_expected_chapter_fingerprint")
    chapters = {}
    chapter_counts = Counter()
    for row in actual_chapters:
        key = row["issue_key"]
        if key in chapters:
            raise ValueError("qa_duplicate_stored_chapter_fingerprint")
        if row["review_state"] not in CHAPTER_STATES:
            raise ValueError("qa_chapter_has_no_valid_tracking_state")
        chapters[key] = (
            key, row["source_key"], row["source_sha256"],
            row["ocr_page"], row["provisional_class"],
        )
        chapter_counts[row["review_state"]] += 1
    if expected_chapter_by_key != chapters:
        raise ValueError("qa_chapter_ledger_provenance_or_coverage_mismatch")

    risk_count = len(expected_risks)
    media_count = sum(row[8] == "unverified_embedded_media" for row in expected_risks)
    if media_count != integrity["embedded_media_objects"]:
        raise ValueError("qa_all_original_media_not_tracked")
    if risk_count - media_count != integrity["risk_occurrences_total"]:
        raise ValueError("qa_all_ocr_risk_flags_not_tracked")
    if len(expected_chapters) != sum(
        doc.get("chapter_unmapped_ocr_page_count", 0)
        for doc in source["files"]
    ):
        raise ValueError("qa_unmapped_chapter_count_mismatch")
    return {
        "status": "stage1_six_sources_imported_all_flags_and_pages_traced",
        "source_integrity_postcommit_passed": True,
        "issue_provenance_and_states_accepted": True,
        "source_files": integrity["six_file_source_count"],
        "source_units": integrity["qa_source_text_unit_count"],
        "ocr_pages": integrity["ocr_page_markers"],
        "media_objects_tracked": media_count,
        "ocr_risk_occurrences_tracked": risk_count - media_count,
        "risk_ledger_total": risk_count,
        "risk_by_state": dict(sorted(counts.items())),
        "chapter_ledger_total": len(chapters),
        "chapter_by_state": dict(sorted(chapter_counts.items())),
        "scientific_or_visual_risks_resolved": False,
        "question_generation_authorized_by_stage1_alone": False,
        "private_source_material_logged": False,
    }


def inspect(conn):
    source = source_audit(conn)
    by_id = {item["file_id"]: item for item in COLLECTION["files"]}
    source_rows = conn.execute(
        "SELECT d.file_id,d.sha256 AS source_sha256,"
        "u.id AS unit_id,u.text,u.ocr_global_page AS ocr_page,u.quality_flags "
        "FROM source_units u JOIN source_documents d ON u.document_id=d.id "
        "WHERE u.quality_flags <> '[]'::jsonb ORDER BY d.file_id,u.id"
    ).fetchall()
    units = []
    for row in source_rows:
        item = by_id.get(row["file_id"])
        if not item or not pinned_source_bytes(row["file_id"], row["source_sha256"]):
            raise ValueError("qa_unpinned_risk_unit")
        units.append({**row, "source_key": item["key"]})
    media = []
    for item in COLLECTION["files"]:
        if item["kind"] != "exam_transcription":
            continue
        original = conn.execute(
            "SELECT d.sha256 AS source_sha256,v.source_content "
            "FROM source_versions v JOIN source_documents d ON v.document_id=d.id "
            "WHERE d.file_id=%s", (item["file_id"],),
        ).fetchone()
        if not original or not pinned_source_bytes(item["file_id"], original["source_sha256"]):
            raise ValueError("qa_original_exam_bytes_unavailable_or_unpinned")
        images = audit_bytes(bytes(original["source_content"]))
        if images["source_sha256"] != original["source_sha256"]:
            raise ValueError("qa_exam_media_bytes_sha_mismatch")
        media.append({
            "source_key": item["key"], "source_sha256": original["source_sha256"],
            "media": images["media"],
        })
    expected_risks, _ = build_rows(units, media)
    actual_risks = conn.execute(
        "SELECT i.issue_key,i.source_key,i.source_sha256,i.source_unit_id,"
        "i.source_ocr_page,i.text_sha256,i.media_member,i.media_sha256,"
        "i.risk_flag,p.review_state "
        "FROM qa_source_risk_items i JOIN qa_source_risk_progress p "
        "ON p.issue_key=i.issue_key"
    ).fetchall()
    expected_chapters = rows_from_report(source)
    actual_chapters = conn.execute(
        "SELECT i.issue_key,i.source_key,i.source_sha256,i.ocr_page,"
        "i.provisional_class,p.review_state "
        "FROM qa_chapter_mapping_items i JOIN qa_chapter_mapping_progress p "
        "ON p.issue_key=i.issue_key"
    ).fetchall()
    return reconcile(
        source, expected_risks, actual_risks, expected_chapters, actual_chapters
    )


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise SystemExit("qa_stage1_gate_refuses_non_qa_environment")
    if os.environ.get("QA_IMPORT_ENABLED") != "0":
        raise SystemExit("qa_stage1_gate_requires_upload_disabled")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_stage1_gate_requires_isolated_schema")
    with isolated_db(schema, "staging", readonly=True) as conn:
        report = inspect(conn)
    print("QA_STAGE1_TRACEABILITY_GATE=" + json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
