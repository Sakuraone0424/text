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
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
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
        with db() as conn:
            valid = conn.execute(
                "SELECT 1 FROM reviewer_sessions WHERE token_sha256=%s AND reviewer=%s AND revoked_at IS NULL AND expires_at>clock_timestamp()",
                (hashlib.sha256(token.encode()).hexdigest(), name),
            ).fetchone()
        if not valid:
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
    profile = os.environ.get("CANDIDATE_NETWORK_PROFILE", "loopback")
    if profile == "loopback":
        if request.client.host not in (
            "127.0.0.1",
            "::1",
            "testclient",
        ) or request.url.hostname not in (
            "127.0.0.1",
            "localhost",
            "::1",
            "testserver",
        ):
            return Response(
                "Private remote access has not been authorized/configured",
                status_code=403,
            )
    elif profile == "private_https":
        origin = os.environ.get("CANDIDATE_PRIVATE_ORIGIN", "").rstrip("/")
        parsed = urlsplit(origin)
        try:
            until = datetime.fromisoformat(
                os.environ.get("CANDIDATE_PRIVATE_UNTIL", "").replace("Z", "+00:00")
            )
        except ValueError:
            return Response("Private HTTPS expiry not configured", status_code=503)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or until.tzinfo is None
            or os.environ.get("CANDIDATE_COOKIE_SECURE", "1") != "1"
        ):
            return Response(
                "Private HTTPS access is not configured safely", status_code=503
            )
        if datetime.now(timezone.utc) >= until:
            return Response("Private review access window expired", status_code=403)
        if request.url.scheme != "https" or str(request.base_url).rstrip("/") != origin:
            return Response(
                "Only the approved private HTTPS origin is accepted", status_code=403
            )
    else:
        return Response("Unknown access profile", status_code=503)
    try:
        size = int(request.headers.get("content-length", "0"))
    except ValueError:
        return Response(status_code=400)
    if size > 65536:
        return Response(status_code=413)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
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


@app.get("/review-vault.js")
def vault_script():
    return FileResponse(STATIC / "review-vault.js")


@app.get("/review-opinions.js")
def opinion_script():
    return FileResponse(STATIC / "review-opinions.js")


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
    with db() as conn:
        conn.execute(
            "INSERT INTO reviewer_sessions(token_sha256,reviewer,expires_at) VALUES (%s,%s,to_timestamp(%s))",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                payload.username,
                int(now) + 8 * 3600,
            ),
        )
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
def logout(request: Request, response: Response, name=Depends(write_identity)):
    with db() as conn:
        conn.execute(
            "UPDATE reviewer_sessions SET revoked_at=clock_timestamp() WHERE token_sha256=%s",
            (hashlib.sha256(request.cookies[COOKIE].encode()).hexdigest(),),
        )
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
    expected_note_version: StrictInt | None = Field(default=None, ge=0)


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_version: StrictInt = Field(ge=0)
    expected_last_decision_id: StrictInt = Field(ge=0)
    note: str = Field(max_length=5000)
    checks: dict[str, StrictBool]


@app.get("/api/candidates/{key}/note")
def read_note(key: str, name=Depends(identity)):
    from app.review_notes import latest

    with db() as conn:
        if not conn.execute(
            "SELECT 1 FROM candidate_review_packets WHERE candidate_key=%s", (key,)
        ).fetchone():
            raise HTTPException(404, "Candidate not found")
        return latest(conn, key, name)


@app.post("/api/candidates/{key}/note")
def save_note(key: str, payload: Note, name=Depends(write_identity)):
    from app.review_notes import save

    try:
        with db() as conn:
            return save(conn, key, name, payload.model_dump())
    except ValueError as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_key: str | None = Field(default=None, max_length=300)
    subject_filter: Literal["", "biochemistry", "cell_biology"] = ""
    type_filter: Literal[
        "", "single_choice", "fill_blank", "term", "short_answer", "essay"
    ] = ""


@app.get("/api/position")
def read_position(name=Depends(identity)):
    from app.review_notes import position

    with db() as conn:
        return position(conn, name)


@app.post("/api/position")
def write_position(payload: Position, name=Depends(write_identity)):
    from app.review_notes import save_position

    try:
        with db() as conn:
            return save_position(conn, name, payload.model_dump())
    except LookupError as e:
        raise HTTPException(404, str(e))


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
    from app.review_backup import checksum

    if path.stat().st_size != info["bytes"] or checksum(path) != info["sha256"]:
        raise HTTPException(409, "Backup integrity differs; regenerate before download")
    return FileResponse(path, filename=info["filename"], media_type="application/zip")
