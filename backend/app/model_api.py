"""Authenticated mobile /model controls; no secrets accepted or returned."""

import os
from decimal import Decimal
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import Field, StrictBool, StrictInt
from app.auth import require_auth
from app.common import ensure_user
from app.db import get_db
from app.question_quality import StrictModel
from app import model_router as models

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
    return dict(
        settings=models.settings(c, uid),
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
    max_concurrency: StrictInt = Field(default=1, ge=1, le=4)
    daily_cny: Decimal = Field(ge=0, le=1000, allow_inf_nan=False)
    monthly_cny: Decimal = Field(ge=0, le=10000, allow_inf_nan=False)
    trial_active: StrictBool = True
    task_cny: Decimal = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    trial_cny: Decimal = Field(default=0, ge=0, le=10000, allow_inf_nan=False)
    stopped: StrictBool = True


@router.post("/limits")
def limits(data: Limits, request: Request, name=Depends(require_auth)):
    origin(request)

    def save(c, u):
        models.settings(c, u)
        c.execute(
            "UPDATE ai_settings SET daily_cny=%s,monthly_cny=%s,task_cny=%s,trial_cny=%s,trial_active=%s,stopped=%s,token_limit=%s,retry_limit=%s,max_concurrency=%s WHERE user_id=%s",
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
                u,
            ),
        )
        return status(c, u)

    return operation(name, save)


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

    return operation(name, stop)


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


@router.get("/billing")
def billing(name=Depends(require_auth)):
    from app.cny_billing import summary

    return operation(name, summary)


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
