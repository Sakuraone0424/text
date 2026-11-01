"""The 20:00 automatic scheduler prepares v2 by default and never falls back to 28."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import automation, training_spec_v2 as v2
from app.training_spec import SPECIFICATION as V1_SPECIFICATION


@pytest.mark.parametrize("env", [{}, {"QUIZ_DAILY_SPECIFICATION": ""}])
def test_unset_specification_defaults_to_sixty_v2(env):
    plan = automation.selected_daily_blueprint(env)
    assert plan.specification == v2.SPECIFICATION
    for subject in v2.SUBJECTS:
        assert plan.quotas[subject] == {
            "single_choice": 18, "fill_blank": 7, "term": 4, "short_answer": 1,
        }
    assert plan.points == {"single_choice": 2, "fill_blank": 2, "term": 4, "short_answer": 9}
    assert sum(
        n * plan.points[k] for q in plan.quotas.values() for k, n in q.items()
    ) == 150


def test_retired_28q_specification_cannot_be_selected_for_new_papers():
    with pytest.raises(ValueError, match="unknown_daily_training_specification"):
        automation.selected_daily_blueprint({"QUIZ_DAILY_SPECIFICATION": V1_SPECIFICATION})


def test_explicit_new_daily_contract_uses_sixty_150_and_3600_seconds():
    blueprint = automation.selected_daily_blueprint({
        "QUIZ_DAILY_SPECIFICATION": v2.SPECIFICATION,
    })
    assert blueprint.specification == v2.SPECIFICATION
    assert blueprint.duration_seconds == 3600
    assert sum(blueprint.quotas["biochemistry"].values()) == 30
    assert sum(blueprint.quotas["cell_biology"].values()) == 30
    assert blueprint.points == v2.POINTS


def test_unknown_contract_never_silently_falls_back_to_28():
    with pytest.raises(ValueError, match="unknown_daily_training_specification"):
        automation.selected_daily_blueprint({"QUIZ_DAILY_SPECIFICATION": "xiaohan-bad-60"})


@pytest.mark.parametrize("changed", [
    {"mode": "reviewed_28"},
    {"count": 28},
    {"max_points": 120},
    {"blueprint": {"specification": V1_SPECIFICATION}},
])
def test_old_or_malformed_paper_cannot_be_marked_as_prepared_sixty(changed):
    blueprint = automation.selected_daily_blueprint({
        "QUIZ_DAILY_SPECIFICATION": v2.SPECIFICATION,
    })
    result = {
        "mode": "reviewed_60",
        "blueprint": {"specification": v2.SPECIFICATION},
        "count": 60,
        "max_points": 150,
    }
    result.update(changed)
    with pytest.raises(ValueError, match="daily_60q_paper_not_proven"):
        automation.verified_daily_paper_response(result, blueprint)


def test_good_v2_paper_is_retained_untouched():
    blueprint = automation.selected_daily_blueprint({
        "QUIZ_DAILY_SPECIFICATION": v2.SPECIFICATION,
    })
    result = {
        "mode": "reviewed_60",
        "blueprint": {"specification": v2.SPECIFICATION},
        "count": 60,
        "max_points": 150,
        "paper_id": 41,
    }
    assert automation.verified_daily_paper_response(result, blueprint) is result


def test_default_daily_plan_never_reports_an_old_28q_paper_as_success():
    blueprint = automation.selected_daily_blueprint({})
    result = {"mode": "reviewed_28", "paper_id": 5}
    with pytest.raises(ValueError, match="daily_60q_paper_not_proven"):
        automation.verified_daily_paper_response(result, blueprint)
