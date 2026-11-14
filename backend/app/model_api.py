"""Authenticated mobile /model controls; no secrets accepted or returned."""

import os
from decimal import Decimal
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import Field, StrictBool, StrictInt
from app.auth import require_auth, same_secret
from app.common import ensure_user
from app.db import get_db
from app.question_quality import StrictModel
from app import model_router as models
from app.server_cache import StaleCache

router = APIRouter(prefix="/v2/model", tags=["models"])


def cny_json(value):
    # FastAPI otherwise encodes raw database Decimal as float, losing money precision.
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: cny_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [cny_json(v) for v in value]
    return value


def operation(name, fn):
    try:
        with get_db() as c:
            return cny_json(fn(c, ensure_user(c, name)))
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc))


def status(c, uid):
    settings = models.settings(c, uid)
    from app import cny_billing

    try:
        conflict = cny_billing.price_conflict(c, settings["model_id"])
    except (ValueError, KeyError):
        conflict = None
    return dict(
        price_conflict=conflict,       # two price versions in force whose rates differ, and the official page could not settle it: the owner picks one
        settings=settings,
        catalog=models.catalog(c),
        label="AI自动核验",
        running_tasks_keep_original_settings=True,
    )


@router.get("")
def current(name=Depends(require_auth)):
    return operation(name, status)


class Selection(StrictModel):
    model_id: str = Field(min_length=1, max_length=200)
    effort: str = Field(min_length=1, max_length=20)
    auto_discover: StrictBool = True
    auto_upgrade: StrictBool = False
    stopped: StrictBool = True
    daily_cny: Decimal = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    monthly_cny: Decimal = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    task_limit_enabled: StrictBool = True
    daily_limit_enabled: StrictBool = True
    monthly_limit_enabled: StrictBool = True


def origin(request):
    expected = os.environ.get(
        "QUIZ_PUBLIC_ORIGIN", str(request.base_url).rstrip("/")
    ).rstrip("/")
    if os.environ.get("QUIZ_PUBLIC_ORIGIN"):
        parsed = urlsplit(expected)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(503, "Approved HTTPS origin is not configured safely")
    if request.headers.get("origin") and request.headers["origin"] != expected:
        raise HTTPException(403, "Origin mismatch")


@router.post("")
def select(data: Selection, request: Request, name=Depends(require_auth)):
    origin(request)
    return operation(
        name,
        lambda c, u: models.configure(
            c, u, **{**data.model_dump(exclude={"model_id"}), "model": data.model_id}
        ),
    )


@router.post("/refresh")
def refresh(request: Request, name=Depends(require_auth)):
    origin(request)
    return operation(name, lambda c, u: models.refresh(c))


HELP = "/model · /model status · /model refresh · /model default · /model help"


class Command(StrictModel):
    command: str = Field(max_length=100)


@router.post("/command")
def command(data: Command, request: Request, name=Depends(require_auth)):
    origin(request)
    cmd = data.command.strip()
    if cmd == "/model help":
        return dict(help=HELP)
    if cmd in ("/model", "/model status"):
        return operation(name, status)
    if cmd == "/model refresh":
        return operation(name, lambda c, u: models.refresh(c))
    if cmd == "/model default":

        def reset(c, u):
            models.settings(c, u)
            c.execute(
                "UPDATE ai_settings SET model_id='deepseek-flash',effort='high',auto_upgrade=false,stopped=true WHERE user_id=%s",
                (u,),
            )
            return status(c, u)

        return operation(name, reset)
    raise HTTPException(422, HELP)


class Limits(StrictModel):
    token_limit: StrictInt = Field(default=4096, ge=128, le=32768)
    retry_limit: StrictInt = Field(default=2, ge=0, le=5)
    max_concurrency: StrictInt = Field(default=2, ge=1, le=4)
    daily_cny: Decimal = Field(ge=0, le=1000, allow_inf_nan=False)
    monthly_cny: Decimal = Field(ge=0, le=10000, allow_inf_nan=False)
    trial_active: StrictBool = True
    task_cny: Decimal = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    trial_cny: Decimal = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    stopped: StrictBool = True
    task_limit_enabled: StrictBool = True
    daily_limit_enabled: StrictBool = True
    monthly_limit_enabled: StrictBool = True


@router.post("/limits")
def limits(data: Limits, request: Request, name=Depends(require_auth)):
    origin(request)

    def save(c, u):
        models.settings(c, u)
        c.execute(
            "UPDATE ai_settings SET daily_cny=%s,monthly_cny=%s,task_cny=%s,trial_cny=%s,trial_active=%s,stopped=%s,token_limit=%s,retry_limit=%s,max_concurrency=%s,task_limit_enabled=%s,daily_limit_enabled=%s,monthly_limit_enabled=%s WHERE user_id=%s",
            (
                data.daily_cny,
                data.monthly_cny,
                data.task_cny,
                data.trial_cny,
                data.trial_active,
                data.stopped,
                data.token_limit,
                data.retry_limit,
                data.max_concurrency,
                data.task_limit_enabled,
                data.daily_limit_enabled,
                data.monthly_limit_enabled,
                u,
            ),
        )
        return status(c, u)

    return operation(name, save)


class SaveAll(StrictModel):
    """Everything on the model page except the stop switch, saved together by the one Save button."""
    model_id: str = Field(min_length=1, max_length=200)
    effort: str = Field(min_length=1, max_length=20)
    auto_discover: StrictBool
    auto_upgrade: StrictBool
    token_limit: StrictInt = Field(ge=128, le=32768)
    retry_limit: StrictInt = Field(ge=0, le=5)
    max_concurrency: StrictInt = Field(ge=1, le=4)
    daily_cny: Decimal = Field(ge=0, le=1000, allow_inf_nan=False)
    monthly_cny: Decimal = Field(ge=0, le=10000, allow_inf_nan=False)
    task_cny: Decimal = Field(ge=0, le=1000, allow_inf_nan=False)
    trial_cny: Decimal = Field(ge=0, le=10000, allow_inf_nan=False)
    trial_active: StrictBool
    task_limit_enabled: StrictBool
    daily_limit_enabled: StrictBool
    monthly_limit_enabled: StrictBool
    weak_focus: StrictBool | None = None      # 薄弱章节优先出题 (None: leave as it is)
    mistake_below: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)       # 主观题得分率低于它算错题
    consolidate_below: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)  # 低于它（且不低于上一条）算待巩固


@router.post("/save")
def save_all(data: SaveAll, request: Request, name=Depends(require_auth)):
    """One transaction: model, effort, switches, limits, concurrency, token and retry limits."""
    origin(request)

    def save(c, u):
        current = models.settings(c, u)
        same = (data.model_id, data.effort) == (current["model_id"], current["effort"])
        models.configure(
            c, u, data.model_id, data.effort, verify=not same,
            **{k: v for k, v in data.model_dump(exclude={"model_id", "effort"}).items() if not (k in ("weak_focus", "mistake_below", "consolidate_below") and v is None)},
        )
        return status(c, u)

    return operation(name, save)


class Stopped(StrictModel):
    stopped: StrictBool


@router.post("/stopped")
def set_stopped(data: Stopped, request: Request, name=Depends(require_auth)):
    """The stop / resume switch (the danger zone of the page), separate from Save."""
    origin(request)

    def apply(c, u):
        models.settings(c, u)
        c.execute("UPDATE ai_settings SET stopped=%s,updated_at=clock_timestamp() WHERE user_id=%s", (data.stopped, u))
        return status(c, u)

    return operation(name, apply)


@router.get("/reconciliation")
def reconciliation(name=Depends(require_auth)):
    """What the counted spend is made of, the last balance reading and the last corrections (time, before, after, difference)."""
    from app import cny_billing, cost_reconcile

    return operation(name, lambda c, u: dict(breakdown=cny_billing.ledger_breakdown(c, u), **cost_reconcile.status(c)))


@router.post("/reconcile")
def reconcile_now(request: Request, name=Depends(require_auth)):
    """Read the real balance now and reconcile (the worker does this hourly and at Beijing midnight)."""
    origin(request)
    from app import cost_reconcile

    def run(c, u):
        try:
            balance = cost_reconcile.fetch_balance()
        except ValueError as exc:
            raise ValueError("balance_unavailable:" + str(exc))
        return dict(result=cost_reconcile.reconcile(c, u, balance, "manual"))

    return operation(name, run)


@router.get("/waste/stats")
def waste_stats(hours: int = Query(24, ge=1, le=168), name=Depends(require_auth)):
    """Why finished model work was thrown away in the last `hours`, most frequent first, with how each is handled now."""
    from app import waste_report

    return operation(name, lambda c, u: waste_report.full_stats(c, hours))


@router.get("/waste/replay")
def waste_replay(hours: int = Query(24, ge=1, le=168), limit: int = Query(300, ge=1, le=1000), name=Depends(require_auth)):
    """Recent failed drafts run through today's rules again. No model call, nothing is written."""
    from app import waste_report

    return operation(name, lambda c, u: waste_report.replay(c, hours, limit))


class Probe(StrictModel):
    model_id: str = Field(min_length=1, max_length=200)
    effort: str = Field(min_length=1, max_length=20)


@router.post("/probe")
def probe(data: Probe, request: Request, name=Depends(require_auth)):
    origin(request)
    from app.model_compatibility import enqueue_probe

    return operation(
        name,
        lambda c, u: enqueue_probe(
            c,
            u,
            data.model_id,
            data.effort,
        ),
    )


@router.get("/jobs")
def jobs(name=Depends(require_auth)):
    return operation(
        name,
        lambda c, u: c.execute(
            "SELECT id,kind,state,result->>'returned_model_id' AS returned_model_id,result->>'model_version' AS model_version,settings_snapshot->>'model_id' AS model_id,settings_snapshot->>'effort' AS effort,attempts,reserved_cny,estimated_cny,billing_status,input_tokens,output_tokens,cached_tokens,error_code,started_at,finished_at FROM ai_jobs WHERE user_id=%s ORDER BY id DESC LIMIT 100",
            (u,),
        ).fetchall(),
    )


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: int, request: Request, name=Depends(require_auth)):
    origin(request)

    def stop(c, u):
        old = c.execute(
            "SELECT * FROM ai_jobs WHERE id=%s AND user_id=%s FOR UPDATE", (job_id, u)
        ).fetchone()
        if not old:
            raise LookupError("job_not_found")
        if old["state"] not in ("pending", "retrying", "running"):
            return dict(cancelled_for_commit=False, state=old["state"])
        if old["state"] == "running":
            from app.cny_billing import settle

            settle(c, old, None, RuntimeError("cancelled"))
        c.execute(
            "UPDATE ai_jobs SET state='cancelled',error_code='cancelled_by_owner',lease_token=NULL,lease_until=NULL,finished_at=clock_timestamp() WHERE id=%s",
            (job_id,),
        )
        return dict(
            cancelled_for_commit=True,
            state="cancelled",
            already_sent_request_cannot_be_recalled=True,
        )

    result = operation(name, stop)
    _PROGRESS.invalidate()
    return result


@router.get("/backup")
def backup_index(name=Depends(require_auth)):
    from app.review_app import backup_manifest

    return backup_manifest(name)


@router.get("/backup/download")
def backup_file(name=Depends(require_auth)):
    from app.review_app import backup_download

    response = backup_download(name)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/backup/refresh")
def backup_refresh(request: Request, name=Depends(require_auth)):
    origin(request)
    from app.review_app import backup_refresh as refresh_snapshot

    return refresh_snapshot(name)


@router.get("/quality")
def quality(name=Depends(require_auth)):
    return operation(
        name,
        lambda c, u: dict(
            label="AI自动核验",
            human_signature_required=False,
            states=c.execute(
                "SELECT s.status,q.content->>'subject' AS subject,q.content->>'question_type' AS question_type,count(*) AS count FROM auto_question_states s JOIN reviewed_questions q ON q.id=s.question_id GROUP BY s.status,subject,question_type ORDER BY subject,question_type,s.status"
            ).fetchall(),
            live_eligibility_rechecked_at_paper_creation=True,
        ),
    )


@router.get("/insights")
def insights(name=Depends(require_auth)):
    return operation(
        name,
        lambda c, u: c.execute(
            "SELECT id,kind,result->'response' AS advice,settings_snapshot->>'model_id' AS model_id,settings_snapshot->>'effort' AS effort FROM ai_jobs WHERE user_id=%s AND kind IN ('explanation','study_plan') AND state='succeeded' AND dispatched_at IS NOT NULL ORDER BY id DESC LIMIT 10",
            (u,),
        ).fetchall(),
    )


@router.get("/insights/status")
def insights_status(name=Depends(require_auth)):
    from app.auto_insights import readiness

    return operation(name, readiness)


@router.get("/billing")
def billing(name=Depends(require_auth)):
    from app.cny_billing import summary

    return operation(name, summary)


@router.get("/spend")
def spend(name=Depends(require_auth)):
    """Today / this month, per-call tokens and cost, caps, and the average cost per admitted question."""
    from app.cost_control import spend_report

    return operation(name, spend_report)


class BenchmarkApproval(StrictModel):
    model_id: str = Field(min_length=1, max_length=200)
    approved: StrictBool = True
    note: str = Field(default="", max_length=500)


@router.post("/benchmark-approval")
def benchmark_approval(data: BenchmarkApproval, request: Request, name=Depends(require_auth)):
    """Operator-only (needs QUIZ_REVIEW_API_KEY): record that the quality comparison passed.

    This only ALLOWS auto-upgrade to consider the model; it never selects it. Nothing automatic
    calls this, and it is refused for a model that has no real compatibility probe yet.
    """
    import hmac

    origin(request)
    configured = os.environ.get("QUIZ_REVIEW_API_KEY", "")
    if not configured:
        raise HTTPException(503, "Operator approval is disabled: QUIZ_REVIEW_API_KEY is not configured")
    if not same_secret(request.headers.get("x-review-api-key", ""), configured):
        raise HTTPException(403, "Operator authorization required")
    from app.model_compatibility import approve_benchmark

    return operation(name, lambda c, u: approve_benchmark(c, data.model_id, data.approved, data.note))


STREAM_SECONDS = 8      # short on purpose: the browser reconnects at once, and a restart is never held up for long
_PROGRESS = StaleCache(ttl=30)
_BANK_CARD = StaleCache(ttl=30)


@router.get("/progress")
def progress_report(fresh: bool = Query(False), name=Depends(require_auth)):
    """Question-writing progress: counts, codes and times only (no question or textbook text).

    Served from a shared copy (a page polling every 5 s, or several open pages, share one build: see StaleCache). The copy
    is rebuilt as soon as the database says something changed (progress_events), so it is also never stale for long;
    `?fresh=1` rebuilds now (used after cancelling a task)."""
    from app.progress import report

    return _PROGRESS.get(name, lambda: operation(name, report), fresh=fresh)


@router.get("/progress/bank")
def progress_bank_card(name=Depends(require_auth)):
    """Just the 'is tomorrow's paper stocked, and when will it be' block of the home progress card (cached the same way)."""
    from app.learning_stats import tomorrow_ready

    return _BANK_CARD.get(name, lambda: operation(name, tomorrow_ready))


def _rebuild_progress_pages():
    _PROGRESS.rebuild_all()
    _BANK_CARD.rebuild_all()


from app import progress_events  # noqa: E402

progress_events.on_change(_rebuild_progress_pages)


@router.get("/progress/events")
async def progress_events_stream(request: Request, name=Depends(require_auth)):
    """Server-sent events: `event: update` whenever the progress may have changed (the page then fetches /progress), and a
    comment line every 20 s to keep the connection open. The page falls back to polling if this stream ends."""
    import asyncio
    import json
    import time

    from fastapi.responses import StreamingResponse

    hub = progress_events.HUB
    entry = hub.subscribe()

    async def stream():
        # A stream never lives longer than STREAM_SECONDS: the browser reconnects by itself (retry:), and a deploy or restart is
        # never held up by pages that are still open (uvicorn waits for open responses before it shuts down).
        began = time.monotonic()
        last_ping = began
        try:
            yield "retry: 1000\n"
            yield f"event: hello\ndata: {json.dumps({'version': hub.version})}\n\n"
            while not hub.closing and time.monotonic() - began < STREAM_SECONDS and not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(entry[1].get(), timeout=1.0)
                    yield f"event: update\ndata: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    if time.monotonic() - last_ping >= 15:
                        last_ping = time.monotonic()
                        yield ": ping\n\n"
        finally:
            hub.unsubscribe(entry)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


class PriceConfirm(StrictModel):
    price_id: StrictInt = Field(ge=1)


@router.post("/price-confirm")
def confirm_price_version(data: PriceConfirm, request: Request, name=Depends(require_auth)):
    """The fallback when the official page could not settle two different prices: the owner picks the version to use."""
    origin(request)
    from app import cny_billing

    def run(c, u):
        model = models.settings(c, u)["model_id"]
        shown = cny_billing.price_conflict(c, model)
        if not shown or data.price_id not in {v["price_id"] for v in shown}:
            raise ValueError("price_version_not_in_conflict")
        return cny_billing.confirm_price(c, data.price_id)

    return operation(name, run)


@router.post("/prices/refresh")
def refresh_cny_prices(request: Request, name=Depends(require_auth)):
    origin(request)
    from app.cny_prices import refresh
    import httpx

    try:
        return operation(
            name,
            lambda c, u: dict(
                prices=refresh(c), paid_api_called=False, private_data_sent=False
            ),
        )
    except httpx.HTTPError:
        raise HTTPException(503, "official_cny_price_page_unavailable")


@router.get("/billing/history")
def billing_history(
    task_type: str | None = None,
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    name=Depends(require_auth),
):
    if task_type is not None and task_type not in models.TASKS:
        raise HTTPException(422, "unknown_task_type")
    return operation(
        name,
        lambda c, u: c.execute(
            "SELECT e.*,j.kind AS task_type FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s AND (%s::text IS NULL OR j.kind=%s) AND (%s::bigint IS NULL OR e.id<%s) ORDER BY e.id DESC LIMIT %s",
            (u, task_type, task_type, before_id, before_id, limit),
        ).fetchall(),
    )
