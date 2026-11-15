import asyncio
import os
import threading
from contextlib import asynccontextmanager, suppress

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    LOCKED_MESSAGE,
    create_session_token,
    lock_remaining,
    login_source,
    password_is_valid,
    record_failure,
    record_success,
    require_auth,
    revoke_session,
)
from app.db import get_db
from app.routes_v2 import router as reviewed_quiz_router
from app.common import ensure_user
from app.legacy_history import history, stats_summary, load_paper


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app):
    from app.worker import tick, integrations_tick
    import logging

    async def loop(fn, interval):
        while True:
            try:
                await asyncio.to_thread(fn)
            except Exception as exc:
                logging.getLogger(__name__).error(
                    "Training worker failed (%s)", type(exc).__name__
                )
            await asyncio.sleep(interval)

    enabled = (
        os.environ.get("DATABASE_URL")
        and os.environ.get("QUIZ_WORKER_ENABLED", "1") == "1"
    )
    tasks = (
        [
            asyncio.create_task(loop(tick, 1)),
            asyncio.create_task(loop(integrations_tick, 2)),
        ]
        if enabled
        else []
    )
    stop_listening = threading.Event()
    if os.environ.get("DATABASE_URL") and os.environ.get("QUIZ_PROGRESS_PUSH", "1") == "1":
        from app import progress_events

        progress_events.start(stop_listening)     # database changes -> open progress pages (server-sent events)
    yield
    stop_listening.set()
    from app import db as _db

    _db.close_pools()
    from app import progress_events as _events

    _events.HUB.closing = True
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan, title="肖涵 · 中科大考研智能答题", version="2.1.0")


@app.middleware("http")
async def json_charset(request, call_next):
    """Every JSON reply says it is UTF-8, so a browser opening an API address directly shows Chinese, not mojibake."""
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json") and "charset" not in content_type:
        response.headers["content-type"] = "application/json; charset=utf-8"
    return response


@app.middleware("http")
async def timing(request, call_next):
    """Time every request: a Server-Timing header for the browser and per-route percentiles for /v2/perf."""
    import time
    from app import perf

    began = time.perf_counter()
    response = await call_next(request)
    ms = int((time.perf_counter() - began) * 1000)
    route = getattr(request.scope.get("route"), "path", None) or request.url.path
    if not route.startswith("/static"):
        perf.record(route, request.method, ms, response.status_code)
    response.headers["Server-Timing"] = f"app;dur={ms}"
    return response


@app.middleware("http")
async def revalidate_the_shell(request, call_next):
    """The pages, scripts and styles of the application shell are checked with the server on every load (an ETag makes that
    cheap). Without a Cache-Control header a browser may reuse a script for hours by its own guess, so a home-screen app kept
    running yesterday's script after a deploy."""
    response = await call_next(request)
    path = request.url.path
    if (path.startswith("/static/") or path in ("/", "/training")) and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.middleware("http")
async def model_privacy(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/v2/model"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/progress", include_in_schema=False)
def progress_page():
    """The old address keeps working: the page is now a view inside the one application shell."""
    return RedirectResponse("/training#/progress", status_code=302)


@app.get("/static/model.html", include_in_schema=False)
def model_page():
    return RedirectResponse("/training#/model", status_code=302)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "training.html")


@app.get("/health")
def health():
    if (
        os.environ.get("DATABASE_URL")
        or os.environ.get("QUIZ_REQUIRE_RAILWAY_IDENTITY") == "1"
    ):
        _database_status(business=False)   # the platform's check: schema yes, bank size never
    return {"status": "healthy"}


CORE_TABLES = (
    "users", "reviewed_questions", "auto_question_states", "papers_v2", "paper_items_v2", "attempts_v2",
    "answers_v2", "training_sessions", "answer_drafts", "fsrs_cards", "source_units", "ai_jobs", "revoked_sessions",
)


def business_gaps():
    """What is missing for the app to do its job (empty list = ready): core tables, and for the CURRENT specification at
    least one paper's worth of published, verified questions in every subject x question type."""
    from app import training_spec_v5 as spec

    gaps = []
    with get_db() as conn:
        for table in CORE_TABLES:
            if conn.execute("SELECT to_regclass(%s) AS t", (table,)).fetchone()["t"] is None:
                gaps.append({"kind": "table", "name": table})
        if gaps:
            return gaps
        have = {
            (r["subject"], r["kind"]): r["n"]
            for r in conn.execute(
                "SELECT q.content->>'subject' AS subject, q.content->>'question_type' AS kind, count(*)::int AS n "
                "FROM reviewed_questions q JOIN auto_question_states s ON s.question_id=q.id "
                "WHERE q.status='published' AND s.status='ready' GROUP BY 1,2"
            ).fetchall()
        }
    for subject in spec.SUBJECTS:
        for kind, need in spec.COUNTS.items():
            n = have.get((subject, kind), 0)
            if n < need:
                gaps.append({"kind": "questions", "subject": subject, "question_type": kind, "have": n, "need": need})
    return gaps


@app.get("/db/health")
def db_health():
    return _database_status(business=True)


def _database_status(business):
    try:
        with get_db() as conn:
            applied = {
                r["name"]
                for r in conn.execute("SELECT name FROM schema_migrations").fetchall()
            }
            required = {p.name for p in (BASE_DIR.parent / "sql").glob("*.sql")}
            if not required.issubset(applied):
                raise HTTPException(503, "V2 schema migrations are incomplete")
        from app.automation import daily_blueprints
        from app import training_spec_v5 as current, training_spec_v4 as retired_spec, training_spec_v2 as older_spec

        missing = business_gaps() if business else []
        if missing:
            # /health (the platform's check) stays green; this one says what the business still lacks
            raise HTTPException(503, detail={"code": "not_ready_for_training", "missing": missing})

        outdated = os.environ.get("QUIZ_DAILY_SPECIFICATION") in (retired_spec.SPECIFICATION, older_spec.SPECIFICATION)
        try:
            spec = "+".join(b.specification for b in daily_blueprints(os.environ))
        except ValueError:
            raise HTTPException(503, "Unknown QUIZ_DAILY_SPECIFICATION")
        return {
            "status": "healthy",
            "database": "connected",
            "schema": "dynamic-v1",
            "migration_count": len(applied),
            **({"specification_env_outdated": True} if outdated else {}),
            "formal_training": {
                "specification": spec,
                "papers": [
                    {"specification": m.SPECIFICATION, "questions": m.TOTAL_QUESTIONS, "points": m.TOTAL_POINTS, "duration_seconds": m.DURATION_SECONDS}
                    for m in current.PAPERS.values()
                ],
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            503, detail=f"Database unavailable: {type(exc).__name__}"
        ) from exc


@app.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    source = login_source(request)
    with get_db() as conn:
        remaining = lock_remaining(conn, source)
    if remaining:
        raise HTTPException(429, LOCKED_MESSAGE, headers={"Retry-After": str(remaining)})
    if not password_is_valid(payload.password):
        # the failure is committed on its own, so the 401 below cannot roll it back
        with get_db() as conn:
            locked = record_failure(conn, source)
        if locked:
            raise HTTPException(429, LOCKED_MESSAGE, headers={"Retry-After": str(locked)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password"
        )
    username = __import__("os").environ.get("QUIZ_USERNAME", "owner")
    try:
        token = create_session_token(username)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    with get_db() as conn:
        record_success(conn, source)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "username": username}


@app.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        revoke_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/v2/perf")
def perf_report(username: str = Depends(require_auth)):
    """Response time per route: p50 / p95 / max over the last 500 requests of each."""
    from app import perf

    return {"slow_ms": perf.SLOW_MS, "routes": perf.report()}


@app.get("/auth/me")
def me(username: str = Depends(require_auth)):
    return {"authenticated": True, "username": username}


@app.get("/quiz/today", deprecated=True, include_in_schema=False)
@app.post("/quiz/submit", deprecated=True, include_in_schema=False)
@app.get("/review/due", deprecated=True, include_in_schema=False)
def retired_training(username: str = Depends(require_auth)):
    raise HTTPException(410, "Legacy training retired; use /v2/papers")


@app.get("/legacy/papers/{paper_id}")
def historical_paper(paper_id: int, username: str = Depends(require_auth)):
    try:
        with get_db() as conn:
            uid = ensure_user(conn, username)
            result = load_paper(conn, uid, paper_id)
            result["historical_read_only"] = True
            result["answers"] = conn.execute(
                "SELECT a.question_id,a.selected_answer,a.is_correct,t.score,t.submitted_at FROM answers a JOIN attempts t ON t.id=a.attempt_id WHERE t.paper_id=%s AND t.user_id=%s ORDER BY a.question_id",
                (paper_id, uid),
            ).fetchall()
            return result
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/history")
def get_history(username: str = Depends(require_auth)):
    with get_db() as conn:
        user_id = ensure_user(conn, username)
        return {"items": history(conn, user_id)}


@app.get("/stats")
def get_stats(username: str = Depends(require_auth)):
    with get_db() as conn:
        user_id = ensure_user(conn, username)
        return stats_summary(conn, user_id)


app.include_router(reviewed_quiz_router)
from app.model_api import router as model_router

app.include_router(model_router)
from app.stats_api import router as stats_router

app.include_router(stats_router)
from app.external_api import router as external_router

app.include_router(external_router)


@app.get("/training", include_in_schema=False)
def training_page():
    return FileResponse(STATIC_DIR / "training.html")


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/training-sw.js", include_in_schema=False)
def training_service_worker():
    return FileResponse(
        STATIC_DIR / "training-sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )
