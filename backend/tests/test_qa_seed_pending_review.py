"""Canonical QA source risks stay pending even when SHA/data are verified."""
import pytest
from app.qa_seed_pending_review import build_rows, _identity


def unit(uid,flags):
    return {
        "source_key":"biochemistry_upper",
        "file_id":"1onpEwQsbL3ntkPOU1mFbgQKD2iFQi3n3",
        "source_sha256":"89b226f7c9da53179943375751cacdf873b3693efdb426304a41ed1556e3b013",
        "unit_id":uid,
        "text":"Synthetic ATP and structures paragraph",
        "ocr_page":12,
        "quality_flags":flags,
    }


def test_same_unit_multiple_risks_yield_distinct_no_text_issues():
    rows,stats=build_rows(
        [unit(1,["image_dependent","chemical_structure"]),
         unit(2,[])],
        [],
    )
    assert len(rows)==2
    assert stats["text"]==2
    assert all("Synthetic" not in str(row) for row in rows)
    assert all(row[5] and len(row[5])==64 for row in rows)
    assert all(row[6] is None and row[7] is None for row in rows)
    assert rows[0][0]!=rows[1][0]
    assert _identity("text","hash",1,"risk")==_identity("text","hash",1,"risk")


def test_image_inventory_is_included_without_image_bytes():
    raw=[{"source_key":"biochemistry_exams",
          "source_sha256":"6add10fad4708453f7f8cff5562ee73bf0853d70e43c8c3d67d06299cf82c001",
          "media":[{"member":"word/media/image1.png","sha256":"a"*64}]}]
    rows,stats=build_rows([],raw)
    assert stats=={"media":1}
    assert len(rows)==1
    assert rows[0][6]=="word/media/image1.png"
    assert rows[0][7]=="a"*64
    assert rows[0][8]=="unverified_embedded_media"


def test_changed_locked_book_source_rejected():
    broken=unit(1,["image_dependent"])
    broken["source_sha256"]="0"*64
    with pytest.raises(ValueError,match="Unpinned source"):
        build_rows([broken],[])


def test_duplicate_source_issue_identity_refuses_approval_or_silent_merge():
    with pytest.raises(ValueError,match="Duplicate fingerprint"):
        build_rows([unit(1,["image_dependent"]),unit(1,["image_dependent"])],[])
