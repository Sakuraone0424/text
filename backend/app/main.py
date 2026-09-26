from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
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
from app.quiz_service import (
    due_reviews,
    ensure_user,
    get_or_create_today_paper,
    history,
    stats_summary,
    submit_paper,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="USTC Adaptive Quiz", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class Submission(BaseModel):
    paper_id: int | None = None
    answers: dict[int, str]


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/db/health")
def db_health():
    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM information_schema.tables
                     WHERE table_schema = 'public'
                       AND table_name IN (
                           'users','topics','questions','papers',
                           'paper_questions','attempts','answers','review_schedule'
                       )) AS table_count,
                    (SELECT COUNT(*) FROM questions) AS question_count
                """
            ).fetchone()
        return {
            "status": "healthy",
            "database": "connected",
            "tables": int(row["table_count"]),
            "questions": int(row["question_count"]),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {type(exc).__name__}")


@app.post("/auth/login")
def login(payload: LoginRequest, response: Response):
    if not password_is_valid(payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
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


def _user_id(username: str) -> int:
    with get_db() as conn:
        return ensure_user(conn, username)


@app.get("/quiz/today")
def get_today_quiz(username: str = Depends(require_auth)):
    try:
        with get_db() as conn:
            user_id = ensure_user(conn, username)
            return get_or_create_today_paper(conn, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/quiz/submit")
def submit_quiz(submission: Submission, username: str = Depends(require_auth)):
    try:
        with get_db() as conn:
            user_id = ensure_user(conn, username)
            return submit_paper(conn, user_id, submission.answers, submission.paper_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


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


@app.get("/review/due")
def get_due_reviews(username: str = Depends(require_auth)):
    with get_db() as conn:
        user_id = ensure_user(conn, username)
        items = due_reviews(conn, user_id)
        return {"count": len(items), "items": items}
