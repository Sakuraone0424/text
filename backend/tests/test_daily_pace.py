"""Writing/review money is spread over the month; when today's allowance is used the jobs wait until Beijing midnight."""
from decimal import Decimal

import pytest

from app import ai_jobs, cost_control as cc
from test_cost_control import spend, caps  # noqa: F401
from test_ai_automation import setup_ai  # noqa: F401

AT = cc.datetime.fromisoformat("2026-11-10T04:00:00+00:00")     # Beijing 12:00 on the 10th of a 30-day month: 21 days left


@pytest.fixture(autouse=True)
def off_peak(monkeypatch, db, setup_ai):
    from app import model_router

    model_router.configure(db, setup_ai, "deepseek-flash", "high", stopped=False, daily_cny=500, monthly_cny=500, task_cny=500, trial_cny=500)
    monkeypatch.setattr(cc, "tomorrow_short", lambda conn, uid: False)
    monkeypatch.setattr(cc, "is_peak", lambda at=None, calendar=None: False)
    monkeypatch.setattr(cc, "_day_numbers", lambda at=None: (AT.astimezone(cc.cny_billing.SHANGHAI), 21, 30))


def test_today_s_allowance_is_what_is_left_for_writing_divided_by_the_days_left(db, setup_ai, caps):
    p = cc.pace(db, setup_ai, short=False)
    # nothing spent: (90 cap - 15 unused grading reserve) / 21 days
    assert Decimal(p["quota_cny"]) == (Decimal(75) / 21).quantize(Decimal("0.0001"))
    assert p["allowed_cny"] == p["quota_cny"] and not p["exceeded"] and p["lasts_to_month_end"]


def test_the_allowance_is_fixed_for_the_day_what_was_spent_today_does_not_shrink_it(db, setup_ai, caps):
    quota = cc.pace(db, setup_ai, short=False)["quota_cny"]
    spend(db, setup_ai, "generation", "1.00")
    p = cc.pace(db, setup_ai, short=False)
    assert p["quota_cny"] == quota and Decimal(p["used_cny"]) == Decimal("1.0000")


def test_when_today_s_allowance_is_spent_writing_and_review_wait_but_grading_runs(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "4.00")                       # allowance is 3.57
    assert cc.pace_exhausted(db, setup_ai)
    ai_jobs.enqueue(db, setup_ai, "w", "generation", {"instructions": "x"})
    ai_jobs.enqueue(db, setup_ai, "r", "quality_review", {"instructions": "x"})
    assert ai_jobs.claim(db) is None
    assert [r["state"] for r in db.execute("SELECT state FROM ai_jobs WHERE job_key IN ('w','r') ORDER BY job_key")] == ["pending", "pending"]
    assert ai_jobs.LAST_CLAIM["held_back"] == "daily_quota"
    ai_jobs.enqueue(db, setup_ai, "g", "grading", {"instructions": "x"})
    assert ai_jobs.claim(db)["kind"] == "grading"


def test_tomorrows_paper_that_cannot_be_put_together_allows_50_percent_more(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "4.00")
    p = cc.pace(db, setup_ai, short=True)
    assert Decimal(p["allowed_cny"]) == (Decimal(p["quota_cny"]) * Decimal("1.5")).quantize(Decimal("0.0001"))
    assert p["exception"] and not p["exceeded"]                     # 4.00 < 5.36
    spend(db, setup_ai, "generation", "2.00")
    assert cc.pace(db, setup_ai, short=True)["exceeded"]            # even the exception has an end


def test_a_new_day_starts_with_a_fresh_allowance(db, setup_ai, caps, monkeypatch):
    spend(db, setup_ai, "generation", "4.00")
    monkeypatch.setattr(cc, "DAY_START", "(clock_timestamp() + interval '1 day')")      # tomorrow: nothing has been spent yet
    p = cc.pace(db, setup_ai, short=False)
    assert Decimal(p["used_cny"]) == 0 and not p["exceeded"]


def test_the_projection_says_on_which_day_the_months_money_runs_out(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "40.00")                      # 40 in 9.5 days -> 4.2/day; 35 left -> about 8 more days
    p = cc.pace(db, setup_ai, short=False)
    assert not p["lasts_to_month_end"] and p["runs_out_on"].startswith("2026-11-1")
    assert p["runs_out_on"] < "2026-11-30"


def test_a_used_up_month_is_the_monthly_caps_business_not_the_pace(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "75")
    assert not cc.pace_exhausted(db, setup_ai)


def test_the_switch_turns_the_pace_off(db, setup_ai, caps, monkeypatch):
    spend(db, setup_ai, "generation", "4.00")
    monkeypatch.setenv("AI_DAILY_PACE", "0")
    ai_jobs.enqueue(db, setup_ai, "w", "generation", {"instructions": "x"})
    assert ai_jobs.claim(db)["job_key"] == "w"


def test_the_progress_report_and_page_carry_the_pace(db, setup_ai, caps):
    from app import progress
    from pathlib import Path

    report = progress.report(db, setup_ai)
    assert {"quota_cny", "allowed_cny", "used_cny", "exceeded", "runs_out_on", "lasts_to_month_end"} <= set(report["pace"])
    js = Path(progress.__file__).parent.joinpath("static/progress.js").read_text()
    html = Path(progress.__file__).parent.joinpath("static/training.html").read_text()
    assert "今日出题额度 已用" in js and "按当前速度，本月额度可用到" in js and 'id="pace-note"' in html
