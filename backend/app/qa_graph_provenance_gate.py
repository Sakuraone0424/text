"""Read-only proof that the QA textbook graph references exact locked OCR rows.

The existing graph seed records lexical facet *candidates*, never scientific
facts. This gate compares every byte-derived node coordinate and candidate
facet to source_units, rather than accepting matching aggregate counts alone.
"""
import json
import os

from app.qa_textbook_graph import BOOKS, prepare_rows
from app.source_intake import SCHEMA, isolated_db
from app.source_readiness import audit as source_audit


NODE_KEYS = (
    "unit_id", "source_key", "source_sha256", "source_text_sha256",
    "subject", "chapter", "ocr_page", "flagged_risks",
)


def reconcile(expected_nodes, expected_facets, actual_nodes, actual_facets):
    expected = {row[0]: tuple(row) for row in expected_nodes}
    if len(expected) != len(expected_nodes):
        raise ValueError("qa_duplicate_expected_textbook_units")
    found = {}
    for row in actual_nodes:
        key = row["unit_id"]
        if key in found:
            raise ValueError("qa_duplicate_textbook_evidence_nodes")
        if row["scientific_review_state"] != "pending":
            # At this stage the column is CHECK(pending); any future approved
            # state must come from separately audited scientific review events.
            raise ValueError("qa_graph_unexpected_science_approval_state")
        found[key] = tuple(row[k] for k in NODE_KEYS)
    if expected != found:
        raise ValueError("qa_graph_source_hash_or_location_mismatch")

    expected_pairs = set(expected_facets)
    if len(expected_pairs) != len(expected_facets):
        raise ValueError("qa_duplicate_expected_facets")
    actual_pairs = set()
    for row in actual_facets:
        pair = (row["unit_id"], row["facet"])
        if pair in actual_pairs:
            raise ValueError("qa_duplicate_stored_facets")
        if (row["reason"] != "literal_regex_not_science"
                or row["scientific_verified"] is not False):
            raise ValueError("qa_lexical_facet_misrepresented_as_science")
        actual_pairs.add(pair)
    if expected_pairs != actual_pairs:
        raise ValueError("qa_graph_facet_source_coverage_mismatch")
    if len(expected) != 9655 or len(expected_pairs) != 12896:
        raise ValueError("qa_original_textbook_graph_inventory_changed")
    return {
        "status": "textbook_graph_all_units_page_chapter_hash_facet_provenance_verified",
        "all_nodes_source_exact": True,
        "all_lexical_facets_source_exact": True,
        "evidence_nodes": len(expected),
        "lexical_facet_candidates": len(expected_pairs),
        "scientifically_approved_nodes_or_facets": 0,
        "full_scientific_knowledge_graph_completed": False,
        "original_text_or_source_quotes_logged": False,
    }


def inspect(conn):
    source = source_audit(conn)
    if (source["imported_files"], source["integrity_passed_files"]) != (6, 6):
        raise ValueError("qa_graph_requires_six_source_integrity")
    rows = conn.execute(
        "SELECT u.id AS unit_id,d.file_id,d.sha256 AS source_sha256,"
        "d.subject,u.chapter,u.text,u.quality_flags,"
        "u.ocr_global_page AS ocr_page "
        "FROM source_units u JOIN source_documents d ON d.id=u.document_id "
        "ORDER BY u.id"
    ).fetchall()
    if len(rows) != 12396:
        raise ValueError("qa_original_text_unit_count_drifted")
    expected_nodes, expected_facets, chapters = prepare_rows(
        [row for row in rows if row["file_id"] in BOOKS]
    )
    if len(chapters) != 83:
        raise ValueError("qa_original_chapter_inventory_drifted")
    actual_nodes = conn.execute(
        "SELECT unit_id,source_key,source_sha256,source_text_sha256,"
        "subject,chapter,ocr_page,flagged_risks,scientific_review_state "
        "FROM qa_textbook_evidence_nodes"
    ).fetchall()
    actual_facets = conn.execute(
        "SELECT unit_id,facet,reason,scientific_verified "
        "FROM qa_textbook_facet_candidates"
    ).fetchall()
    return reconcile(expected_nodes, expected_facets, actual_nodes, actual_facets)


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise SystemExit("qa_graph_gate_refuses_non_qa")
    if os.environ.get("QA_IMPORT_ENABLED") != "0":
        raise SystemExit("qa_graph_gate_requires_upload_disabled")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_graph_gate_requires_isolated_schema")
    with isolated_db(schema, "staging", readonly=True) as conn:
        report = inspect(conn)
    print("QA_TEXTBOOK_GRAPH_PROVENANCE_GATE=" + json.dumps(
        report, sort_keys=True, separators=(",", ":")
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
