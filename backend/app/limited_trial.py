"""One approved 4+6 experiment, enforced by persistent job attempts/reservations.

No schema changes and no live activation. Binding is an operator action only
AFTER authorization; ordinary jobs cannot enter the official harness executor.
"""

import os
from decimal import Decimal
from psycopg.types.json import Jsonb
from app.ai_jobs import content_sha

IDS = (39, 26, 9)
EFFORTS = ("off", "low", "high", "max")
COMPAT_INSTRUCTION = (
    'Return exactly JSON {"check":2} for 1+1. No tool calls, explanation or other text.'
)


def validate_policy(policy):
    expected = {f"{qid}:{phase}" for qid in IDS for phase in ("science", "answer")}
    if (
        set(policy)
        != {"version", "model_id", "output_cap", "system_sha256", "question_payloads"}
        or policy["version"] != "limited-trial/1"
        or policy["model_id"] != "deepseek-flash"
        or policy["output_cap"] != 4096
        or set(policy["question_payloads"]) != expected
        or any(
            len(x) != 64
            for x in [policy["system_sha256"], *policy["question_payloads"].values()]
        )
    ):
        raise ValueError("invalid_limited_trial_policy")
    return content_sha(policy)


def check_job(job):
    guard = job["settings_snapshot"].get("limited_trial")
    if not guard or set(guard) != {"scope_id", "policy"}:
        raise ValueError("approved_limited_trial_required")
    scope = validate_policy(guard["policy"])
    if scope != guard["scope_id"] or os.environ.get("DEEPSEEK_TRIAL_SCOPE_ID") != scope:
        raise ValueError("trial_scope_not_authorized")
    s = job["settings_snapshot"]
    if (
        s["model_id"] != "deepseek-flash"
        or s["token_limit"] != 4096
        or s["retry_limit"] != 0
        or s["max_concurrency"] != 1
    ):
        raise ValueError("trial_parameters_changed")
    p = job["payload"]
    if job["kind"] == "compatibility":
        if (
            s["effort"] not in EFFORTS
            or set(p) != {"instructions", "catalog_metadata"}
            or p["instructions"] != COMPAT_INSTRUCTION
        ):
            raise ValueError("unapproved_compatibility_input")
        return guard, "compat:" + s["effort"]
    slot = f"{p.get('question_id')}:{p.get('phase')}"
    if (
        job["kind"] not in ("science_review", "answer_review")
        or s["effort"] != "high"
        or job["kind"] != p.get("phase", "") + "_review"
        or guard["policy"]["question_payloads"].get(slot) != content_sha(p)
    ):
        raise ValueError("unapproved_private_payload")
    return guard, slot


def bind(conn, job_id, policy):
    """Only bind an authorized, still-unattempted job; never execute it here."""
    from app.model_router import authorized

    job = conn.execute(
        "SELECT * FROM ai_jobs WHERE id=%s FOR UPDATE", (job_id,)
    ).fetchone()
    if not job or job["state"] != "pending" or job["attempts"] != 0:
        raise ValueError("unattempted_pending_job_required")
    authorized(private=job["kind"] != "compatibility")
    scope = validate_policy(policy)
    snapshot = {
        **job["settings_snapshot"],
        "limited_trial": {"scope_id": scope, "policy": policy},
    }
    proposed = {**job, "settings_snapshot": snapshot}
    check_job(proposed)
    old = job["settings_snapshot"].get("limited_trial")
    if old and old != snapshot["limited_trial"]:
        raise ValueError("existing_trial_binding_immutable")
    conn.execute(
        "UPDATE ai_jobs SET settings_snapshot=%s WHERE id=%s", (Jsonb(snapshot), job_id)
    )
    return scope


def claim_reason(conn, job, amount):
    """Called inside the same transaction as ai_jobs.claim and CNY reservation."""
    if "limited_trial" not in job["settings_snapshot"]:
        return None  # Offline/legacy jobs may exist; official executor rejects them.
    try:
        guard, slot = check_job(job)
    except ValueError as e:
        return str(e)
    scope = guard["scope_id"]
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (int(scope[:15], 16),))
    peers = conn.execute(
        "SELECT * FROM ai_jobs WHERE settings_snapshot->'limited_trial'->>'scope_id'=%s ORDER BY id",
        (scope,),
    ).fetchall()
    if any(p["settings_snapshot"].get("limited_trial") != guard for p in peers):
        return "trial_policy_changed"
    if job["attempts"] != 0:
        return "trial_attempt_already_consumed"
    if sum(p["attempts"] for p in peers) >= 10:
        return "trial_ten_call_limit"
    if any(p["state"] == "running" for p in peers):
        return "trial_call_in_flight"
    if any(p["state"] in ("blocked", "failed", "cancelled", "retrying") for p in peers):
        return "trial_wait_for_operator"
    peer_ids = [p["id"] for p in peers]
    if conn.execute(
        "SELECT 1 FROM auto_quality_events WHERE job_id=ANY(%s) AND NOT passed LIMIT 1",
        (peer_ids,),
    ).fetchone():
        return "trial_review_rejected"
    if conn.execute(
        "SELECT 1 FROM ai_cny_events WHERE job_id=ANY(%s) AND kind='unknown' LIMIT 1",
        (peer_ids,),
    ).fetchone():
        return "trial_unknown_charge_reconciliation_required"
    if any(p["state"] == "succeeded" and p["dispatched_at"] is None for p in peers):
        return "trial_waiting_for_validation"
    total = conn.execute(
        "SELECT COALESCE(sum(amount_cny),0) AS n FROM ai_cny_reservations WHERE job_id=ANY(%s)",
        (peer_ids,),
    ).fetchone()["n"]
    if amount > Decimal(".10") or total + amount > Decimal("1.00"):
        return "trial_one_yuan_reservation_limit"
    for p in peers:
        if p["id"] == job["id"]:
            continue
        try:
            _, other = check_job(p)
        except ValueError:
            return "trial_invalid_peer"
        if other == slot:
            return "trial_slot_already_bound"
    if job["kind"] != "compatibility":
        from app.auto_quality import complete_usage

        compat = [p for p in peers if p["kind"] == "compatibility"]
        if len(compat) != 4 or {
            p["settings_snapshot"]["effort"] for p in compat
        } != set(EFFORTS):
            return "trial_four_probes_required"
        if any(
            p["state"] != "succeeded"
            or not p["result"]
            or p["result"].get("real_api") is not True
            or p["result"].get("response") != {"check": 2}
            or not complete_usage(p["result"].get("usage"))
            for p in compat
        ):
            return "trial_compatibility_not_complete"
    return None


def execution_guard(job):
    guard, _ = check_job(job)
    if job["state"] != "running" or job["attempts"] != 1:
        raise ValueError("trial_committed_single_attempt_required")
    return wire_guard(
        job["payload"],
        guard["policy"]["system_sha256"],
        job["settings_snapshot"]["effort"],
    )


def wire_guard(payload, system_sha, effort="high"):
    """Validate the actual SDK body before the sole official HTTP request."""
    from app.harness_runner import build_prompt

    expected = build_prompt(payload)

    def validate(body):
        allowed = {
            "model",
            "max_tokens",
            "system",
            "messages",
            "thinking",
            "output_config",
            "stream",
            "tools",
        }
        if (
            set(body) - allowed
            or body.get("model") != "deepseek-flash"
            or body.get("max_tokens") != 4096
            or body.get("stream") is not True
        ):
            raise ValueError("unapproved_wire_parameters")
        if body.get("tools") or body.get("thinking") != {
            "type": "disabled" if effort == "off" else "enabled"
        }:
            raise ValueError("unapproved_tools_or_thinking")
        if effort != "off" and body.get("output_config") != {"effort": effort}:
            raise ValueError("unapproved_effort")
        if effort == "off" and body.get("output_config"):
            raise ValueError("unexpected_off_effort")
        if content_sha(body.get("system")) != system_sha:
            raise ValueError("unapproved_sdk_system_prompt")
        messages = body.get("messages", [])
        if (
            len(messages) != 1
            or set(messages[0]) != {"role", "content"}
            or messages[0].get("role") != "user"
        ):
            raise ValueError("shared_model_history")
        content = messages[0].get("content")
        if isinstance(content, list):
            if any(
                set(x) - {"type", "text"} or x.get("type") != "text" for x in content
            ):
                raise ValueError("unapproved_content_block")
            # sdk-minimal appends a cwd/sandbox context block. It is not part of
            # the approved exam input and must NEVER leave the loopback relay.
            if len(content) == 2 and content[1]["text"].startswith(
                "Current runtime context. This snapshot supersedes earlier runtime-context snapshots."
            ):
                content = content[:1]
            content = "".join(x["text"] for x in content)
        if content != expected:
            raise ValueError("unapproved_wire_text")
        body["messages"] = [
            {"role": "user", "content": [{"type": "text", "text": expected}]}
        ]
        # API key belongs in headers only, never in the approved JSON body.
        return True

    return validate


def run_once(get_db):
    """Explicit one-shot operator entrypoint after consent, never a Worker loop."""
    from app.model_router import authorized
    from app import ai_jobs
    from app.harness_gateway import execute
    from app.auto_worker import dispatch_completed

    authorized()
    scope = os.environ.get("DEEPSEEK_TRIAL_SCOPE_ID")
    if not scope:
        raise ValueError("trial_scope_not_authorized")
    ran = ai_jobs.run_once(get_db, execute, scope_id=scope)
    if ran:
        with get_db() as conn:
            dispatch_completed(conn, scope_id=scope)
    return ran
