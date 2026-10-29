"""Private local-file preflight and explicit isolated development/staging intake.

Never downloads Drive files, invokes OCR, verifies evidence, or publishes questions.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import psycopg

from app import sources
from app.source_files import part_manifest, read_bounded, source_index_sha


SCHEMA = re.compile(r"source_staging_[a-z0-9_]{1,48}\Z")


def private_json(path, payload):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Fail on existing files/symlinks; never overwrite a source file or prior report.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")


def input_files(args):
    metadata = json.loads(read_bounded(args.metadata, 1024 * 1024).decode("utf-8-sig"))
    if not isinstance(metadata, dict):
        raise ValueError("Metadata must be an object")
    mode = args.mode or (
        "pdf-tools" if args.original or args.original_parts else "ocr-only"
    )
    if mode == "ocr-only":
        if args.original or args.original_parts:
            raise ValueError("OCR-only intake does not require or accept PDF files")
        metadata["source_mode"] = "ocr_only"
        from app.source_policy import entry

        entry(metadata, args.path)
        return metadata, None
    if (
        type(metadata.get("expected_pages")) is not int
        or not 1 <= metadata["expected_pages"] <= 4000
    ):
        raise ValueError(
            "Private intake requires an independently checked expected_pages"
        )
    mapping = metadata.get("physical_mapping")
    if mapping not in ("identity_confirmed", "explicit"):
        raise ValueError(
            "Confirm OCR-to-physical mapping explicitly; file counts alone do not establish correspondence"
        )
    if mapping == "explicit" and set(metadata.get("pdf_pages", {})) != set(
        map(str, range(1, metadata["expected_pages"] + 1))
    ):
        raise ValueError(
            "Explicit physical mapping must cover every whole-book OCR page"
        )
    parts = None
    if args.original_parts:
        manifest = Path(args.original_parts).resolve()
        parts = json.loads(read_bounded(manifest, 1024 * 1024).decode("utf-8-sig"))
        if not isinstance(parts, list):
            raise ValueError("Original-parts manifest must be a list")
        for part in parts:
            if not isinstance(part, dict) or "content" in part:
                raise ValueError(
                    "External manifests accept local paths, never inline content"
                )
            if part.get("path"):
                part["path"] = str(manifest.parent / part["path"])
    if not args.original and parts is None:
        raise ValueError(
            "Private intake requires actual original files, not file IDs alone"
        )
    return metadata, parts


def page_report(prepared):
    if prepared["metadata"].get("source_mode") == "ocr_only":
        return {
            "status": "ocr_preflight_passed_human_review_pending",
            "verified": False,
            "file_id": prepared["metadata"]["file_id"],
            "source_sha256": prepared["sha256"],
            "stages": {
                "file_read": True,
                "preflight_passed": True,
                "database_imported": False,
                "human_text_reviewed": False,
                "scientific_reviewed": False,
                "questions_published": False,
            },
            "ocr_page_markers": len(prepared["pages"]),
            "paragraphs": len(prepared["rows"]),
            "pages": prepared["pages"],
            "pdf_physical_pages_verified": False,
            "printed_pages_verified": False,
            "quality_flags": dict(
                Counter(f for r in prepared["rows"] for f in r["quality_flags"])
            ),
        }
    pages = prepared["pages"]
    total = prepared["metadata"].get("original_pdf_pages")
    mapped = {p["pdf_page"] for p in pages}
    years = Counter()
    for row in prepared["rows"]:
        for year in set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", row["text"])):
            years[year] += 1
    return {
        "status": "pending_human_review",
        "verified": False,
        "file_id": prepared["metadata"]["file_id"],
        "ocr_sha256": prepared["sha256"],
        "expected_ocr_pages": prepared["metadata"].get("expected_pages"),
        "observed_ocr_pages": len(pages),
        "paragraphs": len(prepared["rows"]),
        "unpaged_paragraphs": sum(r["ocr_page"] is None for r in prepared["rows"]),
        "text_characters": sum(len(r["text"]) for r in prepared["rows"]),
        "blank_ocr_pages": [
            p["ocr_global_page"] for p in pages if not p["text_characters"]
        ],
        "unknown_printed_pages": [
            p["pdf_page"] for p in pages if p["printed_page"] is None
        ],
        "original_identity_kind": "sha256_of_ordered_parts_manifest"
        if prepared["parts"]
        else "single_file_sha256",
        "original_identity": prepared["original_sha256"],
        "original_pdf_pages": total,
        "parts": part_manifest(prepared["parts"]),
        "unmapped_original_physical_pages": sorted(set(range(1, total + 1)) - mapped)
        if total
        else None,
        "quality_flags": dict(
            Counter(f for r in prepared["rows"] for f in r["quality_flags"])
        ),
        "year_mentions_not_verified_exam_coverage": dict(sorted(years.items())),
        "pages": pages,
    }


def _validate_staging_database_url(url):
    """Reject known production targets before opening a database connection.

    This is a fail-closed check for known aliases, not proof that an unlisted
    URL points to a physically independent QA cluster. The latter requires
    the separate QA environment and deployment acceptance checks.
    """
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() in (
        "production", "prod"
    ):
        raise ValueError("Source intake is forbidden in the production Railway runtime")

    from psycopg.conninfo import conninfo_to_dict

    def endpoint(dsn):
        try:
            values = conninfo_to_dict(dsn)
        except (psycopg.Error, ValueError) as exc:
            raise ValueError("Invalid database connection configuration") from None
        # A second database name or role on the same host:port is NOT an
        # isolated PostgreSQL cluster. Passwords and database names are
        # deliberately ignored for this comparison.
        host = (values.get("hostaddr") or values.get("host") or "localhost")
        port = str(values.get("port") or "5432")
        return (host.strip().lower(), port)

    target_endpoint = endpoint(url)
    for name in ("DATABASE_URL", "PRODUCTION_DATABASE_URL"):
        known_production_url = os.environ.get(name)
        if known_production_url and endpoint(known_production_url) == target_endpoint:
            raise ValueError(
                "Source intake target shares a PostgreSQL endpoint with " + name
            )


@contextmanager
def isolated_db(schema, target, *, create=False, readonly=False):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    if not SCHEMA.fullmatch(schema) or target not in ("development", "staging"):
        raise ValueError(
            "Choose development/staging and a source_staging_* schema; public is forbidden"
        )
    url = os.environ.get("SOURCE_STAGING_DATABASE_URL")
    if not url:
        raise ValueError(
            "SOURCE_STAGING_DATABASE_URL must explicitly identify the authorized nonproduction database"
        )
    _validate_staging_database_url(url)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        if readonly:
            conn.execute("SET TRANSACTION READ ONLY")
        if create:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        elif not conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)
        ).fetchone():
            raise ValueError("Isolated intake schema is not initialized")
        conn.execute(
            sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
        )
        yield conn


def database_report(conn, document_id):
    from app.source_files import map_originals

    doc = conn.execute(
        "SELECT id,file_id,title,edition,subject,sha256,original_sha256,metadata FROM source_documents WHERE id=%s",
        (document_id,),
    ).fetchone()
    if not doc:
        raise ValueError("Source document not found in the selected isolated schema")
    if doc["metadata"].get("source_mode") == "ocr_only":
        from app.ocr_reports import document_report

        return document_report(conn, document_id)
    rows = conn.execute(
        "SELECT * FROM source_units WHERE document_id=%s ORDER BY ordinal",
        (document_id,),
    ).fetchall()
    pages = conn.execute(
        "SELECT * FROM source_pages WHERE document_id=%s ORDER BY ocr_global_page",
        (document_id,),
    ).fetchall()
    parts = conn.execute(
        "SELECT part_number,file_id,sha256,byte_size,pdf_pages,page_offset FROM source_original_parts WHERE document_id=%s ORDER BY part_number",
        (document_id,),
    ).fetchall()
    original = conn.execute(
        "SELECT original_content FROM source_documents WHERE id=%s", (document_id,)
    ).fetchone()["original_content"]
    issues = []
    if (
        doc["metadata"].get("source_index_sha256")
        and source_index_sha(rows, pages) != doc["metadata"]["source_index_sha256"]
    ):
        issues.append({"issue": "stored_text_or_index_checksum_mismatch"})
    # Check the manifest and actual bytes for every part, including parts with no OCR text.
    actual_parts = conn.execute(
        "SELECT * FROM source_original_parts WHERE document_id=%s ORDER BY part_number",
        (document_id,),
    ).fetchall()
    for part in actual_parts:
        from app.source_files import sha, pdf_pages

        if (
            sha(bytes(part["content"])) != part["sha256"]
            or len(part["content"]) != part["byte_size"]
        ):
            issues.append(
                {
                    "part_number": part["part_number"],
                    "issue": "original_checksum_mismatch",
                }
            )
        elif pdf_pages(bytes(part["content"])) != part["pdf_pages"]:
            issues.append(
                {
                    "part_number": part["part_number"],
                    "issue": "original_page_count_mismatch",
                }
            )
    if doc["metadata"].get("original_kind") != "pdf_parts":
        from app.source_files import sha, pdf_pages

        if not original or sha(bytes(original)) != doc["original_sha256"]:
            issues.append({"issue": "original_checksum_mismatch"})
        elif (
            doc["metadata"].get("original_pdf_pages")
            and pdf_pages(bytes(original)) != doc["metadata"]["original_pdf_pages"]
        ):
            issues.append({"issue": "original_page_count_mismatch"})
    try:
        remapped = [dict(p) for p in pages]
        map_originals(remapped, parts, doc["metadata"].get("original_pdf_pages"))
        if any(
            (a["original_part"], a["original_page"])
            != (b["original_part"], b["original_page"])
            for a, b in zip(pages, remapped)
        ):
            issues.append({"issue": "stored_page_part_mapping_mismatch"})
    except ValueError as exc:
        issues.append({"issue": str(exc)})
    expected = doc["metadata"].get("expected_pages")
    if expected and {p["ocr_global_page"] for p in pages} != set(
        range(1, expected + 1)
    ):
        issues.append({"issue": "stored_page_coverage_mismatch"})
    by_page = {p["ocr_global_page"]: p for p in pages}
    for row in rows:
        identity = {
            **row,
            "metadata": doc["metadata"],
            "original_content": original,
            "original_sha256": doc["original_sha256"],
            "original_present": bool(original),
        }
        # Byte checks above cover every original once per document; checking
        # each paragraph must not repeatedly transfer/hash the entire textbook.
        for issue in sources.original_issues(conn, identity, verify_hash=False):
            issues.append({"unit_id": row["id"], "issue": issue})
        p = by_page.get(row["ocr_global_page"])
        if not p or any(
            row[k] != p[k]
            for k in (
                "volume",
                "ocr_page",
                "pdf_page",
                "printed_page",
                "original_part",
                "original_page",
            )
        ):
            issues.append({"unit_id": row["id"], "issue": "unit_page_mapping_mismatch"})
    links = conn.execute(
        "SELECT COUNT(*) AS n FROM knowledge_links k JOIN source_units u ON u.id=k.unit_id WHERE u.document_id=%s",
        (document_id,),
    ).fetchone()["n"]
    return {
        "status": "integrity_issues"
        if issues
        else "integrity_checked_not_content_verified",
        "document": doc,
        "pages": pages,
        "parts": parts,
        "issues": issues,
        "units": len(rows),
        "verified_units": sum(r["verified"] for r in rows),
        "knowledge_links": links,
        "unclassified_units": sum(r["chapter"] == "unclassified" for r in rows),
        "bibliography_missing": [
            k for k in ("authors", "edition", "title") if not doc["metadata"].get(k)
        ],
        "meaning": "integrity_and_coverage_only_not_scientific_or_exam_verification",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "import"):
        p = sub.add_parser(command)
        p.add_argument("path")
        p.add_argument("--metadata", required=True)
        p.add_argument("--mode", choices=("ocr-only", "pdf-tools"))
        choice = p.add_mutually_exclusive_group()
        choice.add_argument("--original")
        choice.add_argument("--original-parts")
        p.add_argument("--report", required=True)
    for command in ("init", "audit", "exam-coverage"):
        p = sub.add_parser(command)
        if command != "init":
            p.add_argument("--report", required=True)
        if command == "audit":
            p.add_argument("--document-id", required=True, type=int)
    for command in ("init", "import", "audit", "exam-coverage"):
        sub.choices[command].add_argument("--schema", required=True)
        sub.choices[command].add_argument(
            "--target", required=True, choices=("development", "staging")
        )
    for command in ("collection-manifest", "collection-check"):
        p = sub.add_parser(command)
        p.add_argument("--directory", required=True)
        p.add_argument("--report", required=True)
    p = sub.add_parser("collection-status")
    p.add_argument("--schema", required=True)
    p.add_argument("--target", required=True, choices=("development", "staging"))
    p.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ("collection-manifest", "collection-check"):
            from app.source_policy import handoff
            from app.source_files import sha, MAX_OCR_BYTES

            report = handoff(args.directory)
            if args.command == "collection-check":
                for item in report["files"]:
                    if not item["codex_located_local_file"]:
                        item["error"] = "current_file_not_handed_off"
                        continue
                    try:
                        raw = read_bounded(item["local_path"], MAX_OCR_BYTES)
                        item["file_read"] = True
                        item["source_sha256"] = sha(raw)
                        prepared = sources.prepare_source(
                            item["local_path"], item["metadata"], raw=raw
                        )
                        item["preflight_passed"] = True
                        item["preflight"] = page_report(prepared)
                    except (ValueError, OSError) as exc:
                        item["error"] = str(exc)
            report["counts"] = {
                key: sum(bool(item[key]) for item in report["files"])
                for key in (
                    "user_reported_located",
                    "codex_located_local_file",
                    "file_read",
                    "preflight_passed",
                    "database_imported",
                    "human_text_reviewed",
                    "scientific_reviewed",
                )
            }
            private_json(args.report, report)
            return (
                2
                if args.command == "collection-check"
                and report["counts"]["preflight_passed"] != 6
                else 0
            )
        if args.command == "collection-status":
            from app.ocr_reports import collection_status

            with isolated_db(args.schema, args.target, readonly=True) as conn:
                private_json(args.report, collection_status(conn))
            return 0
        if args.command == "init":
            from migrate import apply_migrations

            with isolated_db(args.schema, args.target, create=True) as conn:
                apply_migrations(conn)
            print(
                "New isolated schema initialized; no source verified or question published"
            )
            return 0
        if args.command in ("check", "import"):
            metadata, parts = input_files(args)
            prepared = sources.prepare_source(args.path, metadata, args.original, parts)
            report = page_report(prepared)
            if args.command == "import":
                # Report creation and import run inside one transaction. A report
                # write failure rolls back the import; reports never contain body text.
                with isolated_db(args.schema, args.target) as conn:
                    report["document_id"] = sources.import_source(
                        conn, args.path, metadata, args.original, parts
                    )
                    stored = conn.execute(
                        "SELECT sha256,original_sha256,metadata FROM source_documents WHERE id=%s",
                        (report["document_id"],),
                    ).fetchone()
                    if (
                        stored["sha256"] != prepared["sha256"]
                        or stored["original_sha256"] != prepared["original_sha256"]
                        or stored["metadata"] != prepared["metadata"]
                    ):
                        raise ValueError(
                            "Source files changed during import; retry from stable private files"
                        )
                    report["database_audit"] = database_report(
                        conn, report["document_id"]
                    )
                    if report["database_audit"]["issues"]:
                        raise ValueError(
                            "Imported source failed stored-data integrity audit"
                        )
                    private_json(args.report, report)
            else:
                private_json(args.report, report)
        else:
            with isolated_db(args.schema, args.target, readonly=True) as conn:
                if args.command == "audit":
                    report = database_report(conn, args.document_id)
                else:
                    from app.ocr_reports import annual_report

                    report = {
                        "meaning": "transcription_candidates_are_not_verified_original_exams",
                        "years": annual_report(conn),
                    }
                private_json(args.report, report)
        print("Private report written; source review and publication remain separate")
        return 2 if report.get("issues") else 0
    except (ValueError, OSError, psycopg.Error) as exc:
        # Do not echo paths, body text or credentials. Detailed parser diagnostics
        # are stored only in a private report when possible.
        if getattr(args, "report", None) and not Path(args.report).exists():
            try:
                private_json(
                    args.report,
                    {"status": "rejected", "verified": False, "error": str(exc)},
                )
            except OSError:
                pass  # A failed report write must not mask a rolled-back import.

        print(
            "Intake rejected; inspect the private report/configuration",
            file=__import__("sys").stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
