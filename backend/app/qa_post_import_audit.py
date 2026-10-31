"""QA-only independent post-import SQL reconciliation, sanitized for Railway logs.

Reads the locked six source documents from an existing isolated QA schema.
It never inserts, updates, prints source text, or publishes scientific verdicts.
Use on the one-time QA import service while uploads remain disabled:
    python -m app.qa_post_import_audit
"""
from collections import Counter
import json
import os

from app.source_intake import SCHEMA, isolated_db
from app.source_readiness import audit as source_audit

EXPECTED_FILES = {
    "biochemistry_upper": (654, 0),
    "biochemistry_lower": (587, 0),
    "biochemistry_notes": (289, 0),
    "cell_biology": (385, 0),
    "biochemistry_exams": (0, 27),
    "cell_biology_exams": (0, 4),
}
INDEPENDENT_OFFLINE_UNIT_COUNT = 12302


def summarize(raw):
    """Fail closed on mismatched identities, pages, images or source integrity."""
    docs = raw.get("files", [])
    if (
        len(docs) != 6
        or {doc.get("key") for doc in docs} != set(EXPECTED_FILES)
        or raw.get("imported_files") != 6
        or raw.get("integrity_passed_files") != 6
        or raw.get("all_six_byte_integrity_checks_passed") is not True
    ):
        raise ValueError("qa_six_source_sql_integrity_gate_failed")
    actual_pages, actual_images, actual_units = 0, 0, 0
    risks = Counter()
    per_source = []
    for doc in docs:
        key = doc["key"]
        expected_pages, expected_images = EXPECTED_FILES[key]
        if (
            not str(doc.get("status", "")).startswith("integrity_passed")
            or doc.get("issues")
            or doc.get("ocr_page_markers") != expected_pages
            or (doc.get("embedded_media_objects") or 0) != expected_images
            or doc.get("missing_ocr_page_markers")
            or doc.get("ocr_unit_count", 0) < 1
        ):
            raise ValueError("qa_source_sql_page_media_or_byte_gate_failed")
        if not isinstance(doc.get("source_sha256"), str) or len(doc["source_sha256"]) != 64:
            raise ValueError("qa_source_hash_missing")
        actual_pages += doc["ocr_page_markers"]
        actual_images += doc.get("embedded_media_objects") or 0
        actual_units += doc["ocr_unit_count"]
        counts = doc.get("risk_flag_counts") or {}
        for risk, count in counts.items():
            if not isinstance(count, int) or count < 0:
                raise ValueError("invalid_risk_counts")
            risks[risk] += count
        per_source.append({
            "key": key,
            "sha256": doc["source_sha256"],
            "ocr_pages": doc["ocr_page_markers"],
            "text_units": doc["ocr_unit_count"],
            "embedded_media": doc.get("embedded_media_objects") or 0,
            "risk_occurrences": sum(counts.values()),
            "chapter_unmapped_pages": doc.get("chapter_unmapped_ocr_page_count", 0),
            "chapter_scientifically_verified": False,
            "media_scientifically_verified": False,
        })
    if actual_pages != 1915 or actual_images != 31:
        raise ValueError("qa_six_source_expected_markers_or_media_mismatch")
    return {
        "status": "six_locked_sources_committed_verified_readonly_sql_audit",
        "qa_source_integrity_passed": True,
        "six_file_source_count": len(docs),
        "ocr_page_markers": actual_pages,
        "embedded_media_objects": actual_images,
        "qa_source_text_unit_count": actual_units,
        "independent_offline_text_unit_count": INDEPENDENT_OFFLINE_UNIT_COUNT,
        "parser_unit_count_parity": actual_units == INDEPENDENT_OFFLINE_UNIT_COUNT,
        "unit_count_parity_is_science_review": False,
        "risk_occurrences_by_flag": dict(sorted(risks.items())),
        "risk_occurrences_total": sum(risks.values()),
        "source_details": sorted(per_source, key=lambda x: x["key"]),
        "chapter_mapping_scientifically_verified": False,
        "media_content_scientifically_verified": False,
        "original_exam_questions_independently_authenticated": False,
        "two_round_accepted_question_reserve": 0,
        "source_text_or_answer_exported": False,
    }


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise SystemExit("qa_source_audit_refused_non_qa_environment")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_source_audit_requires_isolated_schema")
    with isolated_db(schema, "staging", readonly=True) as conn:
        result = summarize(source_audit(conn))
    print("QA_SIX_SOURCE_SQL_AUDIT=" + json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
