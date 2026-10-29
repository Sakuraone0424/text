"""Safely ingest the six locked source files into an isolated PostgreSQL schema.

Default is read-only preflight. --write-isolated requires an existing, migrated
source_staging_* schema and SOURCE_STAGING_DATABASE_URL. It never deploys,
downloads from Drive, writes to public, publishes exam questions, or triggers AI.
Do not commit the six original copyrighted source files to Git.
"""

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path

from app import sources
from app.source_files import MAX_OCR_BYTES, read_bounded
from app.source_policy import COLLECTION, handoff
from app.source_readiness import audit
from app.source_intake import isolated_db, private_json


LOCK = Path(__file__).with_name("ocr_source_sha256_lock.json")


def preflight(directory, *, lock_path=LOCK):
    locked = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    if locked["collection_version"] != COLLECTION["version"]:
        raise ValueError("Locked hashes belong to a different six-source version")
    hand = handoff(directory)
    if len(hand["files"]) != 6 or len(locked["files"]) != 6:
        raise ValueError("Six source files and six locked hashes are required")
    loaded = []
    for item in hand["files"]:
        ident = item["key"]
        locked_item = locked["files"].get(ident)
        if not locked_item:
            raise ValueError("Missing locked source identity: " + ident)
        path = Path(item["local_path"])
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing or symlinked locked input: " + ident)
        data = read_bounded(path, MAX_OCR_BYTES)
        digest = sha256(data).hexdigest()
        if len(data) != locked_item["bytes"] or digest != locked_item["sha256"]:
            raise ValueError("Downloaded source changed from its locked SHA-256: " + ident)
        metadata = dict(item["metadata"], expected_sha256=digest)
        if item["kind"] == "textbook":
            from app.source_bibliography import from_locked_cover

            book = from_locked_cover(
                ident, digest, data[:16000].decode("utf-8", errors="ignore"),
                expected_sha256=locked_item["sha256"],
            )
            metadata["authors"] = book["authors"]
            metadata["edition"] = book["edition"]
            metadata["bibliography_validation"] = book["verification"]
        prepared = sources.prepare_source(path, metadata, raw=data)
        if prepared["sha256"] != digest:
            raise ValueError("Source preflight produced unexpected bytes: " + ident)
        if item["kind"] == "textbook":
            pages = [p["ocr_global_page"] for p in prepared["pages"]]
            if pages != list(range(1, item["expected_pages"] + 1)):
                raise ValueError("Missing or out-of-order OCR page markers: " + ident)
            if any(p["paragraph_count"] == 0 for p in prepared["pages"]):
                # Explicit blank original pages can be legitimate; retain and
                # flag for automated review rather than silently treating as text.
                raise ValueError("Blank OCR page requires explicit source version fix: " + ident)
        if not prepared["rows"]:
            raise ValueError("Missing source text units: " + ident)
        flags = Counter(f for r in prepared["rows"] for f in r["quality_flags"])
        loaded.append({
            "key": ident, "path": str(path), "metadata": metadata,
            "sha256": digest, "bytes": len(data),
            "ocr_page_markers": len(prepared["pages"]),
            "ocr_units": len(prepared["rows"]),
            "risk_flags": dict(flags),
            "source_kind": item["kind"],
        })
    if sum(item["ocr_page_markers"] for item in loaded) != 1915:
        raise ValueError("The four textbooks require exactly 1915 OCR page markers")
    return loaded


def safe_report(items):
    return {
        "files": [
            {
                k: value for k, value in item.items()
                if k not in ("path", "metadata")
            } for item in items
        ],
        "source_bytes": sum(item["bytes"] for item in items),
        "ocr_page_markers": sum(item["ocr_page_markers"] for item in items),
        "all_locked_sha256_verified": len(items) == 6,
        "scientific_accuracy_verified": False,
        "official_exam_original_verified": False,
    }


def import_isolated(items, schema, target, report_path):
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").lower() == "production":
        raise ValueError("Cannot import sources from a production Railway runtime")
    # This function never calls init() or migrations and never invents tables.
    # All six sources and the private report must commit in one transaction.
    with isolated_db(schema, target) as conn:
        results = []
        for item in items:
            document_id = sources.import_source(
                conn, item["path"], item["metadata"]
            )
            stored = conn.execute(
                "SELECT d.sha256,v.source_sha256,octet_length(v.source_content) AS byte_count "
                "FROM source_documents d JOIN source_versions v ON v.document_id=d.id "
                "WHERE d.id=%s",
                (document_id,),
            ).fetchone()
            if (
                not stored or stored["sha256"] != item["sha256"]
                or stored["source_sha256"] != item["sha256"]
                or stored["byte_count"] != item["bytes"]
            ):
                raise ValueError("Source database readback mismatch: " + item["key"])
            results.append({"key": item["key"], "document_id": document_id})
        db_report = audit(conn)
        if (
            db_report["imported_files"] != 6
            or db_report["integrity_passed_files"] != 6
            or not db_report["all_six_byte_integrity_checks_passed"]
            or any(x["source_sha256"] != next(
                i["sha256"] for i in items if i["key"] == x["key"]
            ) for x in db_report["files"])
        ):
            raise ValueError("The six-file database audit failed; transaction rolled back")
        final = {
            **safe_report(items), "status": "committed_to_isolated_schema",
            "schema": schema, "target": target, "imported": results,
            "database_integrity": {
                "imported_files": db_report["imported_files"],
                "integrity_passed_files": db_report["integrity_passed_files"],
                "scientific_accuracy_verified": False,
                "ocr_risks_present": any(
                    x["risk_flag_counts"] for x in db_report["files"]
                ),
            },
        }
        # Report persistence failure must not commit partial source imports.
        private_json(report_path, final)
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--write-isolated", action="store_true")
    parser.add_argument("--schema")
    parser.add_argument("--target", choices=("development", "staging"), default="staging")
    args = parser.parse_args(argv)
    if args.write_isolated and not args.schema:
        parser.error("--schema source_staging_* is required when writing")
    items = preflight(args.directory)
    if args.write_isolated:
        result = import_isolated(items, args.schema, args.target, args.report)
    else:
        result = {**safe_report(items), "status": "preflight_passed_no_database_write"}
        private_json(args.report, result)
    print(result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
