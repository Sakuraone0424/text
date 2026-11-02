"""PR C: parallel generation. Claims stay exact (budget, limits, one question at a time); the run stays short."""
import threading
import time
from decimal import Decimal

import pytest

from app import ai_jobs, auto_worker, progress
from test_ai_automation import setup_ai  # noqa: F401  (fixture)


def jobs(db, uid, n, kind="generation", payload=None):
    return [ai_jobs.enqueue(db, uid, f"par-{kind}-{i}", kind, payload(i) if payload else {"subject": "biochemistry", "question_type": "term", "n": i})
            for i in range(n)]


def claim(db):
    job = ai_jobs.claim(db)
    return job, dict(ai_jobs.LAST_CLAIM)


def set_limits(db, **values):
    for key, value in values.items():
        db.execute(f"UPDATE ai_settings SET {key}=%s", (value,))


# ---- claims made one after another see each other's reservations -----------------------------------------

def test_in_flight_calls_count_against_the_daily_budget(db, setup_ai):
    set_limits(db, max_concurrency=4, daily_cny=100, monthly_cny=100, task_cny=100)
    jobs(db, setup_ai, 4)
    first, _ = claim(db)
    amount = db.execute("SELECT amount_cny FROM ai_cny_reservations WHERE job_id=%s", (first["id"],)).fetchone()["amount_cny"]
    set_limits(db, daily_cny=amount * Decimal("2.5"))  # room for two calls, not three
    second, _ = claim(db)
    third, note = claim(db)
    assert second is not None and third is None and note["outcome"] == "blocked:budget_exceeded"


def test_in_flight_calls_count_against_the_monthly_budget(db, setup_ai):
    set_limits(db, max_concurrency=4, daily_cny=100, monthly_cny=100, task_cny=100)
    jobs(db, setup_ai, 3)
    first, _ = claim(db)
    amount = db.execute("SELECT amount_cny FROM ai_cny_reservations WHERE job_id=%s", (first["id"],)).fetchone()["amount_cny"]
    set_limits(db, monthly_cny=amount * Decimal("1.5"))
    second, note = claim(db)
    assert second is None and note["outcome"] == "blocked:budget_exceeded"


def test_the_per_task_limit_applies_to_each_job_not_to_the_sum(db, setup_ai):
    set_limits(db, max_concurrency=4, daily_cny=100, monthly_cny=100, task_cny=100)
    jobs(db, setup_ai, 3)
    first, _ = claim(db)
    amount = db.execute("SELECT amount_cny FROM ai_cny_reservations WHERE job_id=%s", (first["id"],)).fetchone()["amount_cny"]
    set_limits(db, task_cny=amount * Decimal("1.5"))  # one attempt per job fits, two would not
    assert claim(db)[0] is not None and claim(db)[0] is not None  # other jobs are not held back by the first


def test_max_concurrency_caps_the_calls_running_at_once(db, setup_ai):
    set_limits(db, max_concurrency=2, daily_cny=100, monthly_cny=100, task_cny=100)
    jobs(db, setup_ai, 3)
    assert claim(db)[0] and claim(db)[0]
    third, note = claim(db)
    assert third is None and note["outcome"] == "max_concurrency_reached" and note["running"] == 2 and note["limit"] == 2


def test_one_question_is_never_reviewed_by_two_calls_at_once(db, setup_ai):
    set_limits(db, max_concurrency=4, daily_cny=100, monthly_cny=100, task_cny=100)
    same = lambda i: {"question_id": 7, "content_sha256": "x", "n": i}
    first_job, second_job = jobs(db, setup_ai, 2, "quality_review", same)
    first, _ = claim(db)
    assert first["id"] == first_job["id"]
    second, note = claim(db)
    assert second is None and note["outcome"] == "no_claimable_job"
    db.execute("UPDATE ai_jobs SET state='succeeded' WHERE id=%s", (first["id"],))
    assert claim(db)[0]["id"] == second_job["id"]


def test_different_questions_are_claimed_side_by_side(db, setup_ai):
    set_limits(db, max_concurrency=4, daily_cny=100, monthly_cny=100, task_cny=100)
    jobs(db, setup_ai, 2, "quality_review", lambda i: {"question_id": 10 + i, "content_sha256": "x"})
    assert claim(db)[0] and claim(db)[0]


def test_each_thread_keeps_its_own_last_claim():
    ai_jobs.LAST_CLAIM.clear()
    ai_jobs.LAST_CLAIM.update(outcome="mine")
    seen = {}
    worker = threading.Thread(target=lambda: (ai_jobs.LAST_CLAIM.update(outcome="theirs"), seen.update(ai_jobs.LAST_CLAIM)))
    worker.start()
    worker.join()
    assert seen["outcome"] == "theirs" and ai_jobs.LAST_CLAIM["outcome"] == "mine"


# ---- the pool: real concurrency, call limit, time budget ---------------------------------------------------

def fake_run_once(monkeypatch, behaviour):
    monkeypatch.setattr(ai_jobs, "run_once", lambda get_db, executor, **kw: behaviour(executor))


class Nothing:
    """get_db stand-in for pool tests: the pool only hands it to dispatch_completed, which is patched out."""
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def pool(monkeypatch, workers, limit, seconds=240, behaviour=None):
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)
    ids = iter(range(1, 1000))
    lock = threading.Lock()

    def default(executor):
        with lock:
            job_id = next(ids)
        ai_jobs.LAST_CLAIM.clear()
        ai_jobs.LAST_CLAIM.update(outcome="claimed", job_id=job_id, kind="generation")
        executor({"id": job_id})
        return True

    fake_run_once(monkeypatch, behaviour or default)
    return auto_worker.CallPool(lambda: Nothing(), lambda job: time.sleep(0.05), workers, limit, seconds)


def test_two_calls_really_run_at_the_same_time(monkeypatch):
    barrier = threading.Barrier(2, timeout=5)  # both must be inside the call together, or the test fails
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)

    def behaviour(executor):
        ai_jobs.LAST_CLAIM.clear()
        ai_jobs.LAST_CLAIM.update(outcome="claimed", job_id=1, kind="generation")
        executor({"id": 1})
        return True

    fake_run_once(monkeypatch, behaviour)
    p = auto_worker.CallPool(lambda: Nothing(), lambda job: barrier.wait(), 2, 2, 240).run()
    assert p.calls == 2 and p.peak == 2 and not p.errors and not p.overran


def test_one_worker_never_overlaps_itself(monkeypatch):
    p = pool(monkeypatch, 1, 3).run()
    assert p.calls == 3 and p.peak == 1


def test_the_call_limit_bounds_a_whole_run_whatever_the_concurrency(monkeypatch):
    p = pool(monkeypatch, 4, 5).run()
    assert p.calls == 5 and p.peak <= 4


def test_an_empty_queue_stops_every_worker_and_says_why(monkeypatch):
    def behaviour(executor):
        ai_jobs.LAST_CLAIM.clear()
        ai_jobs.LAST_CLAIM.update(outcome="no_claimable_job")
        return False

    p = pool(monkeypatch, 3, 10, behaviour=behaviour).run()
    assert p.calls == 0 and p.stopped_because == "no_claimable_job" and p.last_claim["outcome"] == "no_claimable_job"


def test_no_new_call_starts_when_one_more_would_not_finish_in_time(monkeypatch):
    now = [0.0]

    def behaviour(executor):
        ai_jobs.LAST_CLAIM.clear()
        ai_jobs.LAST_CLAIM.update(outcome="claimed", job_id=len([1]), kind="generation")
        now[0] += 45  # a 45-second call
        return True

    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)
    fake_run_once(monkeypatch, behaviour)
    p = auto_worker.CallPool(lambda: Nothing(), lambda job: None, 1, 50, 240, clock=lambda: now[0]).run()
    # starts at 0, 45, 90, 135; at 180 a further 60-second call (+15 s safety) would end past 240
    assert p.calls == 4 and p.stopped_because == "run_time_budget" and now[0] <= 240


def test_a_database_error_in_one_worker_stops_the_run_and_is_reported(monkeypatch):
    def behaviour(executor):
        raise RuntimeError("db down")

    p = pool(monkeypatch, 2, 4, behaviour=behaviour).run()
    assert p.calls == 0 and len(p.errors) >= 1


def test_a_call_still_running_at_the_deadline_does_not_hold_the_run(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(auto_worker, "EXPECTED_CALL_SECONDS", 0)
    monkeypatch.setattr(auto_worker, "SAFETY_SECONDS", 0)
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)

    def behaviour(executor):
        ai_jobs.LAST_CLAIM.clear()
        ai_jobs.LAST_CLAIM.update(outcome="claimed", job_id=1, kind="generation")
        executor({"id": 1})
        return True

    fake_run_once(monkeypatch, behaviour)
    began = time.monotonic()
    result = auto_worker.CallPool(lambda: Nothing(), lambda job: release.wait(30), 2, 2, 0.3).run()
    release.set()
    assert result.overran and time.monotonic() - began < 5  # the run gave up waiting at its deadline


def test_run_seconds_is_bounded():
    assert auto_worker.run_seconds({}) == 240
    assert auto_worker.run_seconds({"AI_RUN_SECONDS": "10"}) == 60
    assert auto_worker.run_seconds({"AI_RUN_SECONDS": "9999"}) == 270
    assert auto_worker.run_seconds({"AI_RUN_SECONDS": "junk"}) == 240


# ---- the worker log and the heartbeat record concurrency and duration -------------------------------------

def test_the_run_log_and_heartbeat_record_concurrency_and_duration(db, setup_ai, monkeypatch, capsys):
    from contextlib import contextmanager

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    for key, value in dict(AI_AUTOMATION_ENABLED="1", QUIZ_DB_SCHEMA="source_staging_x", DEEPSEEK_PRIVATE_DATA_AUTHORIZED="1", AI_QUEUE_AUTOPLAN="0").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(auto_worker, "get_db", test_db)
    monkeypatch.setattr(auto_worker, "tick", lambda: None)
    monkeypatch.setattr(auto_worker, "integrations_tick", lambda: None)
    monkeypatch.setattr(auto_worker.model_router, "authorized", lambda private=False: None)
    monkeypatch.setattr("app.deployment_target.verify_isolated_target", lambda: None)
    set_limits(db, max_concurrency=1)
    auto_worker.main()
    line = capsys.readouterr().out
    assert " concurrency=1 " in line and " parallel_peak=" in line and " duration_s=" in line
    worker = progress.report(db, setup_ai)["worker"]
    assert worker["concurrency"] >= 1 and worker["duration_seconds"] >= 0


# ---- real parallel claims on separate connections ----------------------------------------------------------

def test_claims_racing_on_separate_connections_never_overspend_or_exceed_the_limit():
    import os
    import uuid

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row

    from migrate import apply_migrations

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit development TEST_DATABASE_URL required")
    schema = "quiz_race_test_" + uuid.uuid4().hex
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    dsn = make_conninfo(url, options=f"-csearch_path={schema}")
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            apply_migrations(conn)
            uid = setup_ai.__wrapped__(conn)
            set_limits(conn, max_concurrency=4, daily_cny=100, monthly_cny=100, task_cny=100)
            jobs(conn, uid, 8)
            first = ai_jobs.claim(conn)
            amount = conn.execute("SELECT amount_cny FROM ai_cny_reservations WHERE job_id=%s", (first["id"],)).fetchone()["amount_cny"]
            # the first call is still running; room for three calls today in all, four may run at once:
            # two of the six racers must win, never three
            set_limits(conn, daily_cny=amount * Decimal("3.5"))
        start = threading.Barrier(6, timeout=10)
        won, lost = [], []

        def racer():
            with psycopg.connect(dsn, row_factory=dict_row) as c:
                start.wait()
                job = ai_jobs.claim(c)
            (won if job else lost).append(job)

        threads = [threading.Thread(target=racer) for _ in range(6)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            running = c.execute("SELECT count(*) AS n FROM ai_jobs WHERE state='running'").fetchone()["n"]
            reserved = c.execute("SELECT COALESCE(sum(amount_cny),0) AS s FROM ai_cny_reservations").fetchone()["s"]
        assert len(won) == 2 and running == 3
        assert reserved <= amount * Decimal("3.5")
        assert len({j["id"] for j in won} | {first["id"]}) == 3  # three different jobs
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
