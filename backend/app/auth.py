"""Authentication helpers for the single-user quiz application."""

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request, status

from app.db import get_db

SESSION_COOKIE = "ustc_quiz_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30


def _secret() -> bytes:
    value = os.environ.get("SESSION_SECRET", "")
    if not value:
        raise RuntimeError("SESSION_SECRET is not configured")
    return value.encode("utf-8")


def password_version() -> str:
    """Changes whenever APP_PASSWORD changes, so every cookie signed under the old password stops working."""
    password = os.environ.get("APP_PASSWORD", "").encode("utf-8")
    return hmac.new(_secret(), b"password-version:" + password, hashlib.sha256).hexdigest()[:12]


def create_session_token(username: str, now: int | None = None) -> str:
    """username:expires:session id:password version, signed. The session id lets a log-out end exactly this session."""
    now = int(time.time() if now is None else now)
    expires = now + SESSION_TTL_SECONDS
    payload = f"{username}:{expires}:{secrets.token_hex(8)}:{password_version()}".encode("utf-8")
    signature = hmac.new(_secret(), payload, hashlib.sha256).digest()
    raw = payload + b"." + signature
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_session_token(token: str, now: int | None = None) -> tuple[str, str] | None:
    """(username, session id) for a validly signed, unexpired token made under the current password, else None."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload, separator, signature = raw[:-33], raw[-33:-32], raw[-32:]
        if separator != b".":
            return None
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        username, expires_text, sid, version = payload.decode("utf-8").rsplit(":", 3)
        current = int(time.time() if now is None else now)
        if current > int(expires_text):
            return None
        if not hmac.compare_digest(version.encode("utf-8"), password_version().encode("utf-8")):
            return None
        return username, sid
    except (ValueError, UnicodeDecodeError, RuntimeError):
        return None


def verify_session_token(token: str, now: int | None = None) -> str | None:
    parsed = parse_session_token(token, now)
    return parsed[0] if parsed else None


def _bytes(value) -> bytes:
    return value.encode("utf-8", "surrogatepass") if isinstance(value, str) else bytes(value)


def same_secret(supplied, configured) -> bool:
    """Constant-time comparison on UTF-8 bytes: a Chinese password or a non-ASCII header is a mismatch, never a 500."""
    return hmac.compare_digest(_bytes(supplied), _bytes(configured))


def password_is_valid(password: str) -> bool:
    configured = os.environ.get("APP_PASSWORD", "")
    return bool(configured) and same_secret(password, configured)


# ---- failed-login lockout ------------------------------------------------------------------------------------------
MAX_FAILURES = 10
FAILURE_WINDOW_MINUTES = 10
LOCK_MINUTES = 15
LOCKED_MESSAGE = "登录失败次数过多，已暂时锁定，请 15 分钟后再试。"


def login_source(request: Request) -> str:
    """The client address as the platform proxy saw it (the LAST forwarded entry is the one the proxy added)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    last = forwarded.split(",")[-1].strip() if forwarded else ""
    return (last or (request.client.host if request.client else "unknown"))[:80]


def lock_remaining(conn, source: str) -> int:
    """Seconds left on this source's lock (0 when it is free to try)."""
    row = conn.execute(
        "SELECT GREATEST(0, CEIL(EXTRACT(EPOCH FROM locked_until - clock_timestamp())))::int AS s FROM login_locks WHERE source=%s",
        (source,),
    ).fetchone()
    return row["s"] if row else 0


def record_failure(conn, source: str) -> int:
    """Count one wrong password; the tenth within ten minutes locks the source for fifteen. Returns the lock seconds (0 if none)."""
    conn.execute("INSERT INTO login_failures(source) VALUES (%s)", (source,))
    conn.execute("DELETE FROM login_failures WHERE at < clock_timestamp() - interval '1 day'")
    count = conn.execute(
        "SELECT count(*) AS n FROM login_failures WHERE source=%s AND at > clock_timestamp() - make_interval(mins => %s)",
        (source, FAILURE_WINDOW_MINUTES),
    ).fetchone()["n"]
    if count >= MAX_FAILURES:
        conn.execute(
            "INSERT INTO login_locks(source,locked_until) VALUES (%s, clock_timestamp() + make_interval(mins => %s)) "
            "ON CONFLICT(source) DO UPDATE SET locked_until=EXCLUDED.locked_until",
            (source, LOCK_MINUTES),
        )
        conn.execute("DELETE FROM login_failures WHERE source=%s", (source,))
        return LOCK_MINUTES * 60
    return 0


def record_success(conn, source: str) -> None:
    conn.execute("DELETE FROM login_failures WHERE source=%s", (source,))
    conn.execute("DELETE FROM login_locks WHERE source=%s", (source,))


# ---- session revocation --------------------------------------------------------------------------------------------
def revoke_session(token: str) -> bool:
    """Log out: this cookie's session id is struck off, whatever its remaining lifetime."""
    parsed = parse_session_token(token)
    if not parsed or not os.environ.get("DATABASE_URL"):
        return False
    _, sid = parsed
    with get_db() as conn:
        conn.execute("DELETE FROM revoked_sessions WHERE expires_at < clock_timestamp()")
        conn.execute(
            "INSERT INTO revoked_sessions(sid,expires_at) VALUES (%s, clock_timestamp() + make_interval(secs => %s)) ON CONFLICT DO NOTHING",
            (sid, SESSION_TTL_SECONDS),
        )
    return True


def session_revoked(sid: str) -> bool:
    if not os.environ.get("DATABASE_URL"):
        return False
    with get_db() as conn:
        return conn.execute("SELECT 1 FROM revoked_sessions WHERE sid=%s", (sid,)).fetchone() is not None


def request_identity(request: Request) -> str | None:
    api_key = os.environ.get("QUIZ_API_KEY", "")
    if api_key:
        supplied = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        bearer = auth[7:] if auth.lower().startswith("bearer ") else ""
        if (supplied and same_secret(supplied, api_key)) or (
            bearer and same_secret(bearer, api_key)
        ):
            return os.environ.get("QUIZ_USERNAME", "owner")

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        parsed = parse_session_token(token)
        if parsed and not session_revoked(parsed[1]):
            return parsed[0]
    return None


def require_auth(request: Request) -> str:
    identity = request_identity(request)
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return identity
