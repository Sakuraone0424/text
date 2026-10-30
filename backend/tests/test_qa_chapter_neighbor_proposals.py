"""Synthetic chapter-navigation fixtures; never approve OCR chapter mappings."""
import pytest
from app import qa_chapter_neighbor_proposals as review


@pytest.fixture(autouse=True)
def source_lock(monkeypatch):
    monkeypatch.setattr(review,"pinned_source_bytes",lambda fid,sha:sha=="a"*64)


def node(page,chapter):
    return dict(source_key="cell_biology",source_sha256="a"*64,
                ocr_page=page,chapter=chapter)


def issue(page,key=None):
    return dict(issue_key=key or f"issue-{page}",source_key="cell_biology",
                source_sha256="a"*64,ocr_page=page)


def test_between_chapters_keeps_both_navigation_hints_and_no_approval():
    rows=review.derive([issue(7)],[
        node(2,"第一章"),node(5,"第一章"),node(12,"第二章")
    ])
    assert rows == [("issue-7",review.VERSION,"a"*64,7,"第一章",2,"第二章",5)]


def test_frontmatter_does_not_invent_previous_chapter():
    row=review.derive([issue(1)],[node(7,"第一章")])[0]
    assert row[4] is None and row[5] is None
    assert row[6] == "第一章" and row[7] == 6


def test_conflicting_neighbor_does_not_choose_one_arbitrarily():
    row=review.derive([issue(2)],[node(1,"第一章"),node(1,"第二章")])[0]
    assert row[4] is None and row[5] is None


def test_unpinned_neighbor_fails_closed():
    bad=node(2,"第一章")
    bad["source_sha256"]="f"*64
    with pytest.raises(ValueError,match="unpinned_graph"):
        review.derive([issue(3)],[bad])


def test_duplicate_issue_fails_closed():
    with pytest.raises(ValueError,match="duplicate"):
        review.derive([issue(1),issue(1)],[node(5,"第一章")])
