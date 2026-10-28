"""Synthetic rollback-only policy tests; no provider HTTP or live approval."""

from copy import deepcopy
from decimal import Decimal
import json
import pytest
from app import ai_jobs, model_router, model_compatibility, limited_trial as t
from test_ai_automation import setup_ai  # noqa: F401
from test_live_training import live_app  # noqa: F401


@pytest.fixture
def trial(db, setup_ai, monkeypatch):  # noqa: F811
    uid = setup_ai
    monkeypatch.setattr(model_router, "authorized", lambda **kwargs: None)
    db.execute(
        "UPDATE ai_settings SET token_limit=4096,retry_limit=0,max_concurrency=1,daily_cny=1,monthly_cny=10,task_cny=.1,trial_cny=1 WHERE user_id=%s",
        (uid,),
    )
    payloads = {
        f"{qid}:{phase}": dict(
            question_id=qid, phase=phase, content_sha256="c" * 64, synthetic_only=True
        )
        for qid in t.IDS
        for phase in ("science", "answer")
    }
    policy = dict(
        version="limited-trial/1",
        model_id="deepseek-flash",
        output_cap=4096,
        system_sha256="a" * 64,
        question_payloads={key: ai_jobs.content_sha(p) for key, p in payloads.items()},
    )
    scope = t.validate_policy(policy)
    monkeypatch.setenv("DEEPSEEK_TRIAL_SCOPE_ID", scope)
    probes = []
    for effort in t.EFFORTS:
        j = model_compatibility.enqueue_probe(db, uid, "deepseek-flash", effort)
        t.bind(db, j["id"], policy)
        probes.append(j["id"])
    return uid, policy, scope, payloads, probes


def success(db, job):
    # Explicit synthetic transport receipt in rollback schema; no formal questions exist.
    ai_jobs.finish(
        db,
        job,
        result={
            "real_api": True,
            "returned_model_id": "deepseek-flash",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 10,
                "cache_read_input_tokens": 0,
            },
            "response": {"check": 2},
        },
    )
    # Synthetic acknowledgement for counter tests only, not a real quality event.
    db.execute(
        "UPDATE ai_jobs SET dispatched_at=clock_timestamp() WHERE id=%s", (job["id"],)
    )


def private_job(db, trial, slot="39:science", suffix=""):
    uid, policy, scope, payloads, _ = trial
    p = payloads[slot]
    j = ai_jobs.enqueue(
        db, uid, "synthetic-private:" + slot + suffix, p["phase"] + "_review", p
    )
    t.bind(db, j["id"], policy)
    return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (j["id"],)).fetchone()


def test_no_scope_means_no_official_executor(monkeypatch):
    from app.harness_gateway import execute

    monkeypatch.setattr(
        __import__("app.harness_gateway", fromlist=["authorized"]),
        "authorized",
        lambda **kwargs: None,
    )
    with pytest.raises(ValueError, match="approved_limited_trial"):
        execute({"kind": "compatibility", "settings_snapshot": {}})


def test_four_compat_then_six_bound_calls_and_no_replay(db, trial):
    for _ in range(4):
        j = ai_jobs.claim(db, scope_id=trial[2])
        assert j and j["kind"] == "compatibility"
        success(db, j)
    for slot in trial[3]:
        expected = private_job(db, trial, slot)
        j = ai_jobs.claim(db, scope_id=trial[2])
        assert j["id"] == expected["id"]
        success(db, j)
    assert db.execute("SELECT sum(attempts) AS n FROM ai_jobs").fetchone()["n"] == 10
    replay = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (j["id"],)).fetchone()
    assert (
        t.claim_reason(db, replay, Decimal(".01")) == "trial_attempt_already_consumed"
    )
    payload = {
        "instructions": t.COMPAT_INSTRUCTION,
        "catalog_metadata": db.execute(
            "SELECT metadata FROM ai_model_catalog WHERE model_id='deepseek-flash'"
        ).fetchone()["metadata"],
    }
    extra = ai_jobs.enqueue(
        db, trial[0], "synthetic-eleventh", "compatibility", payload
    )
    t.bind(db, extra["id"], trial[1])
    extra = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (extra["id"],)).fetchone()
    assert t.claim_reason(db, extra, Decimal(".01")) == "trial_ten_call_limit"
    assert (
        db.execute(
            "SELECT count(*) AS n FROM auto_quality_events WHERE phase IN ('science','answer')"
        ).fetchone()["n"]
        == 0
    )


def test_private_cannot_run_without_four_complete_probes(db, trial):
    j = private_job(db, trial)
    assert t.claim_reason(db, j, Decimal(".01")) == "trial_compatibility_not_complete"


def test_timeout_and_restart_keep_reservation_and_block_other_calls(db, trial):
    first = ai_jobs.claim(db, scope_id=trial[2])
    ai_jobs.finish(db, first, error=TimeoutError())
    peer = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (trial[4][1],)).fetchone()
    assert t.claim_reason(db, peer, Decimal(".01")) in (
        "trial_wait_for_operator",
        "trial_unknown_charge_reconciliation_required",
    )
    assert (
        db.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"] == 1
    )
    assert (
        db.execute(
            "SELECT count(*) AS n FROM ai_cny_events WHERE kind='unknown'"
        ).fetchone()["n"]
        == 1
    )
    assert ai_jobs.claim(db, scope_id=trial[2]) is None


def test_unknown_usage_halts_even_successful_transport(db, trial):
    first = ai_jobs.claim(db, scope_id=trial[2])
    ai_jobs.finish(
        db,
        first,
        result={
            "real_api": True,
            "returned_model_id": "deepseek-flash",
            "usage": {},
            "response": {"check": 2},
        },
    )

    peer = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (trial[4][1],)).fetchone()
    assert (
        t.claim_reason(db, peer, Decimal(".01"))
        == "trial_unknown_charge_reconciliation_required"
    )


def test_malformed_charged_response_preserves_supplier_usage(db, trial):
    first = ai_jobs.claim(db, scope_id=trial[2])
    usage = {"input_tokens": 17, "output_tokens": 8, "cache_read_input_tokens": 0}
    ai_jobs.finish(db, first, error=ai_jobs.ProviderFailure(200, usage=usage))
    event = db.execute(
        "SELECT usage,provider_request_confirmed FROM ai_call_events"
    ).fetchone()
    assert event["usage"] == usage and event["provider_request_confirmed"] is True
    billing = db.execute("SELECT kind,detail FROM ai_cny_events").fetchone()
    assert billing["kind"] == "unknown" and billing["detail"]["usage"] == usage
    assert (
        db.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"] == 1
    )


def test_unexpected_returned_model_blocks_without_zeroing_charge(db, trial):
    first = ai_jobs.claim(db, scope_id=trial[2])
    ai_jobs.finish(
        db,
        first,
        result={
            "real_api": True,
            "returned_model_id": "unapproved-model",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 10,
                "cache_read_input_tokens": 0,
            },
            "response": {"check": 2},
        },
    )
    assert (
        db.execute("SELECT state FROM ai_jobs WHERE id=%s", (first["id"],)).fetchone()[
            "state"
        ]
        == "blocked"
    )
    assert (
        db.execute(
            "SELECT count(*) AS n FROM ai_cny_events WHERE kind='unknown'"
        ).fetchone()["n"]
        == 1
    )
    assert ai_jobs.claim(db, scope_id=trial[2]) is None


def test_concurrency_has_one_in_flight(db, trial):
    first = ai_jobs.claim(db, scope_id=trial[2])
    assert first
    assert ai_jobs.claim(db, scope_id=trial[2]) is None
    assert (
        db.execute(
            "SELECT count(*) AS n FROM ai_jobs WHERE state='running'"
        ).fetchone()["n"]
        == 1
    )


def test_expired_lease_after_crash_never_retries_or_releases(db, trial):
    first = ai_jobs.claim(db, scope_id=trial[2])
    db.execute(
        "UPDATE ai_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
        (first["id"],),
    )
    assert ai_jobs.claim(db, scope_id=trial[2]) is None
    row = db.execute(
        "SELECT state,attempts FROM ai_jobs WHERE id=%s", (first["id"],)
    ).fetchone()
    assert row == {"state": "blocked", "attempts": 1}
    assert (
        db.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"] == 1
    )
    assert (
        db.execute(
            "SELECT count(*) AS n FROM ai_cny_events WHERE kind='unknown'"
        ).fetchone()["n"]
        == 1
    )


def test_two_database_connections_only_reserve_one_trial_request(live_app, monkeypatch):  # noqa: F811
    import psycopg
    from psycopg.rows import dict_row
    from concurrent.futures import ThreadPoolExecutor

    _, dsn, _ = live_app
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        uid = setup_ai.__wrapped__(conn)
        state = trial.__wrapped__(conn, uid, monkeypatch)

    def reserve(_):
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            row = ai_jobs.claim(conn, scope_id=state[2])
            return row["id"] if row else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(r is not None for r in results) == 1
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()[
                "n"
            ]
            == 1
        )
        assert (
            conn.execute("SELECT sum(attempts) AS n FROM ai_jobs").fetchone()["n"] == 1
        )


def test_one_yuan_ceiling_does_not_release_previous_reservations(db, trial):
    first = ai_jobs.claim(db, scope_id=trial[2])
    success(db, first)
    db.execute(
        "UPDATE ai_settings SET daily_cny=100,monthly_cny=100,trial_cny=100 WHERE user_id=%s",
        (trial[0],),
    )
    # Simulate an earlier conservative reservation, never claim it was actual spend.
    db.execute(
        "INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot) SELECT job_id,attempt+1,.99-amount_cny,price_snapshot FROM ai_cny_reservations WHERE job_id=%s",
        (first["id"],),
    )
    peer = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (trial[4][1],)).fetchone()
    assert (
        t.claim_reason(db, peer, Decimal(".02")) == "trial_one_yuan_reservation_limit"
    )
    assert (
        t.claim_reason(db, peer, Decimal(".11")) == "trial_one_yuan_reservation_limit"
    )


def test_unapproved_payload_and_other35_rejected(db, trial):
    j = private_job(db, trial)
    bad = deepcopy(j)
    bad["payload"]["question_id"] = 40
    with pytest.raises(ValueError, match="unapproved_private"):
        t.check_job(bad)
    bad = deepcopy(j)
    bad["payload"]["extra_book_text"] = "not in approved request"
    with pytest.raises(ValueError, match="unapproved_private"):
        t.check_job(bad)


def test_wire_strips_runtime_and_rejects_hidden_content():
    from app.harness_runner import build_prompt

    payload = {"synthetic": "allowed"}
    system = "static public persona"
    body = {
        "model": "deepseek-flash",
        "max_tokens": 4096,
        "system": system,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": build_prompt(payload)},
                    {
                        "type": "text",
                        "text": "Current runtime context. This snapshot supersedes earlier runtime-context snapshots.\n cwd: PRIVATE",
                    },
                ],
            }
        ],
        "thinking": {"type": "enabled"},
        "output_config": {"effort": "high"},
        "tools": [],
        "stream": True,
    }
    guard = t.wire_guard(payload, ai_jobs.content_sha(system))
    guard(body)
    assert len(body["messages"][0]["content"]) == 1 and "PRIVATE" not in json.dumps(
        body
    )
    bad = deepcopy(body)
    bad["messages"].append({"role": "assistant", "content": "old round conclusion"})
    with pytest.raises(ValueError, match="shared_model_history"):
        guard(bad)
    bad = deepcopy(body)
    bad["metadata"] = {"credential": "forbidden"}
    with pytest.raises(ValueError, match="wire_parameters"):
        guard(bad)
    bad = deepcopy(body)
    bad["messages"][0]["content"][0]["text"] += " extra unapproved source"
    with pytest.raises(ValueError, match="wire_text"):
        guard(bad)
    bad = deepcopy(body)
    bad["output_config"]["effort"] = "low"
    with pytest.raises(ValueError, match="unapproved_effort"):
        guard(bad)


def test_child_gets_scope_but_never_database_credentials(
    db, trial, monkeypatch, tmp_path
):
    from app import harness_gateway as gateway

    job = ai_jobs.claim(db, scope_id=trial[2])
    monkeypatch.setattr(gateway, "authorized", lambda **kwargs: None)
    monkeypatch.setenv("AI_PRIVATE_HOME", str(tmp_path / "private-sdk"))
    monkeypatch.setenv("DATABASE_URL", "synthetic-secret-not-to-forward")
    captured = {}

    class Process:
        returncode = 0

        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def communicate(self, *args, **kwargs):
            return '{"real_api":false}', ""

    monkeypatch.setattr(gateway.subprocess, "Popen", Process)
    assert gateway.execute(job) == {"real_api": False}
    assert captured["env"]["DEEPSEEK_TRIAL_SCOPE_ID"] == trial[2]
    assert "DATABASE_URL" not in captured["env"]


def test_relay_rejects_extra_text_before_any_official_client(monkeypatch):
    import http.client
    from app.deepseek_relay import relay
    from app.harness_runner import build_prompt
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("No external client permitted")

    monkeypatch.setattr(httpx, "Client", forbidden)
    payload = {"synthetic": "approved"}
    system = "static persona"
    guard = t.wire_guard(payload, ai_jobs.content_sha(system))
    body = {
        "model": "deepseek-flash",
        "max_tokens": 4096,
        "stream": True,
        "system": system,
        "thinking": {"type": "enabled"},
        "output_config": {"effort": "high"},
        "messages": [
            {"role": "user", "content": build_prompt(payload) + " UNAPPROVED"}
        ],
    }
    with relay("deepseek-flash", 4096, request_guard=guard) as (base, state):
        from urllib.parse import urlsplit

        parsed = urlsplit(base)
        client = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        client.request(
            "POST",
            "/v1/messages",
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        response = client.getresponse()
        response.read()
        client.close()
        assert response.status == 403 and state["requests"] == 0
