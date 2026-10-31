"""Fail-closed, read-only release gate for the user's six pinned source bytes.

This is not a certificate of scientific correctness or AI grading. It compares
the database's current source audit with the *exact* locked private source
versions. Ordinary source-file IDs and self-consistent database hashes cannot
substitute for the owner's verified SHA-256 manifest.
"""
import argparse
import json
from pathlib import Path

from app.source_policy import COLLECTION
from app.source_intake import isolated_db, private_json
from app.source_readiness import audit

LOCK = Path(__file__).with_name("ocr_source_sha256_lock.json")
EXPECTED_MEDIA = {"biochemistry_exams": 27, "cell_biology_exams": 4}


def evaluate(db_audit, locked):
    if locked.get("collection_version") != COLLECTION["version"]:
        raise ValueError("Six-file lock version does not match active collection")
    expected_keys = {item["key"] for item in COLLECTION["files"]}
    if set(locked.get("files", {})) != expected_keys:
        raise ValueError("Six-file lock is incomplete or has extra source identities")

    database_files = {item["key"]: item for item in db_audit.get("files", [])}
    results = []
    for item in COLLECTION["files"]:
        key = item["key"]
        observed = database_files.get(key)
        pinned = locked["files"][key]
        if (
            key in EXPECTED_MEDIA
            and pinned.get("embedded_media_objects") != EXPECTED_MEDIA[key]
        ):
            raise ValueError("DOCX media counts missing or changed in SHA lock")
        issues = []
        if observed is None or observed.get("document_id") is None:
            issues.append("source_not_imported")
        else:
            if observed.get("source_sha256") != pinned["sha256"]:
                issues.append("not_the_locked_private_source_bytes")
            if not str(observed.get("status", "")).startswith("integrity_passed"):
                issues.append("database_source_integrity_failed")
            if item["kind"] == "textbook":
                if observed.get("ocr_page_markers") != item["expected_pages"]:
                    issues.append("ocr_page_marker_count_mismatch")
                if observed.get("missing_ocr_page_markers"):
                    issues.append("missing_ocr_page_markers")
                if observed.get("blank_ocr_pages"):
                    issues.append("blank_ocr_pages")
            elif observed.get("embedded_media_objects") != EXPECTED_MEDIA[key]:
                issues.append("unexpected_exam_docx_media_count")
        results.append({
            "key": key,
            "expected_sha256": pinned["sha256"],
            "observed_sha256": observed.get("source_sha256") if observed else None,
            "locked_byte_match": not any(x in issues for x in (
                "source_not_imported", "not_the_locked_private_source_bytes"
            )),
            "integrity_and_marker_passed": not issues,
            "issues": issues,
            "ocr_risk_flags": observed.get("risk_flag_counts", {}) if observed else {},
            "unresolved_exam_images": (
                observed.get("embedded_media_review_status")
                == "embedded_media_not_independently_transcribed"
            ) if observed else False,
        })
    return {
        "collection_version": COLLECTION["version"],
        "scope": "locked_byte_version_and_db_index_only_not_scientific_approval",
        "verified_locked_sources": sum(x["integrity_and_marker_passed"] for x in results),
        "all_six_locked_sources_integrity_passed": all(
            x["integrity_and_marker_passed"] for x in results
        ),
        "observed_ocr_markers": sum(
            (database_files.get(i["key"]) or {}).get("ocr_page_markers", 0)
            for i in COLLECTION["files"] if i["kind"] == "textbook"
        ),
        "known_docx_media_objects": sum(EXPECTED_MEDIA.values()),
        "image_transcription_pending": [
            x["key"] for x in results if x["unresolved_exam_images"]
        ],
        # No scientific review, question inventory, worker, or real exam
        # is proven by this report; it cannot authorize a deployment.
        "eligible_to_claim_120_verified_questions": False,
        "production_release_authorized": False,
        "files": results,
    }


def report(conn):
    locked = json.loads(LOCK.read_text(encoding="utf-8"))
    return evaluate(audit(conn), locked)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--target", choices=("development", "staging"), required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    with isolated_db(args.schema, args.target, readonly=True) as conn:
        result = report(conn)
    private_json(args.report, result)
    return 0 if result["all_six_locked_sources_integrity_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
