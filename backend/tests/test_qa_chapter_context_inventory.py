"""Frontmatter navigation must not silently approve or erase 72 source risks."""
import pytest

from app import qa_chapter_context_inventory as chapter


def example_rows():
    return [
        {"source_key": key, "ocr_page": i,
         "provisional_class": "before_first_body",
         "source_sha256": "a"*64}
        for key, count in chapter.EXPECTED_PENDING_BY_BOOK.items()
        for i in range(1, count + 1)
    ]


def test_all_72_ocr_chapter_gaps_are_frontmatter_only_pending(monkeypatch):
    monkeypatch.setattr(chapter, "pinned_source_bytes", lambda fid, digest: True)
    value = chapter.summarize(example_rows())
    assert value["unmapped_chapter_pages_total"] == 72
    assert value["unmapped_before_first_recognized_body_chapter"] == 72
    assert value["unmapped_at_or_after_body"] == 0
    assert value["actual_missing_content_ruled_out"] is False
    assert value["original_page_science_approvals"] == 0


def test_actual_body_page_gap_never_reported_as_all_frontmatter(monkeypatch):
    monkeypatch.setattr(chapter, "pinned_source_bytes", lambda fid, digest: True)
    rows = example_rows()
    rows[0]["provisional_class"] = "after_or_no_body"
    value = chapter.summarize(rows)
    assert not value["all_current_72_are_before_first_body"]


def test_changed_sha_or_missing_page_fails_closed(monkeypatch):
    monkeypatch.setattr(chapter, "pinned_source_bytes", lambda fid, digest: False)
    with pytest.raises(ValueError, match="six_source_locked"):
        chapter.summarize(example_rows())
    monkeypatch.setattr(chapter, "pinned_source_bytes", lambda fid, digest: True)
    with pytest.raises(ValueError, match="inventory_drift"):
        chapter.summarize(example_rows()[1:])
