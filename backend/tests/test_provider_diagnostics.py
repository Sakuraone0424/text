"""Synthetic offline receipts only; no external HTTP and no private exam text."""

import hashlib
import http.client
import json
import stat
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from app.provider_diagnostics import (
    inspect_sse, parse_completed_result, private_write, safe_diagnostic,
)


@pytest.mark.parametrize("reason,text,expected", [
    ("max-tokens", "", "incomplete_model_response"),
    ("max-tokens", '{"passed":true}', "incomplete_model_response"),
    ("completed", "{", "invalid_response_json"),
    ("completed", "", "empty_final_response"),
    ("completed", "[]", "response_object_required"),
])
def test_incomplete_responses_never_become_answers(reason, text, expected):
    parsed, error = parse_completed_result(SimpleNamespace(finish_reason=reason, final_response=text))
    assert parsed is None and error == expected


def test_sdk_reasoning_only_session_replays_token_exhaustion():
    from deepseek_harness.api import final_response, finish_reason
    events = [
        {"type": "assistant/message", "data": {"message": {"content": [
            {"type": "reasoning", "text": "Synthetic thinking, never an answer"}
        ]}}},
        {"type": "turn/end", "data": {"reason": {"kind": "max-tokens"}}},
    ]
    assert final_response(events) == ""
    assert parse_completed_result(SimpleNamespace(finish_reason=finish_reason(events), final_response=final_response(events)))[1] == "incomplete_model_response"


def sse(items):
    return b"".join(b"data: " + json.dumps(item).encode() + b"\n\n" for item in items)


def test_sse_missing_usage_is_not_zero_and_malformed_events_are_safe():
    assert inspect_sse(b'data: null\n\ndata: [1]\n\ndata: broken\n\n')["usage"] == {}
    result = inspect_sse(sse([{"type": "message_start", "message": {"id": "msg-synthetic", "model": "deepseek-flash"}}, {"delta": {"stop_reason": "max_tokens"}}]))
    assert result["provider_stop_reason"] == "max_tokens"
    assert result["provider_message_id"] == "msg-synthetic" and result["usage"] == {}


def test_private_receipt_is_exclusive_and_diagnostics_exclude_prose(tmp_path):
    path = tmp_path / "attempt" / "response.sse"
    digest = private_write(path, b"synthetic")
    assert digest == hashlib.sha256(b"synthetic").hexdigest()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        private_write(path, b"overwrite")
    diagnostic = safe_diagnostic({"failure_stage": "invalid_response_json", "exception": "secret", "receipt": {"response_sha256": digest, "authorization": "secret", "raw": "private prose", "provider_message_id": "bad\nheader"}})
    assert diagnostic == {"failure_stage": "invalid_response_json", "receipt": {"response_sha256": digest}}


def test_private_home_rejects_checkout_and_symlink_and_locks_permissions(tmp_path):
    from pathlib import Path
    from app import harness_gateway

    checkout = Path(harness_gateway.__file__).resolve().parents[2]
    with pytest.raises(ValueError, match="outside_checkout"):
        harness_gateway.prepare_private_home(checkout / "private-responses", 1)
    root = tmp_path / "private"
    home = harness_gateway.prepare_private_home(root, 1)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    (root / "job-2").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        harness_gateway.prepare_private_home(root, 2)


@pytest.mark.parametrize("interrupted", [False, True])
def test_relay_persists_raw_bytes_without_headers_even_on_interruption(monkeypatch, tmp_path, interrupted):
    from app import deepseek_relay
    raw = sse([
        {"type": "message_start", "message": {"id": "msg-synthetic", "model": "deepseek-flash", "usage": {"input_tokens": 7, "cache_read_input_tokens": 0}}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "synthetic private reasoning"}},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 4096}},
    ])

    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_bytes(self):
            yield raw
            if interrupted:
                raise OSError("synthetic interruption")

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def stream(self, method, url, **kwargs):
            assert method == "POST" and url == "https://api.deepseek.com/anthropic/v1/messages"
            assert json.loads(kwargs["content"])["model"] == "deepseek-flash"
            return Response()

    monkeypatch.setattr(deepseek_relay.httpx, "Client", Client)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-credential-local-only")
    with deepseek_relay.relay("deepseek-flash", 4096, evidence_dir=tmp_path / "receipt") as (base, measured):
        parsed = urlsplit(base)
        client = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        body = json.dumps({"model": "deepseek-flash", "max_tokens": 4096})
        client.request("POST", "/v1/messages", body=body, headers={"Content-Type": "application/json"})
        response = client.getresponse(); response.read(); client.close()
        assert response.status == (502 if interrupted else 200)
        assert measured["usage"]["output_tokens"] == 4096
        assert measured["provider_stop_reason"] == "max_tokens"
        assert measured["http_status"] == 200
        assert measured["requests"] == 1
    assert (tmp_path / "receipt/response.sse").read_bytes() == raw
    for path in (tmp_path / "receipt").iterdir():
        assert b"synthetic-credential-local-only" not in path.read_bytes()
        assert b"x-api-key" not in path.read_bytes()


def test_container_layout_does_not_make_entire_filesystem_the_checkout(tmp_path, monkeypatch):
    from app import harness_gateway
    code = tmp_path / "image" / "app"
    monkeypatch.setattr(harness_gateway, "__file__", str(code / "app" / "harness_gateway.py"))
    private = tmp_path / "private-outside-image"
    assert harness_gateway.prepare_private_home(private, "9-attempt-1").is_dir()
    with pytest.raises(ValueError, match="outside_checkout"):
        harness_gateway.prepare_private_home(code / "private", 9)
    assert harness_gateway.prepare_private_home(private, "9-attempt-2").is_dir()


def test_sdk_cache_cleanup_preserves_actual_receipt_and_never_follows_links(tmp_path):
    from app.harness_gateway import prune_runtime_cache
    home=tmp_path/'job-1-attempt-1';home.mkdir()
    receipt=home/'provider-receipt';receipt.mkdir()
    (receipt/'response.sse').write_bytes(b'synthetic retained transport evidence')
    cache=home/'runtime-cache';cache.mkdir();(cache/'binary').write_bytes(b'reproducible SDK binary')
    outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('keep')
    (home/'link').symlink_to(outside,target_is_directory=True)
    prune_runtime_cache(home)
    assert (receipt/'response.sse').read_bytes()==b'synthetic retained transport evidence'
    assert (outside/'keep').read_text()=='keep'
    assert not cache.exists() and not (home/'link').exists()
