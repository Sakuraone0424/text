"""V2 is proposed only; these tests must not alter active v1 training."""

import copy

import pytest

from app import training_spec as legacy
from app import training_spec_v2 as v2


def sample_items():
    rows = []
    for subject in v2.SUBJECTS:
        for kind, count in v2.COUNTS.items():
            for i in range(count):
                rows.append(
                    {
                        "subject": subject,
                        "question_type": kind,
                        "max_points": v2.POINTS[kind],
                        "rubric": [
                            {"description": "synthetic scoring criterion", "points": v2.POINTS[kind]}
                        ],
                        "stem": f"synthetic {subject} {kind} {i}",
                    }
                )
    return rows


def test_v2_exact_blueprint_and_symmetric_scoring():
    blueprint = v2.default_blueprint()
    assert v2.validate_blueprint(blueprint) == blueprint
    spec = v2.formal_specification()
    assert spec["questions"] == 60
    assert spec["points_total"] == 150
    assert spec["questions_per_subject"] == 30
    assert spec["points_per_subject"] == 75
    assert spec["duration_seconds"] == 3600
    assert sum(v2.COUNTS[k] * v2.POINTS[k] for k in v2.COUNTS) == 75
    assert sum(v2.COUNTS.values()) == 30
    assert len(v2.count_required_bank()) == 8
    assert v2.validate_items(sample_items())


@pytest.mark.parametrize("kind", ["single_choice", "fill_blank", "term", "short_answer"])
def test_v2_each_type_is_present_in_both_subjects(kind):
    rows = sample_items()
    assert all(
        sum(r["question_type"] == kind and r["subject"] == subject for r in rows)
        == v2.COUNTS[kind]
        for subject in v2.SUBJECTS
    )


@pytest.mark.parametrize("mutation", ["count", "essay", "mismatched_subject", "points", "rubric"])
def test_v2_rejects_invalid_papers(mutation):
    rows = sample_items()
    if mutation == "count":
        rows.pop()
    elif mutation == "essay":
        rows[0]["question_type"] = "essay"
    elif mutation == "mismatched_subject":
        rows[0]["subject"] = "cell_biology"
    elif mutation == "points":
        rows[0]["max_points"] = 3
    elif mutation == "rubric":
        rows[0]["rubric"] = [{"description": "wrong", "points": 1}]
    with pytest.raises(ValueError):
        v2.validate_items(rows)


@pytest.mark.parametrize("mutation", ["specification", "duration", "quota", "point", "essay", "extra_field"])
def test_v2_rejects_invalid_blueprints(mutation):
    spec = copy.deepcopy(v2.default_blueprint())
    if mutation == "specification":
        spec["specification"] = "xiaohan-150-60-v1"
    elif mutation == "duration":
        spec["duration_seconds"] = 4500
    elif mutation == "quota":
        spec["quotas"]["cell_biology"]["single_choice"] = 19
    elif mutation == "point":
        spec["points"]["fill_blank"] = 3
    elif mutation == "essay":
        spec["quotas"]["biochemistry"]["essay"] = 1
    elif mutation == "extra_field":
        spec["mode"] = "unreviewed"
    with pytest.raises(ValueError):
        v2.validate_blueprint(spec)


def test_v1_is_unmodified_and_v2_never_regrades_it():
    v1 = legacy.formal_specification()
    assert v1["questions"] == 28
    assert v1["points"] == 150
    assert v1["questions_per_subject"] == 14
    assert v1["points_per_subject"] == 75
    assert v1["points_by_type"]["fill_blank"] == 3
    assert v1["points_by_type"]["short_answer"] == 8
    with pytest.raises(ValueError):
        v2.validate_items([{"subject": "biochemistry"}] * 28)
