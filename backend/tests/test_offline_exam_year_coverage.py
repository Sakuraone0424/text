"""Synthetic-only 52-year audit: no certified original-paper claims."""
import json
import pytest

from app.offline_exam_year_coverage import report
from app.source_policy import COLLECTION
from test_collection_seed import synthetic_collection


def test_six_source_year_audit_is_pinned_and_explicitly_provisional(tmp_path):
    root, lock=synthetic_collection(tmp_path)
    data=report(root,lock_path=lock)
    assert data["source_files_hash_verified"]==6
    assert data["expected_year_rows"]==52
    assert len(data["years"])==52
    assert {x["subject"] for x in data["years"]}=={"cell_biology","biochemistry"}
    assert data["embedded_media_awaiting_review"]>=0
    assert not data["official_originals_independently_verified"]
    assert not data["answer_keys_scientifically_verified"]
    assert not data["question_counts_finalized"]
    assert all(
        x["candidate_question_starts"] >=
        x["parser_safe_style_candidates"] for x in data["years"]
    )
    assert all(
        sum(x["candidate_types"].values())
        <= x["candidate_question_starts"] for x in data["years"]
    )
    assert all(
        not x["official_original_independently_verified"]
        and not x["scientific_answers_verified"] for x in data["years"]
    )
    rendered=json.dumps(data,ensure_ascii=False)
    assert "Synthetic 2023 question" not in rendered
    assert str(root) not in rendered


def test_exam_source_modified_byte_fails_before_any_year_audit(tmp_path):
    root,lock=synthetic_collection(tmp_path)
    item=next(x for x in COLLECTION["files"] if x["key"]=="biochemistry_exams")
    path=root / item["relative_path"]
    path.write_bytes(path.read_bytes()+b"tampered")
    with pytest.raises(ValueError,match="SHA-256"):
        report(root,lock_path=lock)
