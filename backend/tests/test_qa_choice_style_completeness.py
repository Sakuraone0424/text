"""A question heading without choices must never be a trusted A-D style example."""
from copy import deepcopy

import pytest

from app.qa_choice_style_completeness import inventory
from app.source_policy import COLLECTION
from pathlib import Path
import json


def exam(subject, unit_id, *, risk=None):
    item = next(x for x in COLLECTION["files"]
                if x["subject"] == subject and x["kind"] == "exam_transcription")
    lock = json.loads(
        (Path(__file__).parents[1] / "app" / "ocr_source_sha256_lock.json")
        .read_text(encoding="utf-8")
    )
    return {
        "subject": subject, "unit_id": unit_id, "document_id": unit_id,
        "ordinal": 1, "year": 2017, "file_id": item["file_id"],
        "source_sha256": lock["files"][item["key"]]["sha256"],
        "concerns": [risk] if risk else [], "quality_flags": [],
    }


def chunks(question_id, with_options=True):
    items = [{"id": question_id, "text": "1. Test question",
              "quality_flags": []}]
    if with_options:
        items.extend(
            {"id": question_id + i, "text": f"{label}. Synthetic answer",
             "quality_flags": []}
            for i, label in enumerate("ABCD", 1)
        )
    return items


def test_only_complete_source_locked_A_to_D_count_as_style_examples():
    rows = [exam("biochemistry", 10), exam("cell_biology", 20),
            exam("cell_biology", 30)]
    report = inventory(rows, lambda doc, ordinal: chunks(doc, doc != 30))
    assert report["by_subject"]["biochemistry"]["complete_A_to_D_choice_examples"] == 1
    assert report["by_subject"]["cell_biology"]["complete_A_to_D_choice_examples"] == 1
    assert report["by_subject"]["cell_biology"]["incomplete_or_unreadable_options"] == 1
    assert report["both_subjects_have_complete_choice_style_examples"] is True
    assert report["answer_correctness_or_science_approved"] is False


def test_image_blocker_does_not_launder_a_question_as_complete():
    report = inventory([exam("cell_biology", 5, risk="image_dependent")],
                       lambda doc, ordinal: chunks(doc))
    assert report["by_subject"]["cell_biology"]["blocked_before_option_check"] == 1
    assert report["by_subject"]["cell_biology"]["complete_A_to_D_choice_examples"] == 0
    assert report["both_subjects_have_complete_choice_style_examples"] is False


def test_changed_source_hash_fails_even_with_perfect_A_to_D():
    row = exam("biochemistry", 1)
    row["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source_locked"):
        inventory([row], lambda doc, ordinal: chunks(doc))


def test_incomplete_or_wrong_ordinal_question_is_not_counted():
    row = exam("biochemistry", 20)
    choices = chunks(21)
    report = inventory([row], lambda doc, ordinal: choices)
    assert report["by_subject"]["biochemistry"]["complete_A_to_D_choice_examples"] == 0
    assert report["by_subject"]["biochemistry"]["incomplete_or_unreadable_options"] == 1


def test_52_locked_subject_year_slots_report_actual_complete_options_only():
    report = inventory(
        [exam("biochemistry", 10), exam("cell_biology", 20)],
        lambda doc, ordinal: chunks(doc),
    )
    assert report["by_source_year_is_52_real_source_slots"] is True
    assert len(report["by_source_year"]) == 52
    bio = next(item for item in report["by_source_year"]
               if item["subject"] == "biochemistry" and item["year"] == 2017)
    cell = next(item for item in report["by_source_year"]
                if item["subject"] == "cell_biology" and item["year"] == 2017)
    assert bio["complete_A_to_D_choice_examples"] == 1
    assert cell["complete_A_to_D_choice_examples"] == 1
    assert report["unassigned_or_outside_year_choice_starts"] == {
        "biochemistry": 0, "cell_biology": 0
    }
    assert not any(
        r["complete_A_to_D_choice_examples"] for r in report["by_source_year"]
        if r["year"] != 2017
    )


def test_out_of_range_year_cannot_launder_complete_exam_question():
    row = exam("biochemistry", 11)
    row["year"] = 1998  # legitimate cell year but outside biochemistry source
    report = inventory([row], lambda doc, ordinal: chunks(doc))
    assert report["by_subject"]["biochemistry"]["complete_A_to_D_choice_examples"] == 0
    assert report["by_subject"]["biochemistry"]["blocked_before_option_check"] == 1
    assert report["unassigned_or_outside_year_choice_starts"]["biochemistry"] == 1
