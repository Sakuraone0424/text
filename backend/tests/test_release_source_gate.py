"""Read-only SHA-256/marker/media release gate, using synthetic audit rows."""
import copy
import json

import pytest

from app.release_source_gate import EXPECTED_MEDIA, LOCK, evaluate
from app.source_policy import COLLECTION


def synthetic_database_audit():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    rows = []
    for item in COLLECTION["files"]:
        rows.append({
            "key": item["key"],
            "document_id": 1,
            "source_sha256": lock["files"][item["key"]]["sha256"],
            "status": "integrity_passed",
            "ocr_page_markers": item.get("expected_pages", 0),
            "missing_ocr_page_markers": [],
            "blank_ocr_pages": [],
            "risk_flag_counts": {},
            "embedded_media_objects": EXPECTED_MEDIA.get(item["key"]),
            "embedded_media_review_status": (
                "embedded_media_not_independently_transcribed"
                if item["kind"] == "exam_transcription" else "not_applicable"
            ),
        })
    return {"files": rows}, lock


def test_six_locked_sources_allow_only_integrity_claim():
    audited, lock = synthetic_database_audit()
    result = evaluate(audited, lock)
    assert result["all_six_locked_sources_integrity_passed"]
    assert result["verified_locked_sources"] == 6
    assert result["observed_ocr_markers"] == 1915
    assert result["known_docx_media_objects"] == 31
    assert set(result["image_transcription_pending"]) == set(EXPECTED_MEDIA)
    assert result["eligible_to_claim_120_verified_questions"] is False
    assert result["production_release_authorized"] is False


@pytest.mark.parametrize("defect,issue", [
    ("wrong_sha", "not_the_locked_private_source_bytes"),
    ("missing", "source_not_imported"),
    ("bad_index", "database_source_integrity_failed"),
    ("missing_page", "ocr_page_marker_count_mismatch"),
    ("blank_page", "blank_ocr_pages"),
    ("media", "unexpected_exam_docx_media_count"),
])
def test_release_gate_blocks_all_six_integrity_on_any_defect(defect, issue):
    audited, lock = synthetic_database_audit()
    files = audited["files"]
    target = next(
        row for row in files if row["key"] == (
            "cell_biology_exams" if defect == "media" else "biochemistry_upper"
        )
    )
    if defect == "wrong_sha":
        target["source_sha256"] = "0" * 64
    elif defect == "missing":
        target["document_id"] = None
    elif defect == "bad_index":
        target["status"] = "integrity_failed"
    elif defect == "missing_page":
        target["ocr_page_markers"] -= 1
    elif defect == "blank_page":
        target["blank_ocr_pages"] = [20]
    elif defect == "media":
        target["embedded_media_objects"] = 0

    result = evaluate(audited, lock)
    assert not result["all_six_locked_sources_integrity_passed"]
    assert result["verified_locked_sources"] == 5
    assert issue in next(x for x in result["files"] if x["key"] == target["key"])["issues"]
    assert not result["production_release_authorized"]


def test_release_gate_rejects_mismatched_collection_lock_even_with_clean_rows():
    audited, lock = synthetic_database_audit()
    tampered = copy.deepcopy(lock)
    tampered["collection_version"] = "outdated"
    with pytest.raises(ValueError, match="version"):
        evaluate(audited, tampered)
    tampered = copy.deepcopy(lock)
    del tampered["files"]["cell_biology"]
    with pytest.raises(ValueError, match="incomplete"):
        evaluate(audited, tampered)
