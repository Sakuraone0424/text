"""Single official Responses request with strict, evidence-bound Finding schema.

No tools, conversation history, prompt repair, retries or response completion.
Raw provider bytes and normalized usage remain separate from scientific approval.
"""

import copy
import json
import os
import hashlib
from pathlib import Path

import httpx
from psycopg.types.json import Jsonb

from app.ai_jobs import content_sha, ProviderFailure
from app.auto_quality import Finding, complete_usage
from app.harness_runner import PROMPT_PREFIX
from app.provider_diagnostics import private_write, safe_diagnostic
from app.question_quality import AXES

URL = "https://api.deepseek.com/responses"
TRANSPORT = "official-responses-json-schema"
SYNTHETIC_TEXT = "SYNTHETIC PROBE ONLY: one plus one equals two."


def probe_payload(metadata, version=1):
    payload = dict(
        catalog_metadata=metadata, schema=Finding.model_json_schema(),
        instructions="Return a complete truthful Finding about the synthetic arithmetic rule. All five checks pass; independent_choice is null. Cite the exact synthetic text as a claim with criterion_indices empty. Map synthetic/arithmetic to unit 1; risk_resolutions and calculations are empty. This is a protocol probe, not a real scientific approval.",
        question=dict(knowledge_tags=["synthetic/arithmetic"]),
        evidence=[dict(unit_id=1, text=SYNTHETIC_TEXT, quality_flags=[])], style=[],
    )
    if version == 2:
        payload["instructions"] = payload["instructions"].replace("risk_resolutions and calculations are empty", "risk_resolutions records only the schema's no-supplied-flags metadata; calculations are empty")
    return payload


def output_schema(payload, version=1):
    schema = copy.deepcopy(Finding.model_json_schema())
    schema["properties"]["checks"] = dict(type="object", properties={k: {"type": "boolean"} for k in AXES})
    schema["properties"]["mapping_evidence"] = dict(type="object", properties={k: dict(type="array", items={"type": "integer"}) for k in payload["question"]["knowledge_tags"]})
    schema["properties"]["risk_resolutions"] = dict(type="object", properties={f"{u['unit_id']}:{flag}": dict(type="string", minLength=30) for u in payload["evidence"] + payload["style"] for flag in u["quality_flags"]})
    if version == 2 and not schema["properties"]["risk_resolutions"]["properties"]:
        # Provider rejects a closed empty object. This is model-emitted transport
        # metadata about the supplied flag set, never a scientific risk approval.
        schema["properties"]["risk_resolutions"]["properties"]["_no_supplied_flags"] = dict(type="string", enum=["No source quality flags were supplied; scientific uncertainty must still be reported in unresolved."])

    def strict(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for v in node.values():
                strict(v)
        elif isinstance(node, list):
            for v in node:
                strict(v)
    strict(schema)
    if version == 2:
        def no_empty(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and not node.get("properties"):
                    raise ValueError("unsupported_empty_schema_object")
                for v in node.values(): no_empty(v)
            elif isinstance(node, list):
                for v in node: no_empty(v)
        no_empty(schema)
    return schema


def wire_contract_sha256():
    return content_sha(dict(transport=TRANSPORT, instructions=PROMPT_PREFIX,
                            schema_algorithm="closed-required-objects-no-empty-v2", reasoning="high", max_output_tokens=32768))


def validate_closed_structure(schema, value):
    """Check required keys without Pydantic inserting optional defaults."""
    def check(node, data):
        if "$ref" in node:
            node = schema["$defs"][node["$ref"].split("/")[-1]]
        if "enum" in node and data not in node["enum"]:
            raise ValueError("schema_enum_mismatch")
        if node.get("type") == "object":
            if not isinstance(data, dict) or set(data) != set(node["required"]):
                raise ValueError("schema_required_keys_mismatch")
            for k, v in data.items(): check(node["properties"][k], v)
        elif node.get("type") == "array":
            if not isinstance(data, list): raise ValueError("schema_array_required")
            for v in data: check(node["items"], v)
    check(schema, value)


def body_for(job):
    from app.review_campaign import check_job
    guard, _ = check_job(job)
    if guard["policy"].get("transport") != "responses_json_schema":
        raise ValueError("responses_scope_required")
    version = 2 if guard["policy"]["version"] == "review-campaign/6" else 1
    return dict(model="deepseek-flash", reasoning={"effort": "high"}, max_output_tokens=32768,
                instructions=PROMPT_PREFIX, input=json.dumps(job["payload"], ensure_ascii=False, sort_keys=True),
                text={"format": dict(type="json_schema", name="Finding", strict=True, schema=output_schema(job["payload"], version))},
                stream=False)


def normalize_usage(raw):
    total, cached, output, reasoning = (raw["input_tokens"], raw["input_tokens_details"]["cached_tokens"], raw["output_tokens"], raw["output_tokens_details"]["reasoning_tokens"])
    if any(type(x) is not int or x < 0 for x in (total, cached, output, reasoning)) or cached > total or reasoning > output or raw["total_tokens"] != total + output:
        raise ValueError("invalid_responses_usage")
    return dict(input_tokens=total-cached, cache_read_input_tokens=cached, output_tokens=output, cache_creation_input_tokens=0)


def parse_response(data, body):
    if data.get("model") != "deepseek-flash" or data.get("status") != "completed" or data.get("error") or data.get("incomplete_details"):
        raise ValueError("incomplete_or_wrong_model_response")
    if data.get("reasoning", {}).get("effort") != "high" or data.get("max_output_tokens") != 32768 or data.get("text", {}).get("format") != body["text"]["format"]:
        raise ValueError("response_configuration_not_confirmed")
    if data.get("previous_response_id") is not None or data.get("store") is not False:
        raise ValueError("stateless_response_required")
    messages = [x for x in data["output"] if x.get("type") == "message"]
    if len(messages) != 1 or messages[0].get("role") != "assistant" or messages[0].get("status") != "completed" or any(x.get("type") not in ("message", "reasoning") for x in data["output"]):
        raise ValueError("one_final_message_without_tools_required")
    parts = messages[0]["content"]
    if not parts or any(x.get("type") != "output_text" for x in parts):
        raise ValueError("final_output_text_required")
    response = json.loads("".join(x["text"] for x in parts))
    validate_closed_structure(body["text"]["format"]["schema"], response)
    Finding.model_validate(response)
    return response


def execute(job, *, client_factory=httpx.Client):
    from app.model_router import authorized
    from app.harness_gateway import prepare_private_home
    from app.review_campaign import execution_guard
    authorized(private=job["kind"] != "compatibility")
    body = body_for(job)
    if not execution_guard(job)(body):
        raise ValueError("unapproved_wire_request")
    home = prepare_private_home(os.environ.get("AI_PRIVATE_HOME", "/tmp/ustc-responses-private"), job["id"]) / "provider-receipt"
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    receipt = dict(transport=TRANSPORT, request_sha256=private_write(home / "request.json", raw), usage={})
    usage = {}; data = None; status = None
    try:
        with client_factory(timeout=360, follow_redirects=False) as client:
            with client.stream("POST", URL, content=raw, headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"], "Content-Type": "application/json"}) as r:
                status = r.status_code; receipt["http_status"] = status
                response_file = home / "response.json"
                fd = os.open(response_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                size = 0; h = hashlib.sha256()
                with os.fdopen(fd, "wb") as f:
                    try:
                        for chunk in r.iter_bytes():
                            size += len(chunk)
                            if size > 8 * 1024 * 1024:
                                raise ValueError("oversized_provider_response")
                            f.write(chunk); h.update(chunk)
                    finally:
                        f.flush(); os.fsync(f.fileno())
                        receipt["response_sha256"] = h.hexdigest()
        if status != 200:
            raise ValueError("provider_http_error")
        data = json.loads(response_file.read_bytes())
        receipt.update(returned_model_id=data.get("model"), provider_message_id=data.get("id"), response_status=data.get("status"))
        usage = normalize_usage(data["usage"]); receipt["usage"] = usage
        response = parse_response(data, body)
        receipt["provider_stop_reason"] = "end_turn"
        return dict(response=response, usage=usage, native_usage=data["usage"], returned_model_id=data["model"], model_version=data.get("model_version"),
                    transport=TRANSPORT, real_api=True, response_status="completed", response_parameters=dict(reasoning=data["reasoning"], max_output_tokens=data["max_output_tokens"], schema_sha256=content_sha(body["text"]["format"]["schema"])),
                    diagnostic=safe_diagnostic({"receipt": receipt}))
    except Exception:
        raise ProviderFailure(status, usage=usage, diagnostic={"receipt": receipt}) from None
    finally:
        private_write(home / "receipt.json", json.dumps(receipt, default=str, sort_keys=True).encode())


def accept_probe(conn, job):
    result = job.get("result") or {}
    from app.review_campaign import check_job
    check_job(job)
    if job["kind"] != "compatibility" or job["state"] != "succeeded" or result.get("real_api") is not True or result.get("transport") != TRANSPORT or result.get("response_status") != "completed" or not complete_usage(result.get("usage")):
        raise ValueError("actual_responses_probe_required")
    finding = Finding.model_validate(result["response"])
    if not finding.passed or finding.unresolved or set(finding.checks) != set(AXES) or not all(finding.checks.values()) or finding.independent_choice is not None or finding.mapping_evidence != {"synthetic/arithmetic": [1]} or not finding.claims or any(c.unit_id != 1 or c.excerpt not in SYNTHETIC_TEXT for c in finding.claims) or result.get("native_usage", {}).get("output_tokens_details", {}).get("reasoning_tokens", 0) <= 0:
        raise ValueError("synthetic_finding_probe_failed")
    if result.get("response_parameters", {}).get("schema_sha256") != content_sha(body_for(job)["text"]["format"]["schema"]):
        raise ValueError("probe_output_schema_changed")
    row = conn.execute("SELECT * FROM ai_model_catalog WHERE model_id='deepseek-flash' FOR UPDATE").fetchone()
    if not row or row["metadata"] != job["payload"]["catalog_metadata"]:
        raise ValueError("probe_catalog_changed")
    cap = {**row["compatibility"], "responses_json_schema": dict(job_id=job["id"], real_api_verified=True, model="deepseek-flash", effort="high", max_output_tokens=32768, finding_schema_sha256=content_sha(Finding.model_json_schema()), wire_contract_sha256=job["settings_snapshot"]["review_campaign"]["policy"].get("wire_contract_sha256"))}
    conn.execute("UPDATE ai_model_catalog SET compatibility=%s WHERE model_id='deepseek-flash'", (Jsonb(cap),))


def compatible(conn, wire_contract=None):
    row = conn.execute("SELECT compatibility FROM ai_model_catalog WHERE model_id='deepseek-flash'").fetchone()
    cap = (row or {}).get("compatibility", {}).get("responses_json_schema", {})
    if not cap.get("real_api_verified") or cap.get("effort") != "high" or cap.get("max_output_tokens") != 32768 or cap.get("finding_schema_sha256") != content_sha(Finding.model_json_schema()):
        return False
    if wire_contract and cap.get("wire_contract_sha256") != wire_contract:
        return False
    probe = conn.execute("SELECT * FROM ai_jobs WHERE id=%s", (cap["job_id"],)).fetchone()
    return bool(probe and probe["kind"] == "compatibility" and probe["state"] == "succeeded" and probe["dispatched_at"] and probe["result"].get("real_api") is True and probe["result"].get("transport") == TRANSPORT)
