"""The interface a Claude Code scheduled task (Routine) uses: lease tasks, submit results, release, status. Secret: EXTERNAL_EXECUTOR_TOKEN in
X-External-Token (separate from the login). Plus the owner's executor settings."""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import Field, StrictInt

from app import external_executor as ext
from app.auth import require_auth
from app.common import ensure_user
from app.db import get_db
from app.question_quality import StrictModel

router = APIRouter(tags=["external-executor"])


def external_auth(response: Response, x_external_token: str | None = Header(default=None)):
    """401 for a wrong or missing token, 503 while no token is configured, 429 above 60 requests a minute. The token is never echoed or logged."""
    response.headers["Cache-Control"] = "no-store"
    try:
        ext.check_token(x_external_token)
        ext.rate_limit()
    except ext.ExternalError as exc:
        raise HTTPException(exc.status, exc.code) from None


def _run(fn):
    try:
        with get_db() as conn:
            return fn(conn)
    except ext.ExternalError as exc:
        raise HTTPException(exc.status, exc.code) from None


@router.get("/v2/external/lease", dependencies=[Depends(external_auth)])
def lease(kind: str = Query(pattern="^(generation|review|grading)$"), max: int = Query(default=1, ge=1, le=ext.MAX_PER_LEASE),
          session: str = Query(min_length=6, max_length=80)):
    return {"leases": _run(lambda c: ext.lease(c, kind, max, session)), "lease_minutes": ext.LEASE_MINUTES}


class Submission(StrictModel):
    lease_id: str = Field(min_length=8, max_length=64)
    session: str = Field(min_length=6, max_length=80)
    model_label: str = Field(min_length=1, max_length=120)
    result: dict


@router.post("/v2/external/submit", dependencies=[Depends(external_auth)])
def submit(data: Submission):
    return _run(lambda c: ext.submit(c, data.lease_id, data.session, data.model_label, data.result))


class Release(StrictModel):
    lease_id: str = Field(min_length=8, max_length=64)


@router.post("/v2/external/release", dependencies=[Depends(external_auth)])
def release(data: Release):
    return _run(lambda c: ext.release(c, data.lease_id))


@router.get("/v2/external/status", dependencies=[Depends(external_auth)])
def status():
    return _run(ext.status)


# ---- the owner's settings (signed in) -----------------------------------------------------------------------------------------

class ExecutorSettings(StrictModel):
    generation: str | None = Field(default=None, pattern="^(deepseek|claude|claude_first)$")
    review: str | None = Field(default=None, pattern="^(deepseek|claude|claude_first)$")
    grading: str | None = Field(default=None, pattern="^(deepseek|claude|claude_first)$")
    fallback_hours: StrictInt | None = Field(default=None, ge=1, le=72)


@router.get("/v2/model/executors")
def get_executors(username: str = Depends(require_auth)):
    def read(conn):
        ensure_user(conn, username)
        import os

        return dict(routing=ext.policy(conn), today=ext.executor_stats(conn), token_configured=bool(os.environ.get("EXTERNAL_EXECUTOR_TOKEN")),
                    grading_trigger_configured=bool(os.environ.get("CLAUDE_ROUTINE_GRADING_URL") and os.environ.get("CLAUDE_ROUTINE_GRADING_TOKEN")))
    return _run(read)


@router.put("/v2/model/executors")
def put_executors(data: ExecutorSettings, username: str = Depends(require_auth)):
    def write(conn):
        ensure_user(conn, username)
        return dict(routing=ext.save_policy(conn, data.model_dump(exclude_none=True)))
    return _run(write)


# ---- blind comparison (signed in) ------------------------------------------------------------------------------------------------

def _owner(fn, username):
    def run(conn):
        return fn(conn, ensure_user(conn, username))
    try:
        return _run(run)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/v2/compare")
def compare_list(username: str = Depends(require_auth)):
    from app import comparison

    return {"sets": _owner(lambda c, u: comparison.listing(c, u), username)}


@router.post("/v2/compare")
def compare_start(username: str = Depends(require_auth)):
    from app import comparison

    return _owner(lambda c, u: comparison.start(c, u), username)


@router.get("/v2/compare/{set_id}")
def compare_view(set_id: int, username: str = Depends(require_auth)):
    from app import comparison

    return _owner(lambda c, u: comparison.view(c, u, set_id), username)


class Rating(StrictModel):
    position: StrictInt = Field(ge=1, le=10)
    side: str = Field(pattern="^[AB]$")
    realism: StrictInt = Field(ge=1, le=5)
    correct: StrictInt = Field(ge=1, le=5)
    rubric: StrictInt = Field(ge=1, le=5)
    note: str | None = Field(default=None, max_length=300)


@router.post("/v2/compare/{set_id}/rate")
def compare_rate(set_id: int, data: Rating, username: str = Depends(require_auth)):
    from app import comparison

    return _owner(lambda c, u: comparison.rate(c, u, set_id, data.position, data.side, data.realism, data.correct, data.rubric, data.note), username)
