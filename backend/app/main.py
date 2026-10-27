import asyncio
import os
from contextlib import asynccontextmanager, suppress

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    create_session_token,
    password_is_valid,
    require_auth,
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
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan, title="肖涵 · 中科大考研智能答题", version="2.1.0")


@app.middleware("http")
async def model_privacy(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/v2/model"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "training.html")


@app.get("/health")
def health():
    if os.environ.get("DATABASE_URL"):
        db_health()
    return {"status": "healthy"}


@app.get("/db/health")
def db_health():
    try:
        with get_db() as conn:
            applied = {
                r["name"]
                for r in conn.execute("SELECT name FROM schema_migrations").fetchall()
            }
            required = {p.name for p in (BASE_DIR.parent / "sql").glob("*.sql")}
            if not required.issubset(applied):
                raise HTTPException(503, "V2 schema migrations are incomplete")
        return {
            "status": "healthy",
            "database": "connected",
            "schema": "dynamic-v1",
            "migration_count": len(applied),
            "formal_training": {
                "questions": 28,
                "points": 150,
                "duration_seconds": 3600,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            503, detail=f"Database unavailable: {type(exc).__name__}"
        ) from exc


@app.post("/auth/login")
def login(payload: LoginRequest, response: Response):
    if not password_is_valid(payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password"
        )
    username = __import__("os").environ.get("QUIZ_USERNAME", "owner")
    try:
        token = create_session_token(username)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
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
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


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


@app.get("/training", include_in_schema=False)
def training_page():
    return FileResponse(STATIC_DIR / "training.html")


@app.get("/training-sw.js", include_in_schema=False)
def training_service_worker():
    return FileResponse(
        STATIC_DIR / "training-sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )
