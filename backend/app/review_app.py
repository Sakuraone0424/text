"""Private nonproduction reviewer app. No training, publication or source-approval routes."""

from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
import threading
from subprocess import SubprocessError
from typing import Literal
from uuid import UUID
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from app import candidate_review as service
from app.source_intake import isolated_db
from app.training_spec import COUNTS, SUBJECTS, validate_items

app = FastAPI(
    title="私有候选题审核（非生产）", docs_url=None, redoc_url=None, openapi_url=None
)
STATIC = Path(__file__).with_name("static")
COOKIE = "ustc_candidate_review"
attempts = {}
backup_lock = threading.Lock()


def secret():
    value = os.environ.get("CANDIDATE_SESSION_SECRET", "")
    if len(value) < 32:
        raise HTTPException(503, "Private review session is not configured")
    return value.encode()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 310000
    ).hex()
    return salt + ":" + value


@contextmanager
def db():
    with isolated_db(
        os.environ.get("CANDIDATE_REVIEW_SCHEMA", ""), "development"
    ) as conn:
        name = conn.execute("SELECT current_database() AS name").fetchone()["name"]
        if name != os.environ.get("CANDIDATE_REVIEW_DATABASE_NAME"):
            raise RuntimeError("Explicit private review database identity mismatch")
        yield conn


def identity(request: Request):
    token = request.cookies.get(COOKIE, "")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        payload, signature = raw.rsplit("|", 1)
        name, expiry, _ = payload.split("|")
        if (
            not hmac.compare_digest(
                hmac.new(secret(), payload.encode(), "sha256").hexdigest(), signature
            )
            or int(expiry) < time.time()
        ):
            raise ValueError()
        if name not in json.loads(os.environ.get("CANDIDATE_REVIEW_USERS", "{}")):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(401, "Reviewer login required")
    return name


def csrf(token):
    return hmac.new(secret(), ("csrf:" + token).encode(), "sha256").hexdigest()


def write_identity(request: Request, name=Depends(identity)):
    if not hmac.compare_digest(
        request.headers.get("x-review-csrf", ""), csrf(request.cookies.get(COOKIE, ""))
    ):
        raise HTTPException(403, "Reload the review session before saving")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Origin mismatch")
    return name


@app.middleware("http")
async def privacy(request, call_next):
    try:
        size = int(request.headers.get("content-length", "0"))
    except ValueError:
        return Response(status_code=400)
    if size > 65536:
        return Response(status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


@app.get("/")
def index():
    return FileResponse(STATIC / "review.html")


@app.get("/review.js")
def javascript():
    return FileResponse(STATIC / "review.js")


@app.get("/review.css")
def css():
    return FileResponse(STATIC / "review.css")


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    password: str = Field(min_length=1, max_length=300)


@app.post("/api/login")
def login(payload: Login, request: Request, response: Response):
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Origin mismatch")
    ip = request.client.host
    now = time.time()
    recent = [t for t in attempts.get(ip, []) if now - t < 60]
    attempts[ip] = recent + [now]
    if len(recent) >= 10:
        raise HTTPException(429, "Try again after one minute")
    stored = json.loads(os.environ.get("CANDIDATE_REVIEW_USERS", "{}")).get(
        payload.username, "x:unconfigured"
    )
    salt = stored.split(":", 1)[0]
    if not hmac.compare_digest(password_hash(payload.password, salt), stored):
        raise HTTPException(401, "Reviewer credentials rejected")
    signed = f"{payload.username}|{int(now) + 8 * 3600}|{secrets.token_hex(16)}"
    token = base64.urlsafe_b64encode(
        (
            signed + "|" + hmac.new(secret(), signed.encode(), "sha256").hexdigest()
        ).encode()
    ).decode()
    response.set_cookie(
        COOKIE,
        token,
        max_age=8 * 3600,
        httponly=True,
        secure=os.environ.get("CANDIDATE_COOKIE_SECURE", "1") == "1",
        samesite="strict",
    )
    return {"reviewer": payload.username}


@app.get("/api/session")
def session(request: Request, name=Depends(identity)):
    return {
        "reviewer": name,
        "csrf": csrf(request.cookies[COOKIE]),
        "checks": service.CHECKS,
    }


@app.post("/api/logout")
def logout(response: Response, name=Depends(write_identity)):
    response.delete_cookie(COOKIE)
    return {"logged_out": True}


@app.get("/api/candidates")
def listing(name=Depends(identity)):
    with db() as conn:
        return service.summary(conn)


@app.get("/api/candidates/{key}")
def inspect(key: str, name=Depends(identity)):
    try:
        with db() as conn:
            return service.inspect(conn, key)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/sources/{unit_id}/context")
def source_context(unit_id: int, name=Depends(identity)):
    try:
        with db() as conn:
            service.snapshot_unit(conn, unit_id)
            row = conn.execute(
                "SELECT document_id,ordinal FROM source_units WHERE id=%s", (unit_id,)
            ).fetchone()
            ids = conn.execute(
                "SELECT id FROM source_units WHERE document_id=%s AND ordinal BETWEEN %s AND %s ORDER BY ordinal",
                (row["document_id"], row["ordinal"] - 1, row["ordinal"] + 1),
            ).fetchall()
            return {"units": [service.snapshot_unit(conn, r["id"]) for r in ids]}
    except ValueError as e:
        raise HTTPException(404, str(e))


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_last_decision_id: int = Field(ge=0)
    action: Literal["approve_candidate", "return", "revise"]
    checks: dict[str, StrictBool]
    note: str = Field(min_length=20, max_length=5000)
    human_attestation: StrictBool
    request_id: UUID


@app.post("/api/candidates/{key}/decision")
def decide(key: str, payload: Decision, name=Depends(write_identity)):
    try:
        with db() as conn:
            return service.decide(conn, key, name, payload.model_dump(mode="json"))
    except ValueError as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/api/preview")
def preview(name=Depends(identity)):
    with db() as conn:
        rows = conn.execute(
            "SELECT p.candidate_key,p.question_snapshot FROM candidate_review_packets p WHERE NOT EXISTS(SELECT 1 FROM candidate_revision_links l WHERE l.old_key=p.candidate_key) ORDER BY p.candidate_key"
        ).fetchall()
    chosen = []
    for subject in SUBJECTS:
        for kind, count in COUNTS.items():
            pool = [
                r
                for r in rows
                if r["question_snapshot"]["subject"] == subject
                and r["question_snapshot"]["question_type"] == kind
            ]
            if len(pool) < count:
                raise HTTPException(409, "Candidate quotas are incomplete")
            chosen.extend(pool[:count])
    validate_items([r["question_snapshot"] for r in chosen])
    from collections import Counter

    tags = Counter(t for r in chosen for t in r["question_snapshot"]["knowledge_tags"])
    return {
        "label": "仅供审核的草稿预览，禁止用于真实训练",
        "published": False,
        "points": 150,
        "duration_seconds": 3600,
        "items": chosen,
        "review_warnings": [
            f"共同考点“{tag}”出现{count}次，需核对重复与相互提示。"
            for tag, count in tags.items()
            if count > 1
        ],
    }


def backup_files():
    path = Path(os.environ.get("CANDIDATE_BACKUP_INDEX", ""))
    pointer = Path(os.environ.get("CANDIDATE_BACKUP_POINTER", ""))
    if pointer.is_file():
        root = Path(os.environ.get("CANDIDATE_BACKUP_ROOT", "")).resolve()
        selected = Path(json.loads(pointer.read_text())["index_path"]).resolve()
        if not selected.is_relative_to(root):
            raise HTTPException(503, "Private backup pointer is outside its root")
        path = selected
    if not path.is_file():
        raise HTTPException(503, "Private backup handoff is not prepared")
    info = json.loads(path.read_text())
    return path.parent, info


@app.post("/api/backup/refresh")
def backup_refresh(name=Depends(write_identity)):
    from app.review_backup import export
    from app.source_intake import private_json
    from uuid import uuid4

    root_text = os.environ.get("CANDIDATE_BACKUP_ROOT")
    pointer_text = os.environ.get("CANDIDATE_BACKUP_POINTER")
    pg_bin = os.environ.get("CANDIDATE_PG_BIN")
    if not root_text or not pointer_text or not pg_bin:
        raise HTTPException(503, "Private backup refresh is not configured")
    root = Path(root_text).resolve()
    pointer = Path(pointer_text).resolve()
    if not pointer.is_relative_to(root):
        raise HTTPException(503, "Private pointer/root mismatch")
    with backup_lock:
        destination = root / ("snapshot-" + uuid4().hex)
        try:
            info = export(
                destination,
                os.environ.get("CANDIDATE_REVIEW_SCHEMA", ""),
                pg_bin,
                os.environ.get("CANDIDATE_REVIEW_DATABASE_NAME", ""),
            )
            temp = root / ("pointer-" + uuid4().hex + ".json")
            private_json(
                temp,
                {
                    "index_path": str(destination / "download-index.json"),
                    "requested_by": name,
                },
            )
            os.replace(temp, pointer)
        except (ValueError, RuntimeError, OSError, SubprocessError) as e:
            raise HTTPException(503, "Backup did not complete: " + type(e).__name__)
    return {k: info[k] for k in ("filename", "sha256", "bytes", "snapshot_at")}


@app.get("/api/backup")
def backup_manifest(name=Depends(identity)):
    _, info = backup_files()
    return {
        **{k: info[k] for k in ("filename", "sha256", "bytes", "restore_warning")},
        "snapshot_at": info.get("snapshot_at", "unknown"),
    }


@app.get("/api/backup/download")
def backup_download(name=Depends(identity)):
    root, info = backup_files()
    path = (root / info["filename"]).resolve()
    if path.parent != root.resolve() or not path.is_file():
        raise HTTPException(503, "Private handoff missing")
    return FileResponse(path, filename=info["filename"], media_type="application/zip")
