"""Offline parser parity with SQL exam source units; synthetic bytes only."""
import json

import pytest

from app.offline_exam_style_audit import report, main
from app.training_spec_v2 import SUBJECTS, COUNTS
from app.source_policy import COLLECTION
from test_collection_seed import synthetic_collection


def test_offline_six_source_style_report_has_eight_buckets_and_no_exam_text(tmp_path):
    directory, lock = synthetic_collection(tmp_path)
    result = report(directory, lock_path=lock)
    assert result["all_six_locked_sha256_verified"]
    assert result["observed_ocr_page_markers"] == 1915
    assert len(result["sources"]) == 2
    assert len(result["buckets"]) == 8
    assert {(x["subject"], x["question_type"]) for x in result["buckets"]} == {
        (subject, kind) for subject in SUBJECTS for kind in COUNTS
    }
    single = next(x for x in result["buckets"]
                  if x["subject"] == "biochemistry" and x["question_type"] == "single_choice")
    assert single["observed_question_starts"] == 1
    assert single["safe_style_anchors"] == 1
    assert single["years_with_safe_anchors"] == [2023]
    assert result["scientific_answer_verification"] is False
    assert result["database_import_verified"] is False
    assert result["question_stock_120_verified"] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert "Synthetic 2023 question" not in serialized
    assert "synthetic" not in str(result["sources"]).lower()


def test_offline_report_rejects_source_changed_after_lock(tmp_path):
    directory, lock = synthetic_collection(tmp_path)
    cell = next(x for x in COLLECTION["files"] if x["key"] == "cell_biology")
    source = directory / cell["relative_path"]
    source.write_bytes(source.read_bytes() + b"new revision")
    with pytest.raises(ValueError, match="SHA-256"):
        report(directory, lock_path=lock)


def test_offline_report_counts_missing_style_instead_of_fabrication(tmp_path):
    directory, lock = synthetic_collection(tmp_path)
    result = report(directory, lock_path=lock)
    assert result["ready_for_same_type_style"] is False
    assert result["missing_same_type_style"]
    assert all(
        bucket["safe_style_anchors"] <= bucket["observed_question_starts"]
        for bucket in result["buckets"]
    )
    assert sum(entry["media_objects"] for entry in result["sources"]) == 0
