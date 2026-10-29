"""Read-only six-source manifest and OCR integrity audit.

This is a deterministic inventory, *not* a scientific accuracy certificate.
Missing or superseded source revisions are reported, never silently substituted.
The user-supplied past exam files are genuine exam sources. OCR checks only
cover extracted text, figures, page markers and byte integrity.
"""

from collections import Counter
from hashlib import sha256

from app.source_policy import COLLECTION, FILES, current_source_sql
from app.source_files import source_index_sha


def _integrity(document, version, units, pages, item):
    """Return machine-checkable defects for the active immutable revision."""
    problems = []
    if not version:
        return ["source_version_missing"]
    content = bytes(version["source_content"])
    digest = sha256(content).hexdigest()
    if digest != document["sha256"] or digest != version["source_sha256"]:
        problems.append("source_bytes_sha256_mismatch")
    meta = document["metadata"]
    if version["collection_version"] != COLLECTION["version"]:
        problems.append("collection_version_mismatch")
    if version["folder_id"] != COLLECTION["folder_id"]:
        problems.append("collection_folder_mismatch")
    if version["material_key"] != item["key"]:
        problems.append("source_key_mismatch")
    if meta.get("source_kind") != item["kind"] or document["subject"] != item["subject"]:
        problems.append("source_subject_or_kind_mismatch")
    if not units:
        problems.append("ocr_units_missing")
    if [u["ordinal"] for u in units] != list(range(1, len(units) + 1)):
        problems.append("ocr_unit_ordinals_not_contiguous")
    if any(
        not u["text"].strip() or not u["chapter"].strip()
        for u in units
    ):
        problems.append("empty_unit_or_chapter")
    if item["kind"] == "textbook":
        n = item["expected_pages"]
        seen = [p["ocr_global_page"] for p in pages]
        if seen != list(range(1, n + 1)):
            problems.append("missing_or_nonsequential_ocr_page_markers")
        if any(p["paragraph_count"] == 0 for p in pages):
            problems.append("ocr_blank_pages_present")
        if any(u["ocr_global_page"] is None or u["ocr_global_page"] not in seen for u in units):
            problems.append("unit_without_valid_ocr_page")
        if meta.get("expected_pages") != n:
            problems.append("declared_page_count_mismatch")
    elif item["kind"] == "exam_transcription":
        if pages:
            problems.append("docx_transcript_must_not_assert_textbook_ocr_pages")
    # The index hash protects coordinates, text and flags, not scientific truth.
    try:
        actual = source_index_sha(units, pages)
    except (KeyError, TypeError, ValueError):
        problems.append("ocr_index_fields_incomplete")
    else:
        if meta.get("source_index_sha256") != actual:
            problems.append("ocr_index_sha256_mismatch")
    return sorted(set(problems))


def audit(conn):
    """Six-file aggregate suitable for a learner-facing, owner-scoped API.

    Excludes raw excerpts, DOCX bytes, question answers and private paths.
    """
    rows = []
    for item in COLLECTION["files"]:
        document = conn.execute(
            "SELECT * FROM source_documents WHERE file_id=%s ORDER BY id DESC LIMIT 1",
            (item["file_id"],),
        ).fetchone()
        base = {
            "key": item["key"],
            "filename": item["filename"],
            "kind": item["kind"],
            "subject": item["subject"],
            "expected_ocr_pages": item.get("expected_pages"),
            "expected_year_range": (
                [item["year_start"], item["year_end"]]
                if item["kind"] == "exam_transcription" else None
            ),
            "document_id": document["id"] if document else None,
            "source_sha256": document["sha256"] if document else None,
            "ocr_unit_count": 0,
            "ocr_page_markers": 0,
            "blank_ocr_pages": [],
            "missing_ocr_page_markers": [],
            "risk_flag_counts": {},
            "mapped_chapter_count": 0,
            "exam_candidate_count": 0,
            "exam_candidate_years": [],
            "embedded_media_objects": None,
            "embedded_media_referenced_objects": None,
            "embedded_media_unanchored_objects": [],
            "embedded_media_formats": {},
            "embedded_media_review_status": "not_applicable",
            "printed_original_verified": False,
            "past_exam_source_status": (
                "user_designated_genuine_past_exam"
                if item["kind"] == "exam_transcription" else "not_applicable"
            ),
        }
        if document is None:
            rows.append({**base, "status": "not_imported", "issues": ["source_not_imported"]})
            continue
        version = conn.execute(
            "SELECT * FROM source_versions WHERE document_id=%s",
            (document["id"],),
        ).fetchone()
        units = conn.execute(
            "SELECT * FROM source_units WHERE document_id=%s ORDER BY ordinal",
            (document["id"],),
        ).fetchall()
        pages = conn.execute(
            "SELECT * FROM source_pages WHERE document_id=%s ORDER BY ocr_global_page",
            (document["id"],),
        ).fetchall()
        issues = _integrity(document, version, units, pages, item)
        # No claim of source completeness from a page count alone: OCR may omit
        # diagrams or have content errors even when every marker is present.
        flagged = Counter(flag for u in units for flag in (u["quality_flags"] or []))
        marked = {p["ocr_global_page"] for p in pages}
        missing = (
            sorted(set(range(1, item["expected_pages"] + 1)) - marked)
            if item["kind"] == "textbook" else []
        )
        candidates = []
        media_count = None
        media_referenced = None
        media_unanchored = []
        media_formats = {}
        media_state = "not_applicable"
        if item["kind"] == "exam_transcription" and version:
            candidates = conn.execute(
                "SELECT year FROM exam_transcript_candidates WHERE document_id=%s",
                (document["id"],),
            ).fetchall()
            from app.exam_media_audit import audit_bytes

            try:
                media_audit = audit_bytes(bytes(version["source_content"]))
            except ValueError:
                issues.append("invalid_stored_docx_archive_or_media_links")
                media_state = "docx_invalid"
            else:
                media_count = media_audit["media_objects"]
                media_referenced = media_audit["referenced_media_objects"]
                media_unanchored = media_audit["unreferenced_media_objects"]
                media_formats = media_audit["media_formats"]
                media_state = (
                    "embedded_media_not_independently_transcribed"
                    if media_count else "no_embedded_media"
                )
        rows.append({
            **base,
            "ocr_unit_count": len(units),
            "ocr_page_markers": len(pages),
            "blank_ocr_pages": [p["ocr_global_page"] for p in pages if p["paragraph_count"] == 0],
            "missing_ocr_page_markers": missing,
            "risk_flag_counts": dict(sorted(flagged.items())),
            "mapped_chapter_count": len({
                u["chapter"] for u in units if u["chapter"] not in ("unclassified", "unknown")
            }),
            "exam_candidate_count": len(candidates),
            "exam_candidate_years": sorted({r["year"] for r in candidates if r["year"] is not None}),
            "embedded_media_objects": media_count,
            "embedded_media_referenced_objects": media_referenced,
            "embedded_media_unanchored_objects": media_unanchored,
            "embedded_media_formats": media_formats,
            "embedded_media_review_status": media_state,
            "status": "integrity_passed_with_ocr_risks" if not issues and flagged else
                      "integrity_passed" if not issues else "integrity_failed",
            "issues": issues,
        })
    return {
        "collection_version": COLLECTION["version"],
        "expected_files": len(FILES),
        "imported_files": sum(r["document_id"] is not None for r in rows),
        "integrity_passed_files": sum(r["status"].startswith("integrity_passed") for r in rows),
        "all_six_byte_integrity_checks_passed": all(
            r["status"].startswith("integrity_passed") for r in rows
        ),
        "complete_for_generation": all(
            r["status"] == "integrity_passed" and
            r["embedded_media_review_status"] in ("not_applicable", "no_embedded_media")
            for r in rows
        ),
        "generation_risk_note": (
            "Hash/marker integrity does not remove per-unit scientific or figure "
            "uncertainties. Automatic generation must independently screen each "
            "source unit and never rely only on this collection-level boolean."
        ),
        "note": (
            "OCR hash, index and page-marker checks do not establish scientific "
            "correctness, original-image OCR accuracy, or an answer not in the supplied text."
        ),
        "files": rows,
    }
