"""PR: enough queued work for every call a run can make; exam date; the estimate follows the recent rate."""
import threading
import time
from datetime import datetime, timezone

import pytest

from app import auto_generation as gen, auto_worker, cost_control, ai_jobs, learning_stats, progress
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_gap_generation import corpus, patch_worker, generation_jobs  # noqa: F401


def test_the_exam_is_on_december_19():
    from datetime import date

    assert learning_stats.EXAM_DATE == date(2026, 12, 19)
    js = open(learning_stats.__file__.replace("learning_stats.py", "static/insights.js"), encoding="utf-8").read()
    html = open(learning_stats.__file__.replace("learning_stats.py", "static/training.html"), encoding="utf-8").read()
    assert "距 12 月 19 日初试" in js and "距 12 月 19 日初试" in html
    assert "12 月 21 日" not in js + html


# ---- planning ahead -----------------------------------------------------------------------------------------

def test_the_queue_is_filled_to_twice_the_calls_per_run(db, corpus, setup_ai):
    before = gen.claimable_count(db)
    queued = gen.fill_queue(db, setup_ai, 16)
    assert queued >= 10 and gen.claimable_count(db) >= 16 - 0 and gen.claimable_count(db) - before == queued
    assert gen.fill_queue(db, setup_ai, 16) == 0  # already full: nothing more is queued


def test_planning_never_goes_beyond_what_the_bank_is_short_of(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setenv("AI_BANK_TARGET_PER_BUCKET", "1")  # ten cells, one question each: the gaps add up to 10
    gaps = sum(g for _, _, g in gen.bucket_gaps(db, setup_ai))
    assert gaps == 10
    queued = gen.fill_queue(db, setup_ai, 16)
    assert queued <= gaps and sum(g for _, _, g in gen.bucket_gaps(db, setup_ai)) == gaps - queued
    assert gen.fill_queue(db, setup_ai, 16) == 0 and len(generation_jobs(db)) == queued


def test_planning_needs_no_money_up_front_and_stops_at_one_when_the_budget_is_used_up(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setattr(cost_control, "spend_report", lambda conn, uid: {"writing_review_exhausted": True})
    assert gen.fill_queue(db, setup_ai, 16) == 1   # the old single-request rule
    assert db.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"] == 0   # queued jobs reserve nothing


def test_the_run_plans_first_and_logs_planned(db, corpus, setup_ai, monkeypatch, capsys):
    patch_worker(db, monkeypatch)
    monkeypatch.setenv("AI_CALLS_PER_RUN", "3")
    auto_worker.main()
    line = capsys.readouterr().out
    planned = int(line.split(" planned=")[1].split()[0])
    assert planned >= 6 and gen.claimable_count(db) >= 6   # twice the calls per run


def test_the_queue_is_not_overfilled_when_autoplan_is_off(db, corpus, setup_ai, monkeypatch, capsys):
    patch_worker(db, monkeypatch)
    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "0")
    auto_worker.main()
    assert " planned=0" in capsys.readouterr().out


# ---- the pool keeps working while a result may still create a review ------------------------------------------

def test_an_idle_worker_waits_for_a_running_call_whose_result_creates_new_work(monkeypatch):
    queue = [1]
    lock = threading.Lock()
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)
    handed = []

    def fake_dispatch(conn, **kw):
        with lock:
            if not handed:
                handed.append(1)
                queue.append(2)  # finishing the writing call created its review

    monkeypatch.setattr(auto_worker, "dispatch_completed", fake_dispatch)

    def run_once(get_db, executor, **kw):
        with lock:
            job = queue.pop(0) if queue else None
        if job is None:
            ai_jobs.LAST_CLAIM.clear()
            ai_jobs.LAST_CLAIM.update(outcome="no_claimable_job")
            return False
        ai_jobs.LAST_CLAIM.clear()
        ai_jobs.LAST_CLAIM.update(outcome="claimed", job_id=job, kind="generation")
        executor({"id": job})
        return True

    monkeypatch.setattr(ai_jobs, "run_once", run_once)

    class Nothing:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    pool = auto_worker.CallPool(lambda: Nothing(), lambda job: time.sleep(0.3), 2, 4, 240).run()
    assert pool.calls == 2 and pool.stopped_because == "no_claimable_job"   # the review was claimed in the same run
    assert not pool.errors


def test_an_empty_queue_still_ends_the_run_promptly(monkeypatch):
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)
    monkeypatch.setattr(ai_jobs, "run_once", lambda *a, **k: (ai_jobs.LAST_CLAIM.update(outcome="no_claimable_job"), False)[1])

    class Nothing:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    began = time.monotonic()
    pool = auto_worker.CallPool(lambda: Nothing(), lambda job: None, 4, 8, 240).run()
    assert pool.calls == 0 and time.monotonic() - began < 3 and pool.stopped_because == "no_claimable_job"


# ---- the estimate follows the recent rate -----------------------------------------------------------------------

SUNDAY_EVENING = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)  # Sunday 20:00 Beijing, all recent hours off-peak
MONDAY_10 = datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)        # Monday 10:00 Beijing: peak now, peak and night before


def test_the_rate_window_is_the_last_six_off_peak_hours():
    assert progress.trailing_window(SUNDAY_EVENING) == (6.0, 6.0)
    back, off = progress.trailing_window(MONDAY_10)
    assert off == 6.0 and back > 6.0   # peak time inside the window is skipped, not counted
    short_back, short_off = progress.trailing_window(SUNDAY_EVENING, limit_hours=2)
    assert short_off == 2.0   # not enough history: the data there is is used


def test_twelve_recent_approvals_in_six_off_peak_hours_set_the_estimate_not_the_two_day_average(db, setup_ai, monkeypatch):
    seen = {}

    def approved(conn, hours=None):
        seen["hours"] = hours
        return 24   # 24 approved in the last 6 off-peak hours = four per hour

    monkeypatch.setattr(progress, "approved_recently", approved)
    rows = progress.buckets(db, setup_ai)
    paper = progress.next_paper(db, rows, SUNDAY_EVENING)
    assert seen["hours"] == 6.0 and paper["per_off_peak_hour"] == 4.0
    assert paper["eta_state"] == "estimated" and paper["eta_hours"] < 48   # 74 missing at 4/hour = 19 off-peak hours
    # a faster rate right after raising concurrency shows up at once
    monkeypatch.setattr(progress, "approved_recently", lambda conn, hours=None: 48)
    faster = progress.next_paper(db, rows, SUNDAY_EVENING)
    assert faster["eta_hours"] < paper["eta_hours"] and faster["per_off_peak_hour"] == 8.0
