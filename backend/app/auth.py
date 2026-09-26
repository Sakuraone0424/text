"""Authentication helpers for the single-user quiz application."""

import base64
import hashlib
import hmac
import os
import time

from fastapi import HTTPException, Request, status

SESSION_COOKIE = "ustc_quiz_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30


def _secret() -> bytes:
    value = os.environ.get("SESSION_SECRET", "")
    if not value:
        raise RuntimeError("SESSION_SECRET is not configured")
    return value.encode("utf-8")


def create_session_token(username: str, now: int | None = None) -> str:
    now = int(time.time() if now is None else now)
    expires = now + SESSION_TTL_SECONDS
    payload = f"{username}:{expires}".encode("utf-8")
    signature = hmac.new(_secret(), payload, hashlib.sha256).digest()
    raw = payload + b"." + signature
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def verify_session_token(token: str, now: int | None = None) -> str | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload, signature = raw.rsplit(b".", 1)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        username, expires_text = payload.decode("utf-8").rsplit(":", 1)
        current = int(time.time() if now is None else now)
        if current > int(expires_text):
            return None
        return username
    except (ValueError, UnicodeDecodeError, RuntimeError):
        return None


def password_is_valid(password: str) -> bool:
    configured = os.environ.get("APP_PASSWORD", "")
    return bool(configured) and hmac.compare_digest(password, configured)


def request_identity(request: Request) -> str | None:
    api_key = os.environ.get("QUIZ_API_KEY", "")
    if api_key:
        supplied = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        bearer = auth[7:] if auth.lower().startswith("bearer ") else ""
        if (supplied and hmac.compare_digest(supplied, api_key)) or (
            bearer and hmac.compare_digest(bearer, api_key)
        ):
            return os.environ.get("QUIZ_USERNAME", "owner")

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return verify_session_token(token)
    return None


def require_auth(request: Request) -> str:
    identity = request_identity(request)
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return identity
