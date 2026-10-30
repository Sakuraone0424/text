"""Read-only chapter-frontmatter distribution from the six-source QA SQL ledger.

72 unmapped OCR pages are navigation candidates, not evidence that 72 pages
of scientific textbook content are missing. A chapter gap cannot be marked
scientifically reviewed using a neighboring chapter or text-only guess.
"""
from collections import Counter

from app.source_policy import COLLECTION, pinned_source_bytes

BOOKS = {row["key"]: row["file_id"]
         for row in COLLECTION["files"] if row["kind"] == "textbook"}
EXPECTED_PENDING_BY_BOOK = {
    "biochemistry_upper": 35,
    "biochemistry_lower": 19,
    "biochemistry_notes": 2,
    "cell_biology": 16,
}
CATEGORIES = {"before_first_body", "after_or_no_body"}


def summarize(rows):
    observed = Counter()
    unique = set()
    for row in rows:
        key, page = row["source_key"], row["ocr_page"]
        identity = (key, page)
        if (key not in BOOKS or type(page) is not int or page < 1
                or identity in unique or row["provisional_class"] not in CATEGORIES
                or not pinned_source_bytes(BOOKS[key], row["source_sha256"])):
            raise ValueError("chapter_frontmatter_row_not_exactly_six_source_locked")
        unique.add(identity)
        observed[key] += 1
    if dict(observed) != EXPECTED_PENDING_BY_BOOK:
        raise ValueError("chapter_frontmatter_page_inventory_drift")
    outside = sum(
        row["provisional_class"] == "after_or_no_body" for row in rows
    )
    return {
        "unmapped_chapter_pages_total": len(rows),
        "unmapped_pages_by_book": dict(sorted(observed.items())),
        "unmapped_before_first_recognized_body_chapter": len(rows) - outside,
        "unmapped_at_or_after_body": outside,
        "all_current_72_are_before_first_body": outside == 0,
        "frontmatter_classification_is_navigation_only": True,
        "actual_missing_content_ruled_out": False,
        "original_page_science_approvals": 0,
    }


def inspect(conn):
    rows = conn.execute(
        "SELECT source_key,ocr_page,provisional_class,source_sha256 "
        "FROM qa_chapter_mapping_items ORDER BY source_key,ocr_page"
    ).fetchall()
    return summarize(rows)
