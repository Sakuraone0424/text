"""Read-only QA status reconciler cannot claim content or science acceptance."""
from copy import deepcopy
import pytest
from app.qa_post_import_audit import summarize, EXPECTED_FILES


def sample():
    files = []
    for key, (pages, media) in EXPECTED_FILES.items():
        files.append({
            "key": key, "status": "integrity_passed_with_ocr_risks",
            "source_sha256": "a" * 64,
            "ocr_page_markers": pages,
            "ocr_unit_count": 100,
            "embedded_media_objects": media if media else None,
            "issues": [], "missing_ocr_page_markers": [],
            "risk_flag_counts": {"image_dependent": 2},
            "chapter_unmapped_ocr_page_count": 1,
        })
    return {"files": files, "imported_files": 6,
            "integrity_passed_files": 6,
            "all_six_byte_integrity_checks_passed": True}


def test_good_source_readback_keeps_all_science_flags_pending():
    data = summarize(sample())
    assert data["six_file_source_count"] == 6
    assert data["ocr_page_markers"] == 1915
    assert data["embedded_media_objects"] == 31
    assert data["risk_occurrences_total"] == 12
    assert data["source_text_or_answer_exported"] is False
    assert data["media_content_scientifically_verified"] is False
    assert data["chapter_mapping_scientifically_verified"] is False
    assert data["original_exam_questions_independently_authenticated"] is False
    assert data["two_round_accepted_question_reserve"] == 0
    assert len(data["source_details"]) == 6
    assert all(x["chapter_scientifically_verified"] is False for x in data["source_details"])


@pytest.mark.parametrize("change", [
    lambda d: d.update(imported_files=5),
    lambda d: d.update(integrity_passed_files=5),
    lambda d: d.update(all_six_byte_integrity_checks_passed=False),
    lambda d: d["files"][0].update(status="not_imported"),
    lambda d: d["files"][0].update(ocr_page_markers=653),
    lambda d: d["files"][-1].update(embedded_media_objects=2),
    lambda d: d["files"][1].update(missing_ocr_page_markers=[11]),
    lambda d: d["files"][0].update(issues=["hash_mismatch"]),
    lambda d: d["files"][2].update(source_sha256=None),
    lambda d: d["files"].pop(),
])
def test_bad_db_audit_never_prints_success(change):
    data = sample()
    change(data)
    with pytest.raises(ValueError):
        summarize(data)


def test_nonmatching_parser_counts_are_reported_not_fabricated():
    data = summarize(sample())
    assert data["qa_source_text_unit_count"] == 600
    assert data["independent_offline_text_unit_count"] == 12302
    assert data["parser_unit_count_parity"] is False
