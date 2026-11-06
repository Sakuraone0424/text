"""DeepSeek busy or unreachable (429/500/502/503/504, timeouts, broken connections) is not the task's fault: it does not use
up retries, never blocks, waits with exponential back-off, blocked jobs come back, concurrency adapts, the page says so."""
import subprocess

import httpx
import pytest

from app import ai_jobs as jobs, auto_worker, progress
from test_ai_automation import setup_ai  # noqa: F401


def fail(db, claim, error):
    return jobs.finish(db, claim, error=error)


def wait_over(db):
    db.execute("UPDATE ai_jobs SET next_retry=clock_timestamp()-interval '1 second' WHERE state='retrying'")


def job(db):
    return db.execute("SELECT * FROM ai_jobs").fetchone()


PLATFORM_ERRORS = [
    jobs.ProviderFailure(429), jobs.ProviderFailure(500), jobs.ProviderFailure(502), jobs.ProviderFailure(503),
    jobs.ProviderFailure(504),
    jobs.ProviderFailure(None),                                    # no HTTP answer at all (timeout / connection lost)
    subprocess.TimeoutExpired("harness", 150), TimeoutError(), ConnectionResetError(), httpx.ReadTimeout("x"),
    httpx.ConnectError("x"), httpx.RemoteProtocolError("x"),
]
NOT_PLATFORM = [
    jobs.ProviderFailure(400), jobs.ProviderFailure(401), jobs.ProviderFailure(402), jobs.ProviderFailure(403),
    jobs.ProviderFailure(404), jobs.ProviderFailure(200), ValueError("payload_exceeds_hard_cap"), RuntimeError("x"),
    jobs.ProviderFailure(502, diagnostic={"failure_stage": "output_truncated"}),    # the model's output, not the platform
]


@pytest.mark.parametrize("error", PLATFORM_ERRORS, ids=lambda e: type(e).__name__ + str(getattr(e, "status", "")))
def test_platform_trouble_is_recognised(error):
    assert jobs.is_platform_error(error)


@pytest.mark.parametrize("error", NOT_PLATFORM, ids=lambda e: type(e).__name__ + str(getattr(e, "status", "")))
def test_other_failures_are_not_platform_trouble(error):
    assert not jobs.is_platform_error(error)


def test_busy_platform_never_uses_a_retry_or_blocks_and_waits_longer_each_time(db, setup_ai):
    jobs.enqueue(db, setup_ai, "busy", "science_review", {})
    waits = []
    for n in range(8):                                            # far past the limit of 2 retries
        claim = jobs.claim(db)
        assert claim is not None, n
        assert fail(db, claim, jobs.ProviderFailure(503)) == "retrying"
        row = job(db)
        assert row["state"] == "retrying" and row["error_code"] == "provider_http_503"
        waits.append(round(db.execute("SELECT EXTRACT(EPOCH FROM %s-clock_timestamp()) AS s", (row["next_retry"],)).fetchone()["s"] / 60))
        wait_over(db)
    assert waits == [1, 2, 5, 10, 30, 30, 30, 30]                  # 1, 2, 5, 10, then 30 minutes
    row = job(db)
    assert row["attempts"] == 8 and row["transient_attempts"] == 8 and row["platform_streak"] == 8


def test_the_job_is_not_claimable_until_its_wait_is_over(db, setup_ai):
    jobs.enqueue(db, setup_ai, "wait", "science_review", {})
    fail(db, jobs.claim(db), jobs.ProviderFailure(429))
    assert jobs.claim(db) is None                                  # waiting 1 minute
    wait_over(db)
    assert jobs.claim(db) is not None


def test_its_own_failures_still_count_and_block_after_the_limit(db, setup_ai):
    jobs.enqueue(db, setup_ai, "own", "science_review", {})
    limit = job(db)["settings_snapshot"]["retry_limit"]
    states = []
    for _ in range(limit + 1):
        claim = jobs.claim(db)
        states.append(fail(db, claim, RuntimeError("x")))
        wait_over(db)
    assert states == ["retrying"] * limit + ["blocked"]


def test_busy_periods_do_not_eat_into_the_tasks_own_retries(db, setup_ai):
    jobs.enqueue(db, setup_ai, "mixed", "science_review", {})
    limit = job(db)["settings_snapshot"]["retry_limit"]
    for _ in range(5):                                             # DeepSeek is busy five times
        fail(db, jobs.claim(db), jobs.ProviderFailure(503))
        wait_over(db)
    states = []
    for _ in range(limit + 1):                                     # then the task's real failures: the full allowance
        states.append(fail(db, jobs.claim(db), RuntimeError("x")))
        wait_over(db)
    assert states == ["retrying"] * limit + ["blocked"]
    assert job(db)["transient_attempts"] == 5


def test_a_run_of_ordinary_failures_after_a_busy_one_resets_the_back_off(db, setup_ai):
    jobs.enqueue(db, setup_ai, "reset", "science_review", {})
    for _ in range(3):
        fail(db, jobs.claim(db), jobs.ProviderFailure(503))
        wait_over(db)
    fail(db, jobs.claim(db), RuntimeError("x"))
    assert job(db)["platform_streak"] == 0


def test_a_lost_lease_is_judged_by_the_tasks_own_attempts(db, setup_ai):
    jobs.enqueue(db, setup_ai, "lease", "science_review", {})
    for _ in range(4):
        fail(db, jobs.claim(db), jobs.ProviderFailure(504))
        wait_over(db)
    claim = jobs.claim(db)
    db.execute("UPDATE ai_jobs SET lease_until=clock_timestamp()-interval '1 minute' WHERE id=%s", (claim["id"],))
    assert jobs.claim(db) is not None or job(db)["state"] == "retrying"      # not blocked: 5 attempts, 4 of them platform's
    assert job(db)["state"] != "blocked"


# ---- 2. blocked earlier because of platform trouble: back in the queue -------------------------------------------
def test_jobs_blocked_by_platform_trouble_are_requeued_with_a_fresh_budget(db, setup_ai):
    jobs.enqueue(db, setup_ai, "old-block", "science_review", {})
    jobs.enqueue(db, setup_ai, "real-block", "science_review", {"x": 1})
    jobs.enqueue(db, setup_ai, "budget", "science_review", {"x": 2})
    ids = [r["id"] for r in db.execute("SELECT id FROM ai_jobs ORDER BY id").fetchall()]
    db.execute("UPDATE ai_jobs SET state='blocked',attempts=3,error_code='provider_http_503' WHERE id=%s", (ids[0],))
    db.execute("UPDATE ai_jobs SET state='blocked',attempts=3,error_code='ValueError:oversized_harness_response' WHERE id=%s", (ids[1],))
    db.execute("UPDATE ai_jobs SET state='blocked',attempts=3,error_code='TimeoutExpired' WHERE id=%s", (ids[2],))
    assert jobs.requeue_platform_blocked(db) == 2
    rows = {r["id"]: r for r in db.execute("SELECT * FROM ai_jobs").fetchall()}
    assert rows[ids[0]]["state"] == "retrying" and rows[ids[2]]["state"] == "retrying"
    assert rows[ids[1]]["state"] == "blocked"                                        # a real failure stays blocked
    assert rows[ids[0]]["transient_attempts"] == 3 and rows[ids[0]]["attempts"] - rows[ids[0]]["transient_attempts"] == 0
    assert jobs.claim(db) is not None                                                # and it can run again at once


# ---- 3. adaptive concurrency --------------------------------------------------------------------------------------
def beat(db, calls, b503=0, b429=0, effective=2):
    db.execute(
        "INSERT INTO worker_heartbeats(started_at,outcome,calls,busy_503,busy_429,effective_concurrency) "
        "VALUES (clock_timestamp(),'ok',%s,%s,%s,%s)", (calls, b503, b429, effective))


def test_no_history_means_the_setting(db):
    assert auto_worker.adaptive_concurrency(db, 4) == (4, "normal")


def test_more_than_half_busy_halves_the_next_run_down_to_one(db):
    beat(db, calls=4, b503=2, b429=1, effective=4)
    assert auto_worker.adaptive_concurrency(db, 4) == (2, "halved")
    beat(db, calls=2, b503=2, effective=2)
    assert auto_worker.adaptive_concurrency(db, 4) == (1, "halved")
    beat(db, calls=1, b503=1, effective=1)
    assert auto_worker.adaptive_concurrency(db, 4) == (1, "halved")                  # never below 1


def test_exactly_half_is_not_more_than_half(db):
    beat(db, calls=4, b503=2, effective=4)
    assert auto_worker.adaptive_concurrency(db, 4) == (4, "normal")


def test_two_ordinary_runs_in_a_row_bring_it_back_one_step_at_a_time(db):
    beat(db, calls=4, b503=4, effective=4)
    assert auto_worker.adaptive_concurrency(db, 4)[0] == 2
    beat(db, calls=3, effective=2)                                                   # one good run: hold
    assert auto_worker.adaptive_concurrency(db, 4) == (2, "holding")
    beat(db, calls=3, effective=2)                                                   # two good runs: climb
    assert auto_worker.adaptive_concurrency(db, 4) == (3, "recovering")
    beat(db, calls=3, effective=3)
    assert auto_worker.adaptive_concurrency(db, 4) == (4, "recovering")
    beat(db, calls=3, effective=4)
    assert auto_worker.adaptive_concurrency(db, 4) == (4, "normal")


def test_a_busy_run_between_good_ones_restarts_the_count_and_idle_runs_do_not_count(db):
    beat(db, calls=4, b503=4, effective=4)
    beat(db, calls=0, effective=2)                                                   # nothing was called: no information
    assert auto_worker.adaptive_concurrency(db, 4)[0] == 2
    beat(db, calls=3, effective=2)
    beat(db, calls=2, b429=2, effective=2)
    assert auto_worker.adaptive_concurrency(db, 4) == (1, "halved")


def test_lowering_the_setting_wins_over_a_remembered_higher_number(db):
    beat(db, calls=3, effective=4)
    assert auto_worker.adaptive_concurrency(db, 2) == (2, "normal")


# ---- 5. the run log and the heartbeat ----------------------------------------------------------------------------
def test_trouble_counts_per_run_come_from_the_calls_of_that_run_only(db, setup_ai):
    jobs.enqueue(db, setup_ai, "count", "science_review", {})
    since = db.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    for error in (jobs.ProviderFailure(503), jobs.ProviderFailure(503), jobs.ProviderFailure(429), TimeoutError(), jobs.ProviderFailure(502)):
        fail(db, jobs.claim(db), error)
        wait_over(db)
    assert auto_worker.platform_trouble(db, since) == (2, 1, 1)                       # 503 x2, 429 x1, timeouts x1 (the 502 is neither)
    later = db.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    assert auto_worker.platform_trouble(db, later) == (0, 0, 0)


def test_the_heartbeat_row_keeps_the_counts_and_the_concurrency_used(db):
    report = {"enabled": "yes", "calls": 4, "http_503": 3, "http_429": 1, "timeouts": 2, "concurrency": 2, "duration_s": "240.0"}
    from contextlib import contextmanager

    @contextmanager
    def same():
        with db.transaction():
            yield db

    import app.auto_worker as w
    original = w.get_db
    w.get_db = same
    try:
        w.record_heartbeat(report, db.execute("SELECT clock_timestamp() AS t").fetchone()["t"], "")
    finally:
        w.get_db = original
    row = db.execute("SELECT * FROM worker_heartbeats").fetchone()
    assert (row["busy_503"], row["busy_429"], row["timeouts"], row["effective_concurrency"]) == (3, 1, 2, 2)


# ---- 4. what the page says ---------------------------------------------------------------------------------------
def test_progress_marks_busy_waiting_jobs_apart_from_failures(db, setup_ai):
    jobs.enqueue(db, setup_ai, "ui", "science_review", {})
    fail(db, jobs.claim(db), jobs.ProviderFailure(503))
    listed = progress.recent_jobs(db)[0]
    assert listed["platform_busy"] is True and listed["attempts"] == 0 and listed["state"] == "retrying"
    status = progress.platform_status(db)
    assert status["busy"] is True and status["waiting_jobs"] == 1 and status["next_retry_at"] is not None
    # a real failure waiting to retry is not "busy"
    db.execute("UPDATE ai_jobs SET error_code='RuntimeError'")
    assert progress.recent_jobs(db)[0]["platform_busy"] is False
    assert progress.platform_status(db)["busy"] is False


def test_the_last_run_being_mostly_busy_also_marks_the_platform_busy(db):
    beat(db, calls=4, b503=3, effective=2)
    status = progress.platform_status(db)
    assert status["busy"] is True and status["last_run"]["busy_503"] == 3


def test_the_home_card_data_carries_the_busy_flag(db):
    from app.common import ensure_user
    from app.learning_stats import tomorrow_ready

    beat(db, calls=2, b503=2)
    assert tomorrow_ready(db, ensure_user(db, "owner"))["platform_busy"] is True


def test_the_page_texts_exist():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "progress.js").read_text()
    for text in ("DeepSeek 繁忙，自动等待重试", "provider_http_503", "platform-note", "并发已降到"):
        assert text in js, text
    home = (Path(__file__).resolve().parents[1] / "app" / "static" / "insights.js").read_text()
    assert "DeepSeek 繁忙，出题可能变慢" in home
