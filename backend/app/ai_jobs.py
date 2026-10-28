"""Budgeted leased jobs. Reservations survive uncertain calls and crash recovery."""

import hashlib
import json
from decimal import Decimal
from uuid import uuid4
from psycopg.types.json import Jsonb
from app import model_router, cny_billing


class ProviderFailure(RuntimeError):
    def __init__(self, status, usage=None):
        self.request_confirmed = type(status) is int and 100 <= status <= 599
        self.status = status if type(status) is int and 100 <= status <= 599 else 502
        self.usage = {
            k: v
            for k, v in (usage or {}).items()
            if k
            in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
            and type(v) is int
            and v >= 0
        }
        super().__init__("provider_request_failed")


def enqueue(conn, user_id, key, kind, payload):
    if kind not in model_router.TASKS:
        raise ValueError("unknown_ai_task")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode()) > 24000:
        raise ValueError("bounded_input_exceeded")
    old = conn.execute("SELECT * FROM ai_jobs WHERE job_key=%s", (key,)).fetchone()
    if old:
        if (
            old["user_id"] != user_id
            or old["kind"] != kind
            or old["payload"] != payload
        ):
            raise ValueError("idempotency_conflict")
        return old
    s = model_router.snapshot(conn, user_id)
    inserted = conn.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(job_key) DO NOTHING RETURNING *",
        (key, user_id, kind, Jsonb(s), Jsonb(payload)),
    ).fetchone()
    return inserted if inserted else enqueue(conn, user_id, key, kind, payload)


def claim(conn, scope_id=None):
    # Expired leases are never concurrently finalized: every attempt has a new fence.
    expired = conn.execute(
        "UPDATE ai_jobs SET state=CASE WHEN attempts>=(settings_snapshot->>'retry_limit')::int+1 THEN 'blocked' ELSE 'retrying' END,lease_token=NULL,error_code='lease_expired' WHERE state='running' AND lease_until<clock_timestamp() RETURNING *"
    ).fetchall()
    for lost in expired:
        if lost["settings_snapshot"].get("billing_currency") == "CNY":
            cny_billing.settle(conn, lost, None, TimeoutError())
    scope_filter = (
        " AND settings_snapshot->'limited_trial'->>'scope_id'=%s" if scope_id else ""
    )
    job = conn.execute(
        "SELECT * FROM ai_jobs WHERE state IN ('pending','retrying') AND next_retry<=clock_timestamp()"
        + scope_filter
        + " ORDER BY CASE kind WHEN 'compatibility' THEN 0 WHEN 'grading' THEN 1 ELSE 2 END,id FOR UPDATE SKIP LOCKED LIMIT 1",
        (scope_id,) if scope_id else (),
    ).fetchone()
    if not job:
        return None
    uid = job["user_id"]
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (810000 + uid,))
    live = model_router.settings(conn, uid)
    s = job["settings_snapshot"]
    reason = None
    model = conn.execute(
        "SELECT * FROM ai_model_catalog WHERE model_id=%s", (s["model_id"],)
    ).fetchone()
    expected = (
        job["payload"].get("catalog_metadata")
        if job["kind"] == "compatibility"
        else s.get("official_metadata")
    )
    if not model or not model["available"] or model["metadata"] != expected:
        reason = "model_delisted_or_catalog_changed"
    if live["stopped"]:
        reason = "automation_stopped"
    running = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE user_id=%s AND state='running'", (uid,)
    ).fetchone()["n"]
    if running >= live["max_concurrency"]:
        return None
    try:
        # Freeze model/effort, not a stale tariff. Each attempt snapshots the current
        # admitted CNY version and reserves its own ceiling before any request.
        price = cny_billing.current_price(conn, s["model_id"])
        amount = model_router.cost_bound({**s, "price_snapshot": price})
    except ValueError:
        price = {}
        amount = Decimal(0)
        reason = "verified_cny_price_required"
    totals = cny_billing.totals(conn, uid)
    if (
        totals["day"] + amount > live["daily_cny"]
        or totals["month"] + amount > live["monthly_cny"]
        or (live["trial_active"] and totals["trial"] + amount > live["trial_cny"])
        or job["reserved_cny"] + amount > live["task_cny"]
    ):
        reason = "budget_exceeded"
    if s.get("billing_currency") != "CNY":
        reason = "legacy_currency_job_requires_requeue"
    from app.limited_trial import claim_reason

    trial_reason = claim_reason(conn, job, amount)
    if trial_reason in ("trial_call_in_flight", "trial_waiting_for_validation"):
        return None
    reason = reason or trial_reason
    if reason:
        conn.execute(
            "UPDATE ai_jobs SET state='blocked',error_code=%s WHERE id=%s",
            (reason, job["id"]),
        )
        return None
    claimed = conn.execute(
        "UPDATE ai_jobs SET state='running',attempts=attempts+1,reserved_cny=reserved_cny+%s,lease_token=%s,lease_until=clock_timestamp()+interval '180 seconds',started_at=clock_timestamp() WHERE id=%s RETURNING *",
        (amount, str(uuid4()), job["id"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot) VALUES(%s,%s,%s,%s)",
        (job["id"], claimed["attempts"], amount, Jsonb(price)),
    )
    return claimed


def finish(conn, job, result=None, error=None):
    locked = conn.execute(
        "SELECT * FROM ai_jobs WHERE id=%s FOR UPDATE", (job["id"],)
    ).fetchone()
    if locked["state"] != "running" or locked["lease_token"] != job["lease_token"]:
        raise ValueError("stale_lease")
    if result is not None and len(json.dumps(result).encode()) > 131072:
        raise ValueError("oversized_result")
    if (
        locked["settings_snapshot"].get("limited_trial")
        and result
        and result.get("real_api") is True
        and result.get("returned_model_id") != locked["settings_snapshot"]["model_id"]
    ):
        error = ProviderFailure(200, usage=result.get("usage"))
    state = (
        "succeeded"
        if error is None
        else (
            "retrying"
            if locked["attempts"] <= locked["settings_snapshot"]["retry_limit"]
            else "blocked"
        )
    )
    if isinstance(error, ProviderFailure) and error.status in (400, 401, 402, 403, 404):
        state = "blocked"
    # Error messages/prompts/credentials are not logged. Codes are application-owned.
    code = (
        None
        if error is None
        else (
            "provider_http_" + str(error.status)
            if isinstance(error, ProviderFailure)
            else type(error).__name__
        )
    )
    usage = (result or {}).get("usage", {}) or getattr(error, "usage", {})
    cny_billing.settle(conn, locked, result, error)
    conn.execute(
        "INSERT INTO ai_call_events(job_id,model_id,effort,model_version,usage,outcome,error_code,provider_request_confirmed) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            job["id"],
            job["settings_snapshot"]["model_id"],
            job["settings_snapshot"]["effort"],
            (result or {}).get("model_version"),
            Jsonb(usage),
            state,
            code,
            True
            if (result or {}).get("real_api") is True
            or (isinstance(error, ProviderFailure) and error.request_confirmed)
            else None,
        ),
    )
    conn.execute(
        "UPDATE ai_jobs SET state=%s,result=%s,error_code=%s,lease_token=NULL,lease_until=NULL,finished_at=clock_timestamp(),next_retry=clock_timestamp()+interval '60 seconds' WHERE id=%s",
        (state, Jsonb(result) if result is not None else None, code, job["id"]),
    )
    conn.execute(
        "UPDATE ai_jobs SET input_tokens=%s,output_tokens=%s,cached_tokens=%s WHERE id=%s",
        (
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("cache_read_input_tokens"),
            job["id"],
        ),
    )
    return state


def run_once(get_db, executor, scope_id=None):
    with get_db() as c:
        job = claim(c, scope_id=scope_id)
    if not job:
        return False
    try:
        model_router.authorized(private=job["kind"] != "compatibility")
        result = executor(job)
    except Exception as exc:
        with get_db() as c:
            finish(c, job, error=exc)
    else:
        with get_db() as c:
            finish(c, job, result=result)
    return True


def content_sha(content):
    return hashlib.sha256(
        json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
