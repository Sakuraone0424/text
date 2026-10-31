"""Real QA review priority figures must never be science/answer approval."""
from copy import deepcopy

import pytest

from app.qa_review_worklist import summarize, priority


def sample():
    risks = [
        dict(issue_key="txt-1", source_key="biochemistry_upper",
             source_unit_id=101, risk_flag="image_dependent",
             review_state="pending"),
        dict(issue_key="txt-2", source_key="biochemistry_upper",
             source_unit_id=101, risk_flag="corrupt_ocr",
             review_state="pending"),
        dict(issue_key="media-1", source_key="biochemistry_exams",
             source_unit_id=None, risk_flag="unverified_embedded_media",
             review_state="pending"),
    ]
    chapters = [
        dict(issue_key="chapter-1", source_key="biochemistry_upper",
             ocr_page=1, review_state="pending"),
    ]
    nodes = [
        dict(unit_id=101, source_key="biochemistry_upper", chapter="Chapter 1",
             ocr_page=1, flagged_risks=2),
        dict(unit_id=102, source_key="biochemistry_upper", chapter="Chapter 1",
             ocr_page=2, flagged_risks=0),
    ]
    return [risks, chapters, nodes]


def check(a):
    return summarize(*a, expected_risks=3, expected_chapters=1,
                     expected_nodes=2, expected_media=1)


def test_real_issue_counts_and_textbook_review_priority_without_faking_approval():
    report = check(sample())
    assert report["source_issues_total"] == 3
    assert report["review_state_counts"] == {"pending": 3}
    assert report["metadata_only_priority_counts"] == {
        "P0_visual_or_content_loss_candidate": 3,
    }
    assert report["textbook_units_without_ocr_risk_flags_and_with_chapter"] == 1
    assert report["textbook_units_also_outside_unmapped_chapter_pages"] == 1
    assert report["textbook_nodes_scientifically_approved_by_this_process"] == 0
    assert report["review_events_inserted"] == 0
    assert report["private_excerpts_or_images_exported"] is False
    assert report["ready_to_publish_scientific_questions"] is False


@pytest.mark.parametrize("mutate", [
    lambda a: a[0].pop(),
    lambda a: a[0][1].update(issue_key="txt-1"),
    lambda a: a[0][1].update(review_state="science_approved"),
    lambda a: a[0][1].update(source_key="other_book"),
    lambda a: a[0][2].update(source_unit_id=123),
    lambda a: a[1][0].update(ocr_page=0),
    lambda a: a[2][0].update(flagged_risks=1),
    lambda a: a[2][0].update(unit_id=102),
    lambda a: a[2][0].update(source_key="biochemistry_exams"),
])
def test_missing_duplicate_or_mismatched_risk_provenance_fails(mutate):
    data = deepcopy(sample())
    mutate(data)
    with pytest.raises(ValueError):
        check(data)


def test_risk_priority_is_just_metadata_not_scientific_truth():
    assert priority("chemical_structure") == "P0_visual_or_content_loss_candidate"
    assert priority("complex_table") == "P1_ocr_layout_symbol_candidate"
    assert priority("unknown_other_review_flag") == "P2_other_review_required"
