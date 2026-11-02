"""Auto-upgrade is only an ALLOW switch; a candidate is used only once it is approved, probed at every
level the pipeline asks for, priced and affordable. Delisted frozen models never go silently blocked."""
import pytest
from psycopg.types.json import Jsonb

from app import ai_jobs, cost_control, model_compatibility as mc, model_router as m
from cny_fixtures import price
from app.cny_billing import install_price
from test_ai_automation import setup_ai  # noqa: F401  (fixture)


@pytest.fixture
def candidate(db, setup_ai, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_AUTHORIZED", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-never-sent")
    original = db.execute("SELECT * FROM ai_model_catalog WHERE model_id='deepseek-flash'").fetchone()
    db.execute(
        "INSERT INTO ai_model_catalog(model_id,metadata,compatibility) VALUES('new-synthetic',%s,'{}')",
        (Jsonb(original["metadata"]),),
    )
    m.configure(db, setup_ai, "deepseek-flash", "high", auto_upgrade=True, stopped=False,
                daily_cny=5, monthly_cny=50, task_cny=2, trial_cny=5)
    install_price(db, price("new-synthetic"))
    return setup_ai


def set_compat(db, **cap):
    db.execute("UPDATE ai_model_catalog SET compatibility=%s WHERE model_id='new-synthetic'", (Jsonb(cap),))


def probes(db):
    return sorted(
        r["settings_snapshot"]["effort"]
        for r in db.execute("SELECT settings_snapshot FROM ai_jobs WHERE kind='compatibility' AND settings_snapshot->>'model_id'='new-synthetic'").fetchall()
    )


def test_the_levels_the_pipeline_asks_for_are_all_required(candidate, db):
    assert mc.required_efforts(m.settings(db, candidate)) == {"off", "low", "high"}  # write / review / grading


def test_a_model_nobody_approved_is_ignored_and_nothing_is_queued(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["off", "low", "high"], quality_benchmark_passed=False)
    assert mc.auto_upgrade(db, candidate) == {"status": "none"}
    assert probes(db) == [] and m.settings(db, candidate)["model_id"] == "deepseek-flash"


def test_an_approved_model_missing_levels_gets_synthetic_probes_and_is_not_used_yet(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["high"], quality_benchmark_passed=True)
    out = mc.auto_upgrade(db, candidate)
    assert out["status"] == "waiting"
    assert out["candidates"][-1]["reason"] == "waiting_for_probes" and out["candidates"][-1]["efforts"] == ["low", "off"]
    assert probes(db) == ["low", "off"]
    assert m.settings(db, candidate)["model_id"] == "deepseek-flash"
    mc.auto_upgrade(db, candidate)  # the next run queues nothing new
    assert probes(db) == ["low", "off"]


def test_an_approved_model_never_probed_is_probed_at_every_level(candidate, db):
    set_compat(db, quality_benchmark_passed=True)
    mc.auto_upgrade(db, candidate)
    assert probes(db) == ["high", "low", "off"]


def test_no_switch_without_a_verified_price(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["off", "low", "high"], quality_benchmark_passed=True)
    # a model the price page does not list
    db.execute("UPDATE ai_model_catalog SET model_id='unpriced-synthetic' WHERE model_id='new-synthetic'")
    out = mc.auto_upgrade(db, candidate)
    assert out["status"] == "waiting" and out["candidates"][0]["reason"] == "price_not_verified"
    assert m.settings(db, candidate)["model_id"] == "deepseek-flash"


def test_no_switch_when_the_worst_case_cost_exceeds_a_budget(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["off", "low", "high"], quality_benchmark_passed=True)
    m.configure(db, candidate, "deepseek-flash", "high", auto_upgrade=True, stopped=False,
                daily_cny=5, monthly_cny=50, task_cny=0.0001, trial_cny=5)
    out = mc.auto_upgrade(db, candidate)
    assert out["candidates"][0]["reason"] == "budget_too_small" and m.settings(db, candidate)["model_id"] == "deepseek-flash"


def test_everything_in_place_switches_and_the_switch_is_reported(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["off", "low", "high"], quality_benchmark_passed=True)
    out = mc.auto_upgrade(db, candidate)
    assert out == {"status": "switched", "model": "new-synthetic"} and m.settings(db, candidate)["model_id"] == "new-synthetic"


def test_the_switch_off_or_stopped_does_nothing(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["off", "low", "high"], quality_benchmark_passed=True)
    m.configure(db, candidate, "deepseek-flash", "high", auto_upgrade=False, stopped=False, daily_cny=5, monthly_cny=50, task_cny=2, trial_cny=5)
    assert mc.auto_upgrade(db, candidate)["status"] == "off"
    m.configure(db, candidate, "deepseek-flash", "high", auto_upgrade=True, stopped=True, daily_cny=5, monthly_cny=50, task_cny=2, trial_cny=5)
    assert mc.auto_upgrade(db, candidate) == {"status": "off", "reason": "automation_stopped"}


def test_a_compatibility_probe_never_changes_the_human_benchmark_flag(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["high"], quality_benchmark_passed=True)
    job = mc.enqueue_probe(db, candidate, "new-synthetic", "low")
    db.execute("UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s",
               (Jsonb({"real_api": True, "response": {"check": 2}}), job["id"]))
    job = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    cap = mc.accept(db, job)
    assert cap["quality_benchmark_passed"] is True and "low" in cap["efforts"]
    set_compat(db, real_api_verified=True, efforts=["high"])
    cap = mc.accept(db, db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone())
    assert cap["quality_benchmark_passed"] is False  # was never approved -> stays off


def test_approval_needs_a_probe_and_records_who_when_and_why(candidate, db):
    with pytest.raises(ValueError, match="no_compatibility_probe"):
        mc.approve_benchmark(db, "new-synthetic", True, "x")
    set_compat(db, real_api_verified=True, efforts=["high"])
    cap = mc.approve_benchmark(db, "new-synthetic", True, "质量对比通过")
    assert cap["quality_benchmark_passed"] is True and cap["quality_benchmark_note"] == "质量对比通过"
    assert mc.approve_benchmark(db, "new-synthetic", False)["quality_benchmark_passed"] is False
    with pytest.raises(ValueError, match="not_in_actual_catalog"):
        mc.approve_benchmark(db, "ghost", True)


def test_the_approval_endpoint_is_operator_only(client, db, candidate, monkeypatch):
    from contextlib import contextmanager
    from app import model_api
    from test_api_v2 import LEARNER

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    monkeypatch.setattr(model_api, "get_db", test_db)
    set_compat(db, real_api_verified=True, efforts=["high"])
    body = {"model_id": "new-synthetic", "approved": True, "note": "ok"}
    assert client.post("/v2/model/benchmark-approval", json=body).status_code == 401  # not logged in
    assert client.post("/v2/model/benchmark-approval", json=body, headers=LEARNER).status_code == 403  # no operator key
    ok = client.post("/v2/model/benchmark-approval", json=body, headers={**LEARNER, "x-review-api-key": "operator-test-key"})
    assert ok.status_code == 200 and ok.json()["quality_benchmark_passed"] is True


# ---- a frozen model that disappears --------------------------------------------------------


def delist(db, model="deepseek-flash"):
    db.execute("UPDATE ai_model_catalog SET available=false WHERE model_id=%s", (model,))


def queued(db, uid, key="gone-1", kind="generation"):
    return ai_jobs.enqueue(db, uid, key, kind, {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "y"})


def current(db, job):
    return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()


def test_a_job_frozen_on_a_delisted_model_is_re_frozen_on_the_selected_usable_one(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["off", "low", "high"], max_tested_output_tokens=32768)
    job = queued(db, candidate)
    assert job["settings_snapshot"]["model_id"] == "deepseek-flash"
    delist(db)
    m.configure(db, candidate, "new-synthetic", "high", stopped=False, daily_cny=5, monthly_cny=50, task_cny=2, trial_cny=5)
    claimed = ai_jobs.claim(db)
    assert claimed and claimed["id"] == job["id"] and claimed["state"] == "running"
    assert claimed["settings_snapshot"]["model_id"] == "new-synthetic"
    assert claimed["settings_snapshot"]["effort"] == "off"  # writing level, probed on the replacement
    assert claimed["payload"] == job["payload"]


def test_without_a_usable_replacement_the_job_is_blocked_with_a_clear_reason_and_comes_back(candidate, db):
    job = queued(db, candidate)
    delist(db)
    assert ai_jobs.claim(db) is None
    row = current(db, job)
    assert row["state"] == "blocked" and row["error_code"] == "model_delisted_no_replacement"
    assert ai_jobs.LAST_CLAIM["outcome"] == "blocked:model_delisted_no_replacement"
    # a usable replacement appears: the worker's sweep requeues it and it runs on the new model
    set_compat(db, real_api_verified=True, efforts=["off", "low", "high"], max_tested_output_tokens=32768)
    m.configure(db, candidate, "new-synthetic", "high", stopped=False, daily_cny=5, monthly_cny=50, task_cny=2, trial_cny=5)
    db.execute(
        "UPDATE ai_jobs j SET state='retrying' FROM ai_settings s WHERE s.user_id=j.user_id AND NOT s.stopped AND j.state='blocked' "
        "AND (j.error_code IN ('budget_exceeded','automation_stopped') OR j.error_code LIKE ANY(%s))",
        ([c + "%" for c in ai_jobs.MODEL_GONE_CODES],),
    )
    claimed = ai_jobs.claim(db)
    assert claimed and claimed["settings_snapshot"]["model_id"] == "new-synthetic"


def test_a_replacement_missing_the_writing_level_is_named_not_silently_dropped(candidate, db):
    set_compat(db, real_api_verified=True, efforts=["high"], max_tested_output_tokens=32768)  # no "off"
    job = queued(db, candidate)
    delist(db)
    m.configure(db, candidate, "new-synthetic", "high", stopped=False, daily_cny=5, monthly_cny=50, task_cny=2, trial_cny=5)
    assert ai_jobs.claim(db) is None
    row = current(db, job)
    assert row["state"] == "blocked" and row["error_code"].startswith("replacement_effort_not_probed")
    assert ai_jobs.MODEL_GONE_CODES[1] in row["error_code"]


def test_scoped_trial_and_probe_jobs_are_never_moved_to_another_model(candidate, db):
    probe = mc.enqueue_probe(db, candidate, "deepseek-flash", "high")
    scoped = queued(db, candidate, "scoped")
    db.execute("UPDATE ai_jobs SET settings_snapshot=settings_snapshot||%s WHERE id=%s", (Jsonb({"limited_trial": {"scope_id": "x"}}), scoped["id"]))
    set_compat(db, real_api_verified=True, efforts=["off", "low", "high"], max_tested_output_tokens=32768)
    delist(db)
    moved, reason = ai_jobs.refreeze_on_current_model(db, probe)
    assert reason == "model_delisted_or_catalog_changed" and moved["settings_snapshot"]["model_id"] == "deepseek-flash"
    assert ai_jobs.refreeze_on_current_model(db, current(db, scoped))[1] == "model_delisted_or_catalog_changed"


def test_the_upgrade_status_is_in_the_worker_log_line(db, candidate, monkeypatch, capsys):
    from contextlib import contextmanager
    from app import auto_worker

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    for key, value in dict(AI_AUTOMATION_ENABLED="1", QUIZ_DB_SCHEMA="source_staging_x", DEEPSEEK_PRIVATE_DATA_AUTHORIZED="1").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(auto_worker, "get_db", test_db)
    monkeypatch.setattr(auto_worker, "tick", lambda: None)
    monkeypatch.setattr(auto_worker, "integrations_tick", lambda: None)
    monkeypatch.setattr(auto_worker.model_router, "authorized", lambda private=False: None)
    monkeypatch.setattr("app.deployment_target.verify_isolated_target", lambda: None)
    monkeypatch.setattr(cost_control, "held_back_kinds", lambda at=None: [])
    set_compat(db, real_api_verified=True, efforts=["high"], quality_benchmark_passed=True)
    auto_worker.main()
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("worker-run ")][0]
    assert "upgrade=waiting:new-synthetic=waiting_for_probes" in line
