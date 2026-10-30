"""The existing 20:00 automatic scheduler can prepare v2, but never silently fall back."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import automation, training_spec_v2 as v2
from app.training_spec import SPECIFICATION as V1_SPECIFICATION


def test_legacy_production_default_unchanged():
    plan = automation.selected_daily_blueprint({})
    assert plan.specification == V1_SPECIFICATION
    assert sum(plan.quotas["biochemistry"].values()) == 14


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


def test_legacy_paper_can_be_returned_without_new_spec_override():
    blueprint = automation.selected_daily_blueprint({})
    result = {"mode": "reviewed_28", "paper_id": 5}
    assert automation.verified_daily_paper_response(result, blueprint) is result
