"""Exact QA stage-one reconciliation: coverage is not science approval."""
from copy import deepcopy

import pytest

from app.qa_stage1_acceptance import reconcile
from app.qa_post_import_audit import EXPECTED_FILES


def sample():
    files = []
    for key, (pages, media) in EXPECTED_FILES.items():
        files.append({
            "key": key, "status": "integrity_passed_with_ocr_risks",
            "source_sha256": "a" * 64,
            "ocr_page_markers": pages, "ocr_unit_count": 100,
            "embedded_media_objects": media or None,
            "missing_ocr_page_markers": [], "issues": [],
            "risk_flag_counts": {"image_dependent": 1} if key == "biochemistry_upper" else {},
            "chapter_unmapped_ocr_page_count": 2 if key == "biochemistry_upper" else 0,
        })
    source = {
        "files": files, "imported_files": 6, "integrity_passed_files": 6,
        "all_six_byte_integrity_checks_passed": True,
    }
    risks = [
        ("risk-text", "biochemistry_upper", "a" * 64, 42, 12, "b" * 64,
         None, None, "image_dependent"),
    ]
    risks += [
        (f"risk-media-{i}", "biochemistry_exams" if i < 27 else "cell_biology_exams",
         "a" * 64, None, None, None, f"word/media/image{i}.png", "c" * 64,
         "unverified_embedded_media")
        for i in range(31)
    ]
    actual_risks = [
        dict(zip(
            ("issue_key", "source_key", "source_sha256", "source_unit_id",
             "source_ocr_page", "text_sha256", "media_member", "media_sha256",
             "risk_flag"),
            row,
        ), review_state="pending")
        for row in risks
    ]
    chapters = [
        (f"chapter-{page}", "biochemistry_upper", "a" * 64, page,
         "before_first_body")
        for page in (1, 2)
    ]
    actual_chapters = [
        dict(zip(
            ("issue_key", "source_key", "source_sha256", "ocr_page",
             "provisional_class"),
            row,
        ), review_state="pending")
        for row in chapters
    ]
    return source, risks, actual_risks, chapters, actual_chapters


def test_every_qa_risk_and_chapter_has_stable_identity_and_explicit_pending_state():
    report = reconcile(*sample())
    assert report["issue_provenance_and_states_accepted"] is True
    assert report["source_files"] == 6
    assert report["ocr_pages"] == 1915
    assert report["media_objects_tracked"] == 31
    assert report["ocr_risk_occurrences_tracked"] == 1
    assert report["risk_ledger_total"] == 32
    assert report["risk_by_state"] == {"pending": 32}
    assert report["chapter_ledger_total"] == 2
    assert report["chapter_by_state"] == {"pending": 2}
    assert report["scientific_or_visual_risks_resolved"] is False
    assert report["question_generation_authorized_by_stage1_alone"] is False
    assert "word/media" not in str(report)  # private member names never logged


@pytest.mark.parametrize("mutator", [
    lambda x: x[2].pop(),                         # missing embedded media
    lambda x: x[2][0].update(source_unit_id=99), # text linked to wrong source
    lambda x: x[2][1].update(media_sha256="f" * 64),
    lambda x: x[2][0].update(review_state="approved"),  # fake scientific verdict
    lambda x: x[4].pop(),                         # missing unmapped chapter page
    lambda x: x[4][0].update(ocr_page=3),         # equal count, wrong page
    lambda x: x[4][0].update(review_state="unknown"),
    lambda x: x[0]["files"][0].update(ocr_page_markers=653),
    lambda x: x[0]["files"][0].update(risk_flag_counts={}),
])
def test_missing_mismatched_or_untracked_risks_fail_closed(mutator):
    inputs = list(deepcopy(sample()))
    mutator(inputs)
    with pytest.raises(ValueError):
        reconcile(*inputs)


def test_rejected_risk_still_tracked_but_not_marked_scientifically_resolved():
    x = list(sample())
    x[2][0]["review_state"] = "rejected_repair_required"
    x[4][0]["review_state"] = "reviewed_context_not_automatically_science_approved"
    report = reconcile(*x)
    assert report["risk_by_state"]["rejected_repair_required"] == 1
    assert report["chapter_by_state"]["reviewed_context_not_automatically_science_approved"] == 1
    assert report["scientific_or_visual_risks_resolved"] is False
