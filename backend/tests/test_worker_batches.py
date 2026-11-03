"""One worker run, several batches: after a batch the run plans and claims the next while it is young enough (3 min 30 s),
never starts a call later, ends before 4 min 30 s, leaves at once when there is nothing to do, and never overlaps another run.
Fake clocks, fake pools and fake jobs; no request is ever sent."""
import os
import threading
import time
from types import SimpleNamespace

import psycopg
import pytest

from app import ai_jobs, auto_worker
from test_ai_automation import setup_ai  # noqa: F401
from test_parallel_worker import Nothing, fake_run_once
from test_worker_status import enqueue, status, worker  # noqa: F401


class FakePool:
    """Stands in for CallPool: each batch takes `seconds` on the fake clock and ends as scripted."""

    created = []

    def __init__(self, get_db, executor, workers, limit, seconds, clock=None, start_cutoff=None):
        self.limit, self.seconds, self.start_cutoff = limit, seconds, start_cutoff
        self.errors, self.claimed, self.peak, self.overran = [], [], 1, False
        self.stopped_because, self.last_claim, self.calls = None, {}, 0
        FakePool.created.append(self)

    def run(self):
        step = FakePool.script.pop(0)
        FakePool.clock[0] += step.get("takes", 60)
        self.calls = step.get("calls", self.limit)
        self.claimed = list(range(len(FakePool.created) * 10, len(FakePool.created) * 10 + self.calls))
        self.stopped_because = step.get("stopped")
        self.last_claim = step.get("claim", {})
        self.overran = step.get("overran", False)
        return self


@pytest.fixture
def scripted(worker, monkeypatch):
    FakePool.created, FakePool.clock = [], [0.0]
    FakePool.script = []
    monkeypatch.setattr(auto_worker, "CallPool", FakePool)
    monkeypatch.setattr(auto_worker, "time", SimpleNamespace(monotonic=lambda: FakePool.clock[0], sleep=time.sleep))
    monkeypatch.setattr(auto_worker, "platform_trouble", lambda conn, since: (0, 0, 0))
    monkeypatch.setattr(auto_worker, "adaptive_concurrency", lambda conn, configured: (configured, "normal"))
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)
    monkeypatch.setenv("AI_CALLS_PER_RUN", "3")
    return worker


def run_log(capsys):
    auto_worker.main()
    return status(capsys)


def test_batch_follows_batch_until_the_queue_is_empty(scripted, capsys):
    FakePool.script = [dict(), dict(), dict(calls=1, stopped="no_claimable_job")]
    log = run_log(capsys)
    assert log["batches"] == "3" and log["calls"] == "7" and log["exit_reason"] == "queue_empty"
    assert len(FakePool.created) == 3


def test_every_batch_gets_the_time_that_is_left(scripted, capsys):
    FakePool.script = [dict(takes=100), dict(takes=60), dict(calls=0, stopped="no_claimable_job")]
    run_log(capsys)
    first, second, third = FakePool.created
    assert (first.seconds, first.start_cutoff) == (270, 210)                 # the whole run: hard end 4:30, no new call after 3:30
    assert (second.seconds, second.start_cutoff) == (170, 110)               # 100 s have passed
    assert (third.seconds, third.start_cutoff) == (110, 50)                  # 160 s have passed


def test_no_new_batch_starts_after_three_and_a_half_minutes(scripted, capsys):
    FakePool.script = [dict(takes=120), dict(takes=120), dict(), dict()]      # the second batch ends at 240 s
    log = run_log(capsys)
    assert log["batches"] == "2" and log["exit_reason"] == "time_cutoff"
    assert len(FakePool.created) == 2                                          # a third was never created


def test_a_batch_that_starts_just_before_the_cutoff_is_still_run_but_limited(scripted, capsys):
    FakePool.script = [dict(takes=200), dict(takes=60), dict()]               # 200 s < 210 s: another batch may start
    log = run_log(capsys)
    assert log["batches"] == "2" and log["exit_reason"] == "time_cutoff"
    assert FakePool.created[1].start_cutoff == 10                              # ...but it may only start calls for 10 more seconds


@pytest.mark.parametrize("script,reason", [
    (dict(calls=1, stopped="no_claimable_job", claim=dict(held_back="generation,quality_review")), "peak_hours"),
    (dict(calls=1, stopped="no_claimable_job", claim=dict(held_back="")), "queue_empty"),
    (dict(calls=1, stopped="blocked:budget_exceeded:daily"), "budget_exhausted"),
    (dict(calls=0, stopped="blocked:budget_exceeded"), "budget_exhausted"),
    (dict(calls=1, stopped="blocked:automation_stopped"), "automation_stopped"),
    (dict(calls=1, stopped="run_time_budget"), "time_cutoff"),
    (dict(calls=2, overran=True), "overran"),
])
def test_the_run_ends_at_once_for_each_reason_to_stop(scripted, capsys, script, reason):
    FakePool.script = [dict(script), dict(), dict()]
    log = run_log(capsys)
    assert log["exit_reason"] == reason and len(FakePool.created) == 1 and log["batches"] == str(1 if script["calls"] else 0)


def test_most_calls_answered_503_or_429_ends_the_run(scripted, capsys, monkeypatch):
    monkeypatch.setattr(auto_worker, "platform_trouble", lambda conn, since: (2, 1, 0))     # 3 of 3 calls
    FakePool.script = [dict(), dict()]
    log = run_log(capsys)
    assert log["exit_reason"] == "platform_busy" and len(FakePool.created) == 1


def test_half_busy_is_not_yet_busy(scripted, capsys, monkeypatch):
    monkeypatch.setattr(auto_worker, "platform_trouble", lambda conn, since: (1, 0, 0))     # 1 of 3 calls, not more than half
    FakePool.script = [dict(), dict(calls=0, stopped="no_claimable_job")]
    assert run_log(capsys)["exit_reason"] == "queue_empty"


def test_the_batches_are_capped(scripted, capsys):
    FakePool.script = [dict(takes=1) for _ in range(20)]
    log = run_log(capsys)
    assert log["batches"] == str(auto_worker.MAX_BATCHES) and log["exit_reason"] == "max_batches"


def test_each_next_batch_is_prepared_like_the_first(scripted, capsys, monkeypatch):
    planned = []
    monkeypatch.setattr(auto_worker, "_plan_queue", lambda conn, report, users, private, first: planned.append(first))
    FakePool.script = [dict(), dict(), dict(calls=0, stopped="no_claimable_job")]
    run_log(capsys)
    assert planned == [True, False, False]


def test_a_database_error_in_a_batch_still_reports_what_was_done(scripted, capsys, monkeypatch):
    class Broken(FakePool):
        def run(self):
            if len(FakePool.created) == 2:
                self.errors = [RuntimeError("db down")]
                return self
            return super().run()

    monkeypatch.setattr(auto_worker, "CallPool", Broken)
    FakePool.script = [dict()]
    with pytest.raises(SystemExit):
        auto_worker.main()
    out = [l for l in capsys.readouterr().out.splitlines() if l.startswith("worker-run ")][0]
    assert " batches=1 " in out and " calls=3 " in out and "exit_reason=error" in out


# ---- the real pool: no new call after the cutoff, the deadline is kept ---------------------------------------------

def test_the_real_pool_starts_no_call_after_the_cutoff_and_ignores_the_old_room_rule(monkeypatch):
    now = [0.0]

    def behaviour(executor):
        ai_jobs.LAST_CLAIM.clear()
        ai_jobs.LAST_CLAIM.update(outcome="claimed", job_id=1, kind="generation")
        now[0] += 45
        return True

    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)
    fake_run_once(monkeypatch, behaviour)
    pool = auto_worker.CallPool(lambda: Nothing(), lambda job: None, 1, 50, 270, clock=lambda: now[0], start_cutoff=100).run()
    assert pool.calls == 3 and pool.stopped_because == "run_time_budget"      # started at 0, 45, 90; 135 s is past the cutoff
    now[0] = 0
    FakePool_old = auto_worker.CallPool(lambda: Nothing(), lambda job: None, 1, 50, 240, clock=lambda: now[0]).run()
    assert FakePool_old.calls == 4                                              # without a cutoff the old rule (room for one more call) applies


def test_a_call_started_just_before_the_cutoff_is_given_up_at_the_hard_end(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)

    def behaviour(executor):
        ai_jobs.LAST_CLAIM.clear()
        ai_jobs.LAST_CLAIM.update(outcome="claimed", job_id=1, kind="generation")
        executor({"id": 1})
        return True

    fake_run_once(monkeypatch, behaviour)
    began = time.monotonic()
    pool = auto_worker.CallPool(lambda: Nothing(), lambda job: release.wait(30), 1, 5, 0.4, start_cutoff=0.3).run()
    release.set()
    assert pool.overran and time.monotonic() - began < 3                       # it did not wait for the stuck call
    assert pool.calls == 0 or pool.calls == 1


def test_the_defaults_are_three_and_a_half_and_four_and_a_half_minutes():
    assert auto_worker.start_cutoff_seconds({}) == 210 and auto_worker.run_seconds({}) == 270
    assert auto_worker.start_cutoff_seconds({"AI_START_CUTOFF_SECONDS": "5"}) == 30
    assert auto_worker.start_cutoff_seconds({"AI_START_CUTOFF_SECONDS": "9999"}) == 240
    assert auto_worker.start_cutoff_seconds({"AI_START_CUTOFF_SECONDS": "x"}) == 210
    assert auto_worker.run_seconds({"AI_RUN_SECONDS": "9999"}) == 270


# ---- one run at a time ---------------------------------------------------------------------------------------------

def test_a_second_worker_leaves_at_once_when_another_run_holds_the_lock(worker, capsys, monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    ran = []
    monkeypatch.setattr(auto_worker, "CallPool", lambda *a, **k: ran.append(1))
    monkeypatch.setattr(auto_worker, "tick", lambda: ran.append("tick"))
    with psycopg.connect(url) as other:
        other.execute("SELECT pg_advisory_xact_lock(%s)", (auto_worker.WORKER_RUN_LOCK,))     # the running worker
        before = worker.execute("SELECT count(*) AS n FROM worker_heartbeats").fetchone()["n"]
        auto_worker.main()
        other.rollback()
    log = status(capsys)
    assert log["skipped"] == "already_running" and log["exit_reason"] == "already_running" and log["batches"] == "0"
    assert ran == [] and "calls" not in log
    assert worker.execute("SELECT count(*) AS n FROM worker_heartbeats").fetchone()["n"] == before    # not a run: no heartbeat


def test_the_lock_is_a_transaction_lock_so_a_dead_run_cannot_keep_it(worker):
    """pg_try_advisory_xact_lock is dropped when the run's connection ends, however it ends (a crash, a kill, a restart)."""
    import inspect

    source = inspect.getsource(auto_worker.run)
    assert "pg_try_advisory_xact_lock" in source and "pg_advisory_lock(" not in source


# ---- real jobs through the whole loop ------------------------------------------------------------------------------

def test_three_one_call_batches_then_an_empty_queue(worker, setup_ai, capsys, monkeypatch):
    monkeypatch.setenv("AI_CALLS_PER_RUN", "1")
    for n in range(3):
        enqueue(worker, setup_ai, key=f"batch-{n}")
    auto_worker.main()
    log = status(capsys)
    assert log["batches"] == "3" and log["calls"] == "3" and log["exit_reason"] == "queue_empty"
    assert float(log["duration_s"]) < 60
