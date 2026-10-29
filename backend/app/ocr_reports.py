"""Separate file/index/human/scientific/publication coverage; no inferred original exams."""

from collections import Counter, defaultdict
from pathlib import Path
import re
from app.source_files import sha, source_index_sha
from app.source_policy import COLLECTION, FILES, current_source_sql


def document_report(conn, document_id):
    from app.ocr_sources import prepare, review_snapshot

    doc = conn.execute(
        "SELECT * FROM source_documents WHERE id=%s", (document_id,)
    ).fetchone()
    version = conn.execute(
        "SELECT * FROM source_versions WHERE document_id=%s", (document_id,)
    ).fetchone()
    if not doc or not version:
        raise ValueError("OCR-only source version not found")
    rows = conn.execute(
        "SELECT * FROM source_units WHERE document_id=%s ORDER BY ordinal",
        (document_id,),
    ).fetchall()
    pages = conn.execute(
        "SELECT * FROM source_pages WHERE document_id=%s ORDER BY ocr_global_page",
        (document_id,),
    ).fetchall()
    issues = []
    if (
        sha(bytes(version["source_content"])) != version["source_sha256"]
        or version["source_sha256"] != doc["sha256"]
    ):
        issues.append({"issue": "ocr_file_checksum_mismatch"})
    try:
        parsed = prepare(
            Path(FILES[doc["file_id"]]["filename"]),
            doc["metadata"],
            raw=bytes(version["source_content"]),
        )
        if (
            parsed["metadata"]["source_index_sha256"]
            != doc["metadata"]["source_index_sha256"]
            or source_index_sha(rows, pages) != doc["metadata"]["source_index_sha256"]
        ):
            issues.append({"issue": "stored_text_or_index_checksum_mismatch"})
        if [
            (p["ocr_global_page"], p["marker_text"], p["marker_label"]) for p in pages
        ] != [
            (p["ocr_global_page"], p["marker_text"], p["marker_label"])
            for p in parsed["pages"]
        ]:
            issues.append({"issue": "ocr_page_marker_mismatch"})
    except (ValueError, KeyError) as exc:
        issues.append({"issue": str(exc)})
    chapters = defaultdict(
        lambda: dict(
            indexed_units=0,
            text_reviewed_units=0,
            science_reviewed_units=0,
            uncertainty_pending_units=0,
            pages=set(),
        )
    )
    tasks = []
    reviews = review_snapshot(conn, rows)
    link_counts = {
        r["unit_id"]: r["n"]
        for r in conn.execute(
            "SELECT unit_id,COUNT(*) AS n FROM knowledge_links WHERE unit_id=ANY(%s) GROUP BY unit_id",
            ([r["id"] for r in rows],),
        ).fetchall()
    }
    for row in rows:
        group = chapters[row["chapter"]]
        group["indexed_units"] += 1
        if row["ocr_global_page"]:
            group["pages"].add(row["ocr_global_page"])
        text = reviews[row["id"]].get("text", False)
        science = reviews[row["id"]].get("science", False)
        group["text_reviewed_units"] += int(text)
        group["science_reviewed_units"] += int(science)
        unresolved = bool(row["quality_flags"]) and not reviews[row["id"]].get(
            "uncertainty", False
        )
        group["uncertainty_pending_units"] += int(unresolved)
        tasks.append(
            {
                "unit_id": row["id"],
                "text_pending": not text,
                "science_pending": not science,
                "uncertainty_pending": unresolved,
                "knowledge_links": link_counts.get(row["id"], 0),
            }
        )
    for group in chapters.values():
        group["pages"] = sorted(group["pages"])
        n = group["indexed_units"]
        group["text_review_coverage"] = group["text_reviewed_units"] / n
        group["science_review_coverage"] = group["science_reviewed_units"] / n
    stages = dict(
        file_read=True,
        preflight_passed=not issues,
        database_imported=True,
        ocr_page_markers_complete=not issues
        and len(pages) == doc["metadata"].get("expected_pages")
        if doc["metadata"].get("source_kind") == "textbook"
        else None,
        text_reviewed_units=sum(g["text_reviewed_units"] for g in chapters.values()),
        science_reviewed_units=sum(
            g["science_reviewed_units"] for g in chapters.values()
        ),
    )
    return {
        "status": "integrity_issues"
        if issues
        else "ocr_integrity_checked_not_human_or_scientific_approval",
        "document_id": document_id,
        "file_id": doc["file_id"],
        "source_sha256": doc["sha256"],
        "version": version["collection_version"],
        "previous_document_id": version["previous_document_id"],
        "issues": issues,
        "stages": stages,
        "units": len(rows),
        "ocr_pages": len(pages),
        "pdf_physical_pages_verified": False,
        "printed_pages_verified": False,
        "chapters": dict(chapters),
        "review_tasks": tasks,
        "meaning": "index_read_coverage_is_not_human_scientific_review",
    }


def collection_status(conn):
    files = []
    for item in FILES.values():
        doc = conn.execute(
            "SELECT d.id,d.sha256 FROM source_documents d JOIN source_versions v ON v.document_id=d.id WHERE d.file_id=%s ORDER BY d.id DESC LIMIT 1",
            (item["file_id"],),
        ).fetchone()
        state = {
            "key": item["key"],
            "file_id": item["file_id"],
            "user_reported_located": True,
            "codex_file_read": bool(doc),
            "preflight_passed": False,
            "database_imported": bool(doc),
            "human_text_review_complete": False,
            "science_review_complete": False,
            "published_questions": 0,
        }
        if doc:
            report = document_report(conn, doc["id"])
            state["preflight_passed"] = not report["issues"]
            state["human_text_review_complete"] = (
                report["stages"]["text_reviewed_units"] == report["units"]
            )
            state["science_review_complete"] = (
                report["stages"]["science_reviewed_units"] == report["units"]
            )
            state["document_id"] = doc["id"]
            state["source_sha256"] = doc["sha256"]
            state["published_questions"] = conn.execute(
                "SELECT COUNT(DISTINCT q.id) AS n FROM reviewed_questions q CROSS JOIN LATERAL jsonb_array_elements(q.content->'evidence') e JOIN source_units u ON u.id=(e->>'source_unit_id')::bigint WHERE q.status='published' AND u.document_id=%s",
                (doc["id"],),
            ).fetchone()["n"]
        files.append(state)
    return {
        "collection_version": COLLECTION["version"],
        "folder_id": COLLECTION["folder_id"],
        "expected_textbook_markers": 1915,
        "files": files,
        "counts": {
            key: sum(bool(f[key]) for f in files)
            for key in (
                "user_reported_located",
                "codex_file_read",
                "preflight_passed",
                "database_imported",
                "human_text_review_complete",
                "science_review_complete",
            )
        },
        "published_questions": conn.execute(
            f"SELECT COUNT(DISTINCT q.id) AS n FROM reviewed_questions q CROSS JOIN LATERAL jsonb_array_elements(q.content->'evidence') e JOIN source_units u ON u.id=(e->>'source_unit_id')::bigint JOIN source_documents d ON d.id=u.document_id JOIN source_versions v ON v.document_id=d.id WHERE q.status='published' AND {current_source_sql()}"
        ).fetchone()["n"],
    }


def annual_report(conn):
    from app.exams import coverage

    grouped = defaultdict(list)
    rows = conn.execute(
        f"SELECT t.*,d.subject FROM exam_transcript_candidates t JOIN source_documents d ON d.id=t.document_id WHERE {current_source_sql()}"
    ).fetchall()
    for row in rows:
        grouped[(row["subject"], row["year"])].append(row)
    from app.exams import trusted_records, validate_classification

    now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    classifications = trusted_records(conn, now)
    output = []
    for year in coverage(conn):
        candidates = grouped[(year["subject"], year["year"])]
        questions = [r for r in candidates if r["question_number"]]
        mapped = 0
        year_records = [
            r
            for r in classifications
            if r["subject"] == year["subject"]
            and r["year"] == year["year"]
            and r["source_kind"] == "transcription"
        ]
        for record in year_records:
            if record["mapping_verified"]:
                try:
                    validate_classification(conn, record, record)
                except ValueError:
                    pass
                else:
                    mapped += 1
        output.append(
            {
                **year,
                "ocr_transcription_present": bool(candidates),
                "candidate_questions": len(questions),
                "candidate_question_types": dict(
                    Counter(r["question_type"] or "unknown" for r in questions)
                ),
                "unconfirmed_boundaries": sum(
                    "question_boundary_unconfirmed" in r["concerns"] for r in candidates
                ),
                "reported_transcription_gaps": sum(
                    "transcription_gap_reported" in r["concerns"] for r in candidates
                ),
                "question_completeness": "as_recorded_in_supplied_past_exam_file",
                "year_assignment_status": "explicit_heading_candidates_only"
                if candidates
                else "not_established",
                "reference_answer_markers_not_verified": sum(
                    bool(re.search("参考答案|答案[:：]|^答[：:]", r["transcription"]))
                    for r in candidates
                ),
                "textbook_answer_verification": "independently_checked_during_question_review",
                "transcription_scientific_mapping_verified": mapped,
                "unassigned_year_paragraphs": len(grouped[(year["subject"], None)]),
                "source_level": "user_provided_genuine_past_exam"
                if candidates
                else "not_established",
                "frequency_eligible_from_transcription": bool(questions),
                "priority_2023_2026": year["year"] >= 2023,
            }
        )
    return output
