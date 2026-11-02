"""Budgeted leased jobs. Reservations survive uncertain calls and crash recovery."""

import hashlib
import threading
from collections.abc import MutableMapping
import json
from decimal import Decimal
from uuid import uuid4
from psycopg.types.json import Jsonb
from app import model_router, cny_billing


class ProviderFailure(RuntimeError):
    def __init__(self, status, usage=None, diagnostic=None):
        from app.provider_diagnostics import safe_diagnostic

        self.diagnostic = safe_diagnostic(diagnostic)
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


def extended_limits(snapshot):
    """Long calls (high-effort reviews) for everything except the frozen 3-question trial."""
    return "limited_trial" not in snapshot


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
    from app import cost_control

    level = cost_control.effort_for(kind)
    if level and level != s["effort"]:
        if level not in s["capabilities"].get("efforts", []):
            raise ValueError("model_effort_not_compatibility_verified")
        s["effort"] = level
    inserted = conn.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(job_key) DO NOTHING RETURNING *",
        (key, user_id, kind, Jsonb(s), Jsonb(payload)),
    ).fetchone()
    return inserted if inserted else enqueue(conn, user_id, key, kind, payload)


# Why the most recent claim() did or did not hand out a job. Read by the worker's
# one-line status log; contains ids, kinds and application-owned codes only.
class _PerThreadDict(MutableMapping):
    """A dict that every thread sees as its own: parallel worker threads each keep their own last claim."""

    def __init__(self):
        self._local = threading.local()

    def _d(self):
        if not hasattr(self._local, "d"):
            self._local.d = {}
        return self._local.d

    def __getitem__(self, key):
        return self._d()[key]

    def __setitem__(self, key, value):
        self._d()[key] = value

    def __delitem__(self, key):
        del self._d()[key]

    def __iter__(self):
        return iter(self._d())

    def __len__(self):
        return len(self._d())


LAST_CLAIM = _PerThreadDict()
CLAIM_LOCK = 810000000  # advisory lock key shared by every claim


def _note(outcome, **detail):
    LAST_CLAIM.clear()
    LAST_CLAIM.update(outcome=outcome, **detail)


# Claim order. Probes and grading first; then reviews and repairs (questions already written are checked
# before any new question is written); everything else (writing, analysis) last. Within a class: oldest first.
CLAIM_ORDER_SQL = (
    "CASE kind WHEN 'compatibility' THEN 0 WHEN 'grading' THEN 1 "
    "WHEN 'quality_review' THEN 2 WHEN 'repair' THEN 2 WHEN 'science_review' THEN 2 WHEN 'answer_review' THEN 2 "
    "ELSE 3 END,id"
)

# Jobs that were blocked only because their frozen model disappeared; they are retried once a usable
# replacement exists (see auto_worker), never left silently blocked.
MODEL_GONE_CODES = (
    "model_delisted_no_replacement",
    "replacement_effort_not_probed",
    "replacement_unusable",
)
SCOPED = ("limited_trial", "review_campaign", "qa_automation")


def refreeze_on_current_model(conn, job):
    """The model a queued job was frozen with is gone from the catalog (or its metadata changed).

    Ordinary jobs carry no model-specific input, so they are re-frozen on the model that is
    selected, probed and priced NOW. Returns (job, None) when re-frozen, else (job, reason) with a
    specific code. Scoped (frozen trial / campaign / QA) and compatibility jobs are never moved.
    """
    s = job["settings_snapshot"]
    if job["kind"] == "compatibility" or any(k in s for k in SCOPED):
        return job, "model_delisted_or_catalog_changed"
    live = model_router.settings(conn, job["user_id"])
    row = conn.execute("SELECT * FROM ai_model_catalog WHERE model_id=%s", (live["model_id"],)).fetchone()
    if not row or not row["available"] or (live["model_id"] == s.get("model_id") and row["metadata"] == s.get("official_metadata")):
        return job, "model_delisted_no_replacement"
    from app import cost_control

    try:
        fresh = model_router.snapshot(conn, job["user_id"])
    except ValueError as exc:
        return job, "replacement_unusable:" + str(exc)
    level = cost_control.effort_for(job["kind"])
    if level and level != fresh["effort"]:
        if level not in fresh["capabilities"].get("efforts", []):
            return job, "replacement_effort_not_probed:" + level
        fresh["effort"] = level
    moved = conn.execute(
        "UPDATE ai_jobs SET settings_snapshot=%s WHERE id=%s RETURNING *", (Jsonb(fresh), job["id"])
    ).fetchone()
    return moved, None


def claim(conn, scope_id=None, scope_kind="limited_trial"):
    _note("not_started")
    if scope_kind not in ("limited_trial", "review_campaign", "qa_automation"):
        raise ValueError("unknown_execution_scope")
    # Expired leases are never concurrently finalized: every attempt has a new fence.
    scope_filter = (
        " AND settings_snapshot->'" + scope_kind + "'->>'scope_id'=%s" if scope_id else ""
    )
    # Parallel worker threads claim one at a time: each claim sees the reservations and running jobs of the
    # claims before it, so the budget, the per-task limit, the monthly cap and max_concurrency stay exact.
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (CLAIM_LOCK,))
    expired = conn.execute(
        "UPDATE ai_jobs SET state=CASE WHEN attempts>=(settings_snapshot->>'retry_limit')::int+1 THEN 'blocked' ELSE 'retrying' END,lease_token=NULL,error_code='lease_expired' WHERE state='running' AND lease_until<clock_timestamp()"
        + scope_filter
        + " RETURNING *",
        (scope_id,) if scope_id else (),
    ).fetchall()
    for lost in expired:
        if lost["settings_snapshot"].get("billing_currency") == "CNY":
            cny_billing.settle(conn, lost, None, TimeoutError())
    from app import cost_control

    # Question writing, review and repair wait for off-peak hours; grading never does.
    job = conn.execute(
        "SELECT * FROM ai_jobs WHERE state IN ('pending','retrying') AND next_retry<=clock_timestamp()"
        + scope_filter
        + " AND NOT kind=ANY(%s)"
        # one question is never written/reviewed/repaired by two calls at once
        + " AND NOT (payload ? 'question_id' AND EXISTS(SELECT 1 FROM ai_jobs r WHERE r.state='running' AND r.id<>ai_jobs.id"
        " AND r.payload->>'question_id'=ai_jobs.payload->>'question_id'))"
        + " ORDER BY " + CLAIM_ORDER_SQL + " FOR UPDATE SKIP LOCKED LIMIT 1",
        ((scope_id,) if scope_id else ()) + (cost_control.held_back_kinds(),),
    ).fetchone()
    if not job:
        _note("no_claimable_job", held_back=",".join(cost_control.held_back_kinds()))
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
        job, reason = refreeze_on_current_model(conn, job)
        if reason is None:
            s = job["settings_snapshot"]
            model = conn.execute(
                "SELECT * FROM ai_model_catalog WHERE model_id=%s", (s["model_id"],)
            ).fetchone()
    if live["stopped"]:
        reason = "automation_stopped"
    running = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE user_id=%s AND state='running'", (uid,)
    ).fetchone()["n"]
    if running >= live["max_concurrency"]:
        _note("max_concurrency_reached", job_id=job["id"], kind=job["kind"], running=running, limit=live["max_concurrency"])
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
        (live.get("daily_limit_enabled", True) and totals["day"] + amount > live["daily_cny"])
        or (live.get("monthly_limit_enabled", True) and totals["month"] + amount > live["monthly_cny"])
        or (live["trial_active"] and totals["trial"] + amount > live["trial_cny"])
        or (live.get("task_limit_enabled", True) and job["reserved_cny"] + amount > live["task_cny"])
    ):
        reason = "budget_exceeded"
    if s.get("billing_currency") != "CNY":
        reason = "legacy_currency_job_requires_requeue"
    reason = reason or cost_control.cap_reason(conn, uid, job["kind"], amount)
    from app.limited_trial import claim_reason

    trial_reason = claim_reason(conn, job, amount)
    if "review_campaign" in s:
        from app.review_campaign import claim_reason as campaign_reason
        trial_reason = campaign_reason(conn, job)
    if "qa_automation" in s:
        from app.qa_automation import claim_reason as automation_reason
        trial_reason = automation_reason(conn, job)
    if trial_reason in ("trial_call_in_flight", "trial_waiting_for_validation"):
        _note(trial_reason, job_id=job["id"], kind=job["kind"])
        return None
    reason = reason or trial_reason
    if reason:
        conn.execute(
            "UPDATE ai_jobs SET state='blocked',error_code=%s WHERE id=%s",
            (reason, job["id"]),
        )
        _note("blocked:" + str(reason), job_id=job["id"], kind=job["kind"])
        return None
    claimed = conn.execute(
        "UPDATE ai_jobs SET state='running',attempts=attempts+1,reserved_cny=reserved_cny+%s,lease_token=%s,lease_until=clock_timestamp()+%s*interval '1 second',started_at=clock_timestamp() WHERE id=%s RETURNING *",
        (amount, str(uuid4()), 600 if extended_limits(s) else 180, job["id"]),
    ).fetchone()
    conn.execute(
        "INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot) VALUES(%s,%s,%s,%s)",
        (job["id"], claimed["attempts"], amount, Jsonb(price)),
    )
    _note("claimed", job_id=job["id"], kind=job["kind"])
    return claimed


def finish(conn, job, result=None, error=None):
    locked = conn.execute(
        "SELECT * FROM ai_jobs WHERE id=%s FOR UPDATE", (job["id"],)
    ).fetchone()
    if locked["state"] != "running" or locked["lease_token"] != job["lease_token"]:
        raise ValueError("stale_lease")
    if result is not None and len(json.dumps(result).encode()) > (
        1048576 if extended_limits(locked["settings_snapshot"]) else 131072
    ):
        raise ValueError("oversized_result")
    if (
        result
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
    if result is None and isinstance(error, ProviderFailure):
        # A failed response remains a failure; retain safe metadata, not prose.
        result = dict(provider_error=True, usage=usage, diagnostic=error.diagnostic)
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


def run_once(get_db, executor, scope_id=None, scope_kind="limited_trial"):
    with get_db() as c:
        job = claim(c, scope_id=scope_id, scope_kind=scope_kind)
    if not job:
        return False
    try:
        model_router.authorized(private=job["kind"] != "compatibility")
        result = executor(job)
    except Exception as exc:
        with get_db() as c:
            metadata = ({"execution_failure": {"type": type(exc).__name__, "errno": exc.errno}}
                        if isinstance(exc, OSError) else None)
            finish(c, job, result=metadata, error=exc)
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
