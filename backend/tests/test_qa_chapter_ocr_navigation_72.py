"""All 72 unmapped OCR pages receive source-locked navigation proposals, never a science pass."""
import hashlib
import pytest
from app import qa_chapter_ocr_navigation_72 as nav


def example_issues_and_units():
    issues=[]
    units=[]
    uid=1
    for key,n in nav.EXPECTED_PENDING_BY_BOOK.items():
        for page in range(1,n+1):
            issues.append(dict(issue_key=key+":"+str(page),source_key=key,
                               source_sha256="a"*64,ocr_page=page))
            units.append(dict(source_key=key,source_sha256="a"*64,ocr_page=page,
                              unit_id=uid,ordinal=uid,text="OCR paragraph "+str(uid)))
            uid+=1
    return issues,units


def test_72_pages_are_not_falsely_all_frontmatter(monkeypatch):
    monkeypatch.setattr(nav,"pinned_source_bytes",lambda *a:True)
    issues,units=example_issues_and_units()
    result=nav.build(issues,units)
    assert len(result)==72
    counts={}
    for x in result:
        counts[x["navigation_category"]]=counts.get(x["navigation_category"],0)+1
        assert x["ocr_text_sha256"]==hashlib.sha256(
            ("OCR paragraph "+str(x["unit_ids"][0])).encode()).hexdigest()
    assert counts=={
        "frontmatter_or_contents":52,
        "unnumbered_scientific_intro":17,
        "part_transition_science_context":3,
    }
    lower19=next(x for x in result if x["issue_key"]=="biochemistry_lower:19")
    assert "导言" in lower19["proposed_ocr_label"]
    upper18=next(x for x in result if x["issue_key"]=="biochemistry_upper:18")
    assert "绪论" in upper18["proposed_ocr_label"]


def test_duplicate_or_changed_source_page_refused(monkeypatch):
    monkeypatch.setattr(nav,"pinned_source_bytes",lambda *a:True)
    issues,units=example_issues_and_units()
    with pytest.raises(ValueError,match="source_lock"):
        nav.build(issues+[issues[0]],units)
    with pytest.raises(ValueError,match="coverage_drift"):
        nav.build(issues[:-1],units)
    monkeypatch.setattr(nav,"pinned_source_bytes",lambda *a:False)
    with pytest.raises(ValueError,match="source_lock"):
        nav.build(issues,units)


def test_actual_original_six_source_pending_chapter_items_are_not_mutated(monkeypatch):
    monkeypatch.setattr(nav,"pinned_source_bytes",lambda *a:True)
    issues,units=example_issues_and_units()
    issues_before=[dict(x) for x in issues]
    units_before=[dict(x) for x in units]
    nav.build(issues,units)
    assert issues==issues_before
    assert units==units_before
    assert "pending_science_review boolean NOT NULL DEFAULT true" in nav.DDL
    assert "original_visual_verified boolean NOT NULL DEFAULT false" in nav.DDL
