"""The 20:00 automatic scheduler prepares the two v5 papers by default and never falls back to an old paper."""

import pytest

from app import automation, training_spec_v5 as v5
from app.training_spec import SPECIFICATION as V1_SPECIFICATION


@pytest.mark.parametrize("env", [{}, {"QUIZ_DAILY_SPECIFICATION": ""}])
def test_unset_specification_prepares_both_papers(env):
    objective, subjective = automation.daily_blueprints(env)
    assert (objective.specification, subjective.specification) == (v5.OBJECTIVE_SPEC, v5.SUBJECTIVE_SPEC)
    for subject in v5.SUBJECTS:
        assert objective.quotas[subject] == {"single_choice": 15, "fill_blank": 15}
        assert subjective.quotas[subject] == {"term": 4, "short_answer": 2, "essay": 1}
    assert objective.points == {"single_choice": 3, "fill_blank": 2}
    assert subjective.points == {"term": 5, "short_answer": 15, "essay": 25}
    for plan in (objective, subjective):
        assert sum(n * plan.points[k] for q in plan.quotas.values() for k, n in q.items()) == 150
    assert automation.selected_daily_blueprint(env).specification == v5.OBJECTIVE_SPEC


def test_retired_28q_specification_cannot_be_selected_for_new_papers():
    with pytest.raises(ValueError, match="unknown_daily_training_specification"):
        automation.daily_blueprints({"QUIZ_DAILY_SPECIFICATION": V1_SPECIFICATION})


def test_explicit_new_daily_contract_uses_one_paper_and_3600_seconds():
    (blueprint,) = automation.daily_blueprints({"QUIZ_DAILY_SPECIFICATION": v5.OBJECTIVE_SPEC})
    assert blueprint.duration_seconds == 3600
    assert sum(blueprint.quotas["biochemistry"].values()) == 30
    assert blueprint.points == v5.OBJECTIVE.default_blueprint()["points"]


def test_unknown_contract_never_silently_falls_back_to_an_old_paper():
    with pytest.raises(ValueError, match="unknown_daily_training_specification"):
        automation.daily_blueprints({"QUIZ_DAILY_SPECIFICATION": "xiaohan-bad-60"})


@pytest.mark.parametrize("changed", [
    {"mode": "reviewed_28"},
    {"count": 28},
    {"max_points": 120},
    {"blueprint": {"specification": V1_SPECIFICATION}},
])
def test_old_or_malformed_paper_cannot_be_marked_as_prepared(changed):
    (blueprint,) = automation.daily_blueprints({"QUIZ_DAILY_SPECIFICATION": v5.OBJECTIVE_SPEC})
    result = {"mode": "reviewed_60", "blueprint": {"specification": v5.OBJECTIVE_SPEC}, "count": 60, "max_points": 150}
    result.update(changed)
    with pytest.raises(ValueError, match="daily_paper_not_proven"):
        automation.verified_daily_paper_response(result, blueprint)


def test_good_paper_is_retained_untouched():
    (blueprint,) = automation.daily_blueprints({"QUIZ_DAILY_SPECIFICATION": v5.SUBJECTIVE_SPEC})
    result = {"mode": "reviewed_60", "blueprint": {"specification": v5.SUBJECTIVE_SPEC}, "count": 14, "max_points": 150, "paper_id": 41}
    assert automation.verified_daily_paper_response(result, blueprint) is result


def test_default_daily_plan_never_reports_an_old_28q_paper_as_success():
    blueprint = automation.selected_daily_blueprint({})
    result = {"mode": "reviewed_28", "paper_id": 5}
    with pytest.raises(ValueError, match="daily_paper_not_proven"):
        automation.verified_daily_paper_response(result, blueprint)
