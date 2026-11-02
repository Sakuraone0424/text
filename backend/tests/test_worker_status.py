"""The worker prints one status line per run: ids, counts and codes only. Fake data; no request is sent."""
from contextlib import contextmanager
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from app import ai_jobs, auto_worker, cost_control
from test_ai_automation import setup_ai  # noqa: F401  (fixture)

SECRET = "sk-synthetic-secret-do-not-print"
TEXT = "教材原文一小段，不应出现在日志里。"


@pytest.fixture
def worker(db, setup_ai, monkeypatch):
    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    for name, value in dict(AI_AUTOMATION_ENABLED="1", QUIZ_DB_SCHEMA="source_staging_x",
                            DEEPSEEK_PRIVATE_DATA_AUTHORIZED="1", AI_QUEUE_AUTOPLAN="0",
                            DEEPSEEK_API_KEY=SECRET, AI_CALLS_PER_RUN="2").items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(auto_worker, "get_db", test_db)
    monkeypatch.setattr(auto_worker, "tick", lambda: None)
    monkeypatch.setattr(auto_worker, "integrations_tick", lambda: None)
    monkeypatch.setattr(auto_worker.model_router, "authorized", lambda private=False: None)
    monkeypatch.setattr("app.deployment_target.verify_isolated_target", lambda: None)
    monkeypatch.setattr(auto_worker.cost_control, "held_back_kinds", lambda at=None: [])
    monkeypatch.setattr(cost_control, "held_back_kinds", lambda at=None: [])
    monkeypatch.setattr(auto_worker, "execute", lambda job: (_ for _ in ()).throw(RuntimeError("no model call in tests")))
    return db


def status(capsys):
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("worker-run ")]
    assert len(lines) == 1
    return dict(part.split("=", 1) for part in lines[0][len("worker-run "):].split(" "))


def enqueue(db, uid, key="status-1", kind="generation"):
    return ai_jobs.enqueue(db, uid, key, kind, {"evidence": [{"unit_id": 1, "text": TEXT}], "instructions": "x"})


def test_disabled_worker_says_so_and_shows_the_value(worker, monkeypatch, capsys):
    monkeypatch.setenv("AI_AUTOMATION_ENABLED", "1 ")  # a stray space is exactly the kind of thing to see
    auto_worker.main()
    line = status(capsys)
    assert line["enabled"].startswith("NO(value='1_')") and "claimed" not in line


def test_an_empty_queue_is_reported(worker, setup_ai, capsys):
    auto_worker.main()
    line = status(capsys)
    assert line["enabled"] == "yes" and line["stopped"] == "False" and line["calls"] == "0"
    assert line["stopped_because"] == "no_claimable_job" and line["head"] == "-" and line["cost_cny"] == "0.0000"
    assert line["cap_cny"] == "90/15" and Decimal(line["month_counted_cny"]) >= 0


def test_a_stopped_switch_is_the_reason_and_the_job_is_named(worker, setup_ai, capsys):
    job = enqueue(worker, setup_ai)
    worker.execute("UPDATE ai_settings SET stopped=true")
    auto_worker.main()
    line = status(capsys)
    assert line["stopped"] == "True"
    assert line["stopped_because"] == "blocked:automation_stopped" and line["why_job_id"] == str(job["id"])
    assert line["head"].startswith(f"{job['id']}:generation:pending")


def test_a_leftover_trial_job_at_the_head_of_the_queue_is_visible(worker, setup_ai, monkeypatch, capsys):
    stuck = enqueue(worker, setup_ai, "stuck", "compatibility")
    worker.execute("UPDATE ai_jobs SET settings_snapshot=settings_snapshot||%s WHERE id=%s",
                   (Jsonb({"limited_trial": {"scope_id": "old"}}), stuck["id"]))
    later = enqueue(worker, setup_ai, "later")
    import app.limited_trial as lt
    monkeypatch.setattr(lt, "claim_reason", lambda conn, job, amount: "trial_call_in_flight")
    auto_worker.main()
    line = status(capsys)
    assert line["head"].startswith(f"{stuck['id']}:compatibility:pending:scope=limited_trial")
    assert line["stopped_because"] == "trial_call_in_flight" and line["why_job_id"] == str(stuck["id"])
    assert line["queue"].startswith("pending=2") and later["id"] != stuck["id"]


def test_running_jobs_at_the_concurrency_limit_are_reported(worker, setup_ai, capsys):
    busy = enqueue(worker, setup_ai, "busy")
    worker.execute("UPDATE ai_jobs SET state='running',lease_until=clock_timestamp()+interval '5 minutes',attempts=1 WHERE id=%s", (busy["id"],))
    enqueue(worker, setup_ai, "waiting")
    auto_worker.main()
    line = status(capsys)
    assert line["stopped_because"] == "max_concurrency_reached" and line["why_running"] == "1" and line["why_limit"] == "1"


def test_the_line_never_contains_secrets_or_textbook_text(worker, setup_ai, capsys):
    enqueue(worker, setup_ai)
    auto_worker.main()
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err and TEXT not in out.out + out.err


def test_a_failure_prints_the_status_line_and_a_safe_error(worker, monkeypatch, capsys):
    def deny():
        raise PermissionError("Railway target identity mismatch: SERVICE")

    monkeypatch.setattr("app.deployment_target.verify_isolated_target", deny)
    with pytest.raises(SystemExit):
        auto_worker.main()
    captured = capsys.readouterr()
    assert "error=PermissionError:Railway_target_identity_mismatch:_SERVICE" in captured.out
    assert "AI automation failed (PermissionError)" in captured.err


def test_old_october_reservations_do_not_count_toward_the_monthly_cap(worker, setup_ai):
    """The 90 CNY cap sums reservations created this Beijing month; older GPT-era ones are outside it."""
    job = enqueue(worker, setup_ai, "old-gpt-era")
    worker.execute(
        "INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot,created_at) VALUES(%s,1,25.11,%s,'2026-10-20 12:00+08')",
        (job["id"], Jsonb({})),
    )
    reserved, other = cost_control.month_split(worker, setup_ai)
    assert reserved + other == 0
    assert cost_control.cap_reason(worker, setup_ai, "generation", Decimal("0.5")) is None
