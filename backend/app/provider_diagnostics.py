"""Private transport evidence; metadata never includes headers or response prose."""

import hashlib
import json
import os
from pathlib import Path


def private_write(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Exclusive creation prevents an attempt from overwriting previous evidence.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return hashlib.sha256(data).hexdigest()


def inspect_sse(raw):
    result = {"response_sha256": hashlib.sha256(raw).hexdigest(), "usage": {}}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            item = json.loads(line[6:])
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        message = item.get("message") or {}
        if not isinstance(message, dict):
            continue
        for key, target in (("model", "returned_model_id"), ("id", "provider_message_id"), ("model_version", "model_version")):
            if isinstance(message.get(key), str) and len(message[key]) <= 200:
                result[target] = message[key]
        usage = message.get("usage") or item.get("usage") or {}
        if isinstance(usage, dict):
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                if type(usage.get(key)) is int and usage[key] >= 0:
                    result["usage"][key] = usage[key]
        delta = item.get("delta") or {}
        if isinstance(delta, dict) and delta.get("stop_reason") in ("end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"):
            result["provider_stop_reason"] = delta["stop_reason"]
    return result


def parse_completed_result(result):
    """Never reinterpret thinking, partial JSON or a token stop as an answer."""
    if result.finish_reason != "completed":
        return None, "incomplete_model_response"
    if not result.final_response.strip():
        return None, "empty_final_response"
    try:
        response = json.loads(result.final_response)
    except ValueError:
        return None, "invalid_response_json"
    if not isinstance(response, dict):
        return None, "response_object_required"
    return response, None


def safe_diagnostic(value):
    """Only application-owned codes, enums and hashes cross into task records."""
    if not isinstance(value, dict):
        return {}
    result = {}
    if value.get("failure_stage") in ("sdk_exception", "incomplete_model_response", "empty_final_response", "invalid_response_json", "response_object_required"):
        result["failure_stage"] = value["failure_stage"]
    if value.get("sdk_finish_reason") in ("completed", "max-tokens", "error", "cancelled", "interrupted"):
        result["sdk_finish_reason"] = value["sdk_finish_reason"]
    receipt = value.get("receipt")
    if isinstance(receipt, dict):
        import re
        clean = {k: v for k, v in receipt.items() if k in ("request_sha256", "response_sha256") and isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)}
        for key in ("returned_model_id", "provider_message_id", "model_version"):
            if isinstance(receipt.get(key), str) and re.fullmatch(r"[a-zA-Z0-9._:-]{1,200}", receipt[key]):
                clean[key] = receipt[key]
        if receipt.get("provider_stop_reason") in ("end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal"):
            clean["provider_stop_reason"] = receipt["provider_stop_reason"]
        result["receipt"] = clean
    return result
