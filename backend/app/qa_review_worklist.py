"""QA-only privacy-safe priority inventory for the six-source review backlog.

Neither clean OCR flags nor high regex-facet coverage establishes scientific
truth. This module never mutates data, exports excerpts, records approval
events, or starts a billable model. Every count comes from the independent QA
PostgreSQL issue ledgers and textbook source-node inventory.
"""
from collections import Counter, defaultdict
import json
import os

from app.qa_stage1_acceptance import RISK_STATES, CHAPTER_STATES
from app.source_intake import SCHEMA, isolated_db
from app.source_policy import COLLECTION

SOURCE_KEYS = frozenset(item["key"] for item in COLLECTION["files"])
BOOKS = frozenset(
    item["key"] for item in COLLECTION["files"] if item["kind"] == "textbook"
)


def priority(flag):
    """Metadata triage order, NOT verified severity or scientific diagnosis."""
    token = str(flag).casefold()
    if any(word in token for word in (
        "corrupt", "untranscribed", "missing", "unreadable", "truncat",
        "image", "figure", "structure", "formula", "equation", "chemical",
        "unverified_embedded_media",
    )):
        return "P0_visual_or_content_loss_candidate"
    if any(word in token for word in (
        "ocr", "table", "layout", "symbol", "superscript", "subscript",
        "unicode", "math", "notation",
    )):
        return "P1_ocr_layout_symbol_candidate"
    return "P2_other_review_required"


def summarize(risks, chapters, nodes, *, expected_risks=5094,
              expected_chapters=72, expected_nodes=9655, expected_media=31):
    """Fail closed if the triage inventory omits or misattributes QA records."""
    if (len(risks), len(chapters), len(nodes)) != (
        expected_risks, expected_chapters, expected_nodes
    ):
        raise ValueError("qa_review_backlog_incomplete")
    keys = set()
    by_source = defaultdict(Counter)
    by_flag = Counter()
    by_priority = Counter()
    risk_per_unit = Counter()
    textbook_risk_ids = set()
    states = Counter()
    for row in risks:
        key, source, flag, state = (
            row["issue_key"], row["source_key"], row["risk_flag"],
            row["review_state"],
        )
        if (key in keys or source not in SOURCE_KEYS or state not in RISK_STATES
                or not isinstance(flag, str) or not flag):
            raise ValueError("qa_review_risk_missing_identity_or_state")
        keys.add(key)
        by_source[source]["total_risks"] += 1
        by_source[source][state] += 1
        by_flag[flag] += 1
        by_priority[priority(flag)] += 1
        states[state] += 1
        uid = row.get("source_unit_id")
        if uid is None:
            if flag != "unverified_embedded_media" or source in BOOKS:
                raise ValueError("qa_review_invalid_media_risk")
            by_source[source]["media"] += 1
        else:
            if type(uid) is not int or uid < 1:
                raise ValueError("qa_review_invalid_text_unit")
            risk_per_unit[uid] += 1
            if source in BOOKS:
                textbook_risk_ids.add(uid)
            by_source[source]["text_risks"] += 1
    if sum(by_source[s]["media"] for s in SOURCE_KEYS) != expected_media:
        raise ValueError("qa_review_original_media_count_mismatch")

    chapter_ids = set()
    chapter_pages = set()
    chapter_states = Counter()
    for row in chapters:
        key, source, state = (
            row["issue_key"], row["source_key"], row["review_state"]
        )
        if (key in chapter_ids or source not in BOOKS
                or state not in CHAPTER_STATES
                or type(row["ocr_page"]) is not int
                or row["ocr_page"] < 1):
            raise ValueError("qa_review_untracked_chapter_page")
        location = (source, row["ocr_page"])
        if location in chapter_pages:
            raise ValueError("qa_review_duplicate_chapter_page")
        chapter_ids.add(key)
        chapter_pages.add(location)
        chapter_states[state] += 1
    node_ids = set()
    ocr_unflagged_with_mapped_chapter = 0
    ocr_unflagged_and_chapter_location_traced = 0
    for row in nodes:
        uid = row["unit_id"]
        if (uid in node_ids or type(uid) is not int or uid < 1
                or row["source_key"] not in BOOKS
                or type(row["flagged_risks"]) is not int
                or row["flagged_risks"] < 0):
            raise ValueError("qa_review_invalid_textbook_node")
        node_ids.add(uid)
        if row["flagged_risks"] != risk_per_unit[uid]:
            raise ValueError("qa_review_text_unit_risk_ledger_mismatch")
        if row["flagged_risks"] == 0 and row["chapter"] not in (
            "", "unclassified", "unknown", "contents", None
        ):
            ocr_unflagged_with_mapped_chapter += 1
            if (row["source_key"], row["ocr_page"]) not in chapter_pages:
                ocr_unflagged_and_chapter_location_traced += 1
    # Every flagged textbook unit must appear in the canonical graph.
    if textbook_risk_ids - node_ids:
        raise ValueError("qa_review_textbook_risk_missing_graph_node")

    return {
        "status": "qa_source_review_priorities_grounded_in_real_issue_ledgers",
        "source_issues_total": len(risks),
        "review_state_counts": dict(sorted(states.items())),
        "chapter_mapping_issues": len(chapters),
        "chapter_review_state_counts": dict(sorted(chapter_states.items())),
        "metadata_only_priority_counts": dict(sorted(by_priority.items())),
        "risk_occurrences_by_type": dict(sorted(by_flag.items())),
        "by_source": {
            source: dict(sorted(by_source[source].items()))
            for source in sorted(SOURCE_KEYS)
        },
        "textbook_nodes_total": len(nodes),
        "textbook_units_without_ocr_risk_flags_and_with_chapter": (
            ocr_unflagged_with_mapped_chapter
        ),
        "textbook_units_also_outside_unmapped_chapter_pages": (
            ocr_unflagged_and_chapter_location_traced
        ),
        "textbook_nodes_scientifically_approved_by_this_process": 0,
        "review_events_inserted": 0,
        "private_excerpts_or_images_exported": False,
        "ready_to_publish_scientific_questions": False,
    }


def inspect(conn):
    risks = conn.execute(
        "SELECT i.issue_key,i.source_key,i.source_unit_id,i.risk_flag,"
        "p.review_state FROM qa_source_risk_items i "
        "JOIN qa_source_risk_progress p ON p.issue_key=i.issue_key"
    ).fetchall()
    chapters = conn.execute(
        "SELECT i.issue_key,i.source_key,i.ocr_page,p.review_state "
        "FROM qa_chapter_mapping_items i "
        "JOIN qa_chapter_mapping_progress p ON p.issue_key=i.issue_key"
    ).fetchall()
    nodes = conn.execute(
        "SELECT unit_id,source_key,chapter,ocr_page,flagged_risks "
        "FROM qa_textbook_evidence_nodes"
    ).fetchall()
    return summarize(risks, chapters, nodes)


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower() != "qa":
        raise SystemExit("qa_review_worklist_refuses_non_qa")
    if os.environ.get("QA_IMPORT_ENABLED") != "0":
        raise SystemExit("qa_review_worklist_requires_upload_disabled")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_review_worklist_requires_isolated_schema")
    with isolated_db(schema, "staging", readonly=True) as conn:
        report = inspect(conn)
    print("QA_SCIENCE_REVIEW_WORKLIST=" + json.dumps(
        report, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
