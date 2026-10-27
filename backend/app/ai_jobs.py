"""Budgeted leased jobs. Reservations survive uncertain calls and crash recovery."""

import hashlib
import json
from decimal import Decimal
from uuid import uuid4
from psycopg.types.json import Jsonb
from app import model_router


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


def claim(conn):
    # Expired leases are never concurrently finalized: every attempt has a new fence.
    conn.execute(
        "UPDATE ai_jobs SET state=CASE WHEN attempts>=(settings_snapshot->>'retry_limit')::int+1 THEN 'blocked' ELSE 'retrying' END,lease_token=NULL,error_code='lease_expired' WHERE state='running' AND lease_until<clock_timestamp()"
    )
    job = conn.execute(
        "SELECT * FROM ai_jobs WHERE state IN ('pending','retrying') AND next_retry<=clock_timestamp() ORDER BY CASE kind WHEN 'compatibility' THEN 0 WHEN 'grading' THEN 1 ELSE 2 END,id FOR UPDATE SKIP LOCKED LIMIT 1"
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
        amount = model_router.cost_bound(s)
    except ValueError:
        amount = Decimal(0)
        reason = "verified_price_required"
    totals = conn.execute(
        "SELECT COALESCE(sum(b.usd) FILTER(WHERE b.created_at>=(date_trunc('day',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')),0) AS day,COALESCE(sum(b.usd) FILTER(WHERE b.created_at>=(date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')),0) AS month FROM ai_budget_reservations b JOIN ai_jobs j ON j.id=b.job_id WHERE j.user_id=%s",
        (uid,),
    ).fetchone()
    if (
        totals["day"] + amount > live["daily_usd"]
        or totals["month"] + amount > live["monthly_usd"]
    ):
        reason = "budget_exceeded"
    if reason:
        conn.execute(
            "UPDATE ai_jobs SET state='blocked',error_code=%s WHERE id=%s",
            (reason, job["id"]),
        )
        return None
    claimed = conn.execute(
        "UPDATE ai_jobs SET state='running',attempts=attempts+1,reserved_usd=reserved_usd+%s,lease_token=%s,lease_until=clock_timestamp()+interval '180 seconds',started_at=clock_timestamp() WHERE id=%s RETURNING *",
        (amount, str(uuid4()), job["id"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO ai_budget_reservations(job_id,attempt,usd) VALUES(%s,%s,%s)",
        (job["id"], claimed["attempts"], amount),
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
    state = (
        "succeeded"
        if error is None
        else (
            "retrying"
            if locked["attempts"] <= locked["settings_snapshot"]["retry_limit"]
            else "blocked"
        )
    )
    # Error messages/prompts/credentials are not logged. Codes are application-owned.
    code = None if error is None else type(error).__name__
    usage = (result or {}).get("usage", {})
    conn.execute(
        "INSERT INTO ai_call_events(job_id,model_id,effort,model_version,usage,outcome,error_code) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (
            job["id"],
            job["settings_snapshot"]["model_id"],
            job["settings_snapshot"]["effort"],
            (result or {}).get("model_version"),
            Jsonb(usage),
            state,
            code,
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


def run_once(get_db, executor):
    with get_db() as c:
        job = claim(c)
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
