import os

from app.auth import create_session_token, password_is_valid, verify_session_token


def test_session_roundtrip(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    token = create_session_token("owner", now=1000)
    assert verify_session_token(token, now=1001) == "owner"


def test_session_expiry(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    token = create_session_token("owner", now=1000)
    assert verify_session_token(token, now=1000 + 60 * 60 * 24 * 31) is None


def test_password(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "abc123")
    assert password_is_valid("abc123")
    assert not password_is_valid("wrong")
