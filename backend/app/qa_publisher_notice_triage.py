"""QA-only source-locked classification of publisher conditional 'missing page' notices.

The OCR regex detected three publisher warranty sentences. Their use of 缺页
is not evidence a page was actually lost. Keep source risk rows and review
states unchanged; this is metadata triage, never page/visual approval.
"""
import re
from app.source_policy import FILES, pinned_source_bytes

EXPECTED = {
    "biochemistry_upper": 5,
    "biochemistry_lower": 5,
    "cell_biology": 3,
}


def summarize(rows):
    if len(rows) != len(EXPECTED):
        raise ValueError("publisher_notice_risk_inventory_drift")
    seen = set()
    for row in rows:
        key = row["source_key"]
        if (
            key not in EXPECTED
            or key in seen
            or row["ocr_page"] != EXPECTED[key]
            or row["risk_flag"] != "transcription_gap_reported"
            or row["file_id"] not in FILES
            or FILES[row["file_id"]]["key"] != key
            or not pinned_source_bytes(row["file_id"], row["source_sha256"])
            or not re.search(r"本书如有缺页[^。\n]{0,100}调换", row["text"])
        ):
            raise ValueError("publisher_notice_not_proven_conditional")
        seen.add(key)
    return {
        "conditional_publisher_notice_ocr_regex_hits": len(seen),
        "source_files": sorted(seen),
        "all_three_are_conditional_warranty_language": True,
        "actual_missing_pages_ruled_out": False,
        "risk_ledger_state_unchanged": True,
        "scientific_or_visual_approvals": 0,
    }


def inspect(conn):
    rows = conn.execute(
        "SELECT i.source_key,i.source_ocr_page AS ocr_page,i.risk_flag,"
        "d.file_id,d.sha256 AS source_sha256,u.text "
        "FROM qa_source_risk_items i "
        "JOIN source_units u ON u.id=i.source_unit_id "
        "JOIN source_documents d ON d.id=u.document_id "
        "WHERE i.risk_flag=%s",
        ("transcription_gap_reported",),
    ).fetchall()
    return summarize(rows)
