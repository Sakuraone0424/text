"""Read-only chapter-gap distribution from the six-source QA SQL ledger.

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

# Independently re-read the original, SHA-locked OCR page text rather than
# assuming that everything preceding the FIRST NUMBERED CHAPTER is frontmatter.
# Upper pp18-33 are a substantive unnumbered introduction; lower p19 is
# substantive scientific prose. Part openers can also carry scientific content.
# These are fixed navigation hints for the pinned corpus, NOT science approvals.
SUBSTANTIVE_UNNUMBERED = {
    "biochemistry_upper": frozenset(range(18, 34)),
    "biochemistry_lower": frozenset({19}),
}
PART_TRANSITIONS = {
    "biochemistry_upper": frozenset({34, 35}),
    "biochemistry_lower": frozenset({18}),
}


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
    unnumbered = sum(
        row["ocr_page"] in SUBSTANTIVE_UNNUMBERED.get(row["source_key"], ())
        for row in rows
    )
    transitions = sum(
        row["ocr_page"] in PART_TRANSITIONS.get(row["source_key"], ())
        for row in rows
    )
    frontmatter = len(rows) - unnumbered - transitions
    # A positional detector does not establish that no scientific正文 exists.
    if unnumbered != 17 or transitions != 3 or frontmatter != 52:
        raise ValueError("pinned_unnumbered_science_page_inventory_drift")
    return {
        "unmapped_chapter_pages_total": len(rows),
        "unmapped_pages_by_book": dict(sorted(observed.items())),
        "unmapped_before_first_recognized_body_chapter": len(rows) - outside,
        "unmapped_at_or_after_body": outside,
        "all_current_72_are_before_first_body": outside == 0 and unnumbered == 0 and transitions == 0,
        "before_first_body_provisional_means_first_numbered_chapter_only": True,
        "unmapped_unnumbered_science_or_intro_pages": unnumbered,
        "unmapped_part_transition_pages_with_science_context": transitions,
        "unmapped_probable_frontmatter_or_contents_pages": frontmatter,
        "all_72_frontmatter_only_claim_rejected": unnumbered > 0 or transitions > 0,
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
