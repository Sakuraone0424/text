"""Read-only QA verification of the source's 2017 unnumbered biochem lines.

This does not certify an official original exam paper or grading answer. The
original user-locked DOCX contains 6 unnumbered term lines and 4 short answers.
Numbering in question bank must remain NULL pending original boundary review.
"""
import json
import os
from app.source_intake import SCHEMA, isolated_db
from app.source_policy import COLLECTION, pinned_source_bytes
from app.offline_unnumbered_exam_review import review_candidates


def audit(conn):
    entry = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_exams")
    doc = conn.execute(
        "SELECT id,sha256 FROM source_documents WHERE file_id=%s",
        (entry["file_id"],),
    ).fetchone()
    if not doc or not pinned_source_bytes(entry["file_id"], doc["sha256"]):
        raise ValueError("QA 2017 exam original is not locked")
    rows = conn.execute(
        "SELECT id,ordinal,text,chapter,section,quality_flags "
        "FROM source_units WHERE document_id=%s ORDER BY ordinal",
        (doc["id"],),
    ).fetchall()
    details = review_candidates(
        rows, {"key":"biochemistry_exams","year_start":2004,"year_end":2026},
        source_sha256=doc["sha256"]
    )
    return {
        "status": details["status"],
        "verified_qa_source_sha256": details["source_sha256"],
        "year": 2017,
        "term_candidate_lines": details["term_candidate_lines"],
        "short_answer_candidate_lines": details["short_answer_candidate_lines"],
        "total_unnumbered_candidate_lines": details["distinct_source_units"],
        "all_original_question_numbers_missing": all(
            x["original_question_number"] is None
            for x in details["candidates"]
        ),
        "source_unit_ids": [x["unit_id"] for x in details["candidates"]],
        "official_original_review_completed": False,
        "scientific_answer_review_completed": False,
        "eligible_historical_style_anchors_from_these_lines": 0,
    }


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise SystemExit("qa_2017_review_refused_non_qa_environment")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_2017_review_requires_isolated_schema")
    with isolated_db(schema,"staging",readonly=True) as conn:
        summary = audit(conn)
    print("QA_2017_UNNUMBERED_EXAM_AUDIT=" + json.dumps(
        summary, ensure_ascii=False,sort_keys=True,separators=(",",":")
    ),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
