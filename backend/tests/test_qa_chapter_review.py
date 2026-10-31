"""Unmapped OCR pages are trackable, not automatically source errors."""
import json
from pathlib import Path
import pytest
from app.qa_chapter_review import rows_from_report
from app.source_policy import COLLECTION


def fixture_report():
    locked=json.loads(
        (Path(__file__).parents[1] / "app" / "ocr_source_sha256_lock.json")
        .read_text(encoding="utf8")
    )
    counts={"biochemistry_upper":35,"biochemistry_lower":19,
            "biochemistry_notes":2,"cell_biology":16}
    files=[]
    for source in COLLECTION["files"]:
        if source["kind"]!="textbook":
            continue
        key=source["key"]
        pages=list(range(1,counts[key]+1))
        files.append({
            "key":key,
            "source_sha256":locked["files"][key]["sha256"],
            "chapter_unmapped_ocr_pages":pages,
            "chapter_unmapped_before_body":pages[:-1],
            "chapter_unmapped_at_or_after_body":pages[-1:],
        })
    return {"files":files}


def test_72_unmapped_ocr_pages_are_preserved_with_uncertainty():
    rows=rows_from_report(fixture_report())
    assert len(rows)==72
    assert len({r[0] for r in rows})==72
    assert all(len(r[0])==64 and r[3]>0 for r in rows)
    assert all(r[4] in ("before_first_body","after_or_no_body") for r in rows)


def test_inconsistent_page_sets_fail_closed():
    report=fixture_report()
    report["files"][0]["chapter_unmapped_at_or_after_body"]=[100]
    with pytest.raises(ValueError,match="Inconsistent"):
        rows_from_report(report)


def test_unpinned_source_identity_refused():
    report=fixture_report()
    report["files"][0]["source_sha256"]="0"*64
    with pytest.raises(ValueError,match="Unpinned"):
        rows_from_report(report)
