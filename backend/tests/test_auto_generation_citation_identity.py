"""Fail-closed deterministic repairs of generated source citations.

Synthetic fixtures only; never certify real textbook text or exam answers.
"""
from copy import deepcopy

from app.auto_generation import repair_source_identity_from_locked_units
from app.auto_quality import evidence as _independent_science_gate


def _unit(**changes):
    unit = dict(
        unit_id=17,
        source_kind="textbook",
        subject="biochemistry",
        file_id="biochemistry_upper",
        source_sha256="a" * 64,
        ocr_page=32,
        chapter="Chapter 3",
        text="The original synthetic enzyme requires ATP for this reaction.",
        quality_flags=[],
    )
    unit.update(changes)
    return unit


def _draft(**changes):
    draft = dict(
        stem="Which reagent is necessary for this reaction?",
        answer="B",
        rubric=[{"description": "synthetic grading", "points": 2}],
        evidence=[dict(
            source_unit_id=17,
            excerpt="enzyme requires ATP",
            file_id="hallucinated-book",
            source_sha256="b" * 64,
            paragraph_id=999,
            ocr_page=998,
            chapter="invented chapter",
            locator="invented print page",
        )],
    )
    draft.update(changes)
    return draft


def test_repairs_only_identity_from_literal_locked_snapshot():
    raw = _draft()
    old = deepcopy(raw)
    repaired = repair_source_identity_from_locked_units(raw, [_unit()])
    evidence = repaired["evidence"][0]
    assert evidence["file_id"] == "biochemistry_upper"
    assert evidence["source_sha256"] == "a" * 64
    assert evidence["paragraph_id"] == 17
    assert evidence["ocr_page"] == 32
    assert evidence["chapter"] == "Chapter 3"
    assert evidence["locator"] == "OCR:32:UNIT:17"
    assert evidence["excerpt"] == "enzyme requires ATP"
    assert raw == old  # Original model receipt remains unchanged
    assert repaired["stem"] == raw["stem"]
    assert repaired["answer"] == raw["answer"]
    assert repaired["rubric"] == raw["rubric"]


def test_no_repair_when_excerpt_was_invented():
    raw = _draft(evidence=[{**_draft()["evidence"][0], "excerpt": "This passage was invented"}])
    assert repair_source_identity_from_locked_units(raw, [_unit()]) is raw


def test_no_repair_when_source_id_not_requested():
    raw = _draft(evidence=[{**_draft()["evidence"][0], "source_unit_id": 18}])
    assert repair_source_identity_from_locked_units(raw, [_unit()]) is raw


def test_no_repair_if_ocr_risk_flags_present():
    raw = _draft()
    assert repair_source_identity_from_locked_units(
        raw, [_unit(quality_flags=["formula_unverified"])]
    ) is raw


def test_no_repair_if_source_is_not_textbook():
    raw = _draft()
    assert repair_source_identity_from_locked_units(
        raw, [_unit(source_kind="exam_transcription")]
    ) is raw


def test_all_citations_require_exact_quotes_not_just_one():
    raw = _draft(evidence=[
        _draft()["evidence"][0],
        {**_draft()["evidence"][0], "excerpt": "not in actual source"}
    ])
    assert repair_source_identity_from_locked_units(raw, [_unit()]) is raw
