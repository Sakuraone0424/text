"""Explicit finite review manifests, separate from the immutable historical trial.

Disabling user money limits never authorizes a payload, retry or other provider.
Every request still has a durable attempt, conservative estimate and receipt.
"""

import os
import re
from decimal import Decimal

from psycopg.types.json import Jsonb

from app.ai_jobs import content_sha
from app.limited_trial import COMPAT_INSTRUCTION, wire_guard


def scope(policy):
    fields = {"version", "model_id", "effort", "output_cap", "system_sha256", "question_payloads", "legacy_scope_id", "legacy_attempts", "legacy_reserved_cny", "legacy_unknown_acknowledged"}
    if policy.get("version") in ("review-campaign/2", "review-campaign/3", "review-campaign/4"):
        fields.add("contract_version")
    if (
        set(policy) != fields
        or policy["version"] not in ("review-campaign/1", "review-campaign/2", "review-campaign/3", "review-campaign/4")
        or (policy["version"] != "review-campaign/1" and policy["contract_version"] != int(policy["version"][-1]))
        or policy["model_id"] != "deepseek-flash"
        or policy["effort"] != "high"
        or policy["output_cap"] != 32768
        or policy["legacy_attempts"] != 5
        or Decimal(policy["legacy_reserved_cny"]) != Decimal(".483840")
        or policy["legacy_unknown_acknowledged"] != [{"job_id": 5, "attempt": 1}]
        or not policy["question_payloads"]
        or len(policy["question_payloads"]) > 76
    ):
        raise ValueError("invalid_review_campaign_policy")
    hashes = [policy["system_sha256"], policy["legacy_scope_id"], *policy["question_payloads"].values()]
    if any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes):
        raise ValueError("invalid_review_campaign_hash")
    if any(not re.fullmatch(r"[1-9][0-9]*:(science|answer)", key) for key in policy["question_payloads"]):
        raise ValueError("invalid_review_campaign_slot")
    return content_sha(policy)


def check_job(job):
    guard = job["settings_snapshot"].get("review_campaign", {})
    if set(guard) != {"scope_id", "policy"} or scope(guard["policy"]) != guard["scope_id"]:
        raise ValueError("approved_review_campaign_required")
    if os.environ.get("DEEPSEEK_REVIEW_SCOPE_ID") != guard["scope_id"]:
        raise ValueError("review_campaign_not_authorized")
    s = job["settings_snapshot"]
    if (s["model_id"], s["effort"], s["token_limit"], s["retry_limit"], s["max_concurrency"]) != ("deepseek-flash", "high", 32768, 0, 1):
        raise ValueError("review_campaign_parameters_changed")
    p = job["payload"]
    if job["kind"] == "compatibility":
        if set(p) != {"instructions", "catalog_metadata"} or p["instructions"] != COMPAT_INSTRUCTION:
            raise ValueError("unapproved_campaign_probe")
        return guard, "compat:high"
    slot = f"{p.get('question_id')}:{p.get('phase')}"
    if job["kind"] != p.get("phase", "") + "_review" or guard["policy"]["question_payloads"].get(slot) != content_sha(p):
        raise ValueError("unapproved_campaign_private_payload")
    return guard, slot


def authorize(conn, uid, policy, authorization_sha256):
    from app.model_router import authorized, settings
    authorized(private=True)
    sid = scope(policy)
    if os.environ.get("DEEPSEEK_REVIEW_SCOPE_ID") != sid or not re.fullmatch(r"[0-9a-f]{64}", authorization_sha256):
        raise ValueError("review_campaign_authorization_required")
    old = conn.execute("SELECT * FROM ai_review_campaigns WHERE scope_id=%s FOR UPDATE", (sid,)).fetchone()
    if old:
        if old["user_id"] != uid or old["policy"] != policy or old["authorization_sha256"] != authorization_sha256:
            raise ValueError("campaign_authorization_immutable")
        return sid
    settings(conn, uid)
    conn.execute("INSERT INTO ai_review_campaigns(scope_id,user_id,policy,authorization_sha256) VALUES(%s,%s,%s,%s)", (sid, uid, Jsonb(policy), authorization_sha256))
    conn.execute("INSERT INTO ai_review_campaign_events(scope_id,action) VALUES(%s,'authorized')", (sid,))
    return sid


def activate(conn, sid, *, disable_user_limits=False):
    from app.model_router import authorized
    authorized(private=True)
    row = conn.execute("SELECT * FROM ai_review_campaigns WHERE scope_id=%s FOR UPDATE", (sid,)).fetchone()
    if not row or os.environ.get("DEEPSEEK_REVIEW_SCOPE_ID") != sid:
        raise ValueError("review_campaign_authorization_required")
    # This is an audited application operation, not an overwrite of old jobs.
    conn.execute("UPDATE ai_settings SET model_id='deepseek-flash',effort='high',token_limit=32768,retry_limit=0,max_concurrency=1,auto_discover=false,auto_upgrade=false,stopped=false WHERE user_id=%s", (row["user_id"],))
    if disable_user_limits:
        conn.execute("UPDATE ai_settings SET task_limit_enabled=false,daily_limit_enabled=false,monthly_limit_enabled=false,trial_active=false WHERE user_id=%s", (row["user_id"],))
    conn.execute("INSERT INTO ai_review_campaign_events(scope_id,action) VALUES(%s,'activated')", (sid,))


def pause(conn, sid, completed=False):
    row = conn.execute("SELECT user_id FROM ai_review_campaigns WHERE scope_id=%s", (sid,)).fetchone()
    if not row:
        raise ValueError("unknown_review_campaign")
    conn.execute("UPDATE ai_settings SET stopped=true WHERE user_id=%s", (row["user_id"],))
    conn.execute("INSERT INTO ai_review_campaign_events(scope_id,action) VALUES(%s,%s)", (sid, "completed" if completed else "paused"))


def bind(conn, job_id, sid):
    job = conn.execute("SELECT * FROM ai_jobs WHERE id=%s FOR UPDATE", (job_id,)).fetchone()
    campaign = conn.execute("SELECT * FROM ai_review_campaigns WHERE scope_id=%s", (sid,)).fetchone()
    if not campaign or not job or job["user_id"] != campaign["user_id"] or job["state"] != "pending" or job["attempts"] != 0:
        raise ValueError("unattempted_campaign_job_required")
    if job["settings_snapshot"].get("limited_trial"):
        raise ValueError("historical_trial_job_cannot_be_rebound")
    guard = dict(scope_id=sid, policy=campaign["policy"])
    previous = job["settings_snapshot"].get("review_campaign")
    if previous and previous != guard:
        raise ValueError("campaign_binding_immutable")
    proposed = {**job, "settings_snapshot": {**job["settings_snapshot"], "review_campaign": guard}}
    check_job(proposed)
    conn.execute("UPDATE ai_jobs SET settings_snapshot=%s WHERE id=%s", (Jsonb(proposed["settings_snapshot"]), job_id))


def claim_reason(conn, job):
    try:
        guard, slot = check_job(job)
    except ValueError as e:
        return str(e)
    sid = guard["scope_id"]
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (int(sid[:15], 16),))
    campaign = conn.execute("SELECT * FROM ai_review_campaigns WHERE scope_id=%s", (sid,)).fetchone()
    if not campaign or campaign["policy"] != guard["policy"] or campaign["user_id"] != job["user_id"]:
        return "campaign_authorization_mismatch"
    legacy = conn.execute("SELECT id,attempts FROM ai_jobs WHERE settings_snapshot->'limited_trial'->>'scope_id'=%s", (guard["policy"]["legacy_scope_id"],)).fetchall()
    if sum(j["attempts"] for j in legacy) != 5:
        return "legacy_trial_attempts_changed"
    old_reserved = conn.execute("SELECT COALESCE(sum(amount_cny),0) AS n FROM ai_cny_reservations WHERE job_id=ANY(%s)", ([j["id"] for j in legacy],)).fetchone()["n"]
    if old_reserved != Decimal(".483840"):
        return "legacy_trial_reservations_changed"
    if conn.execute(
        "SELECT 1 FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s AND e.kind='unknown' AND NOT(e.job_id=5 AND e.attempt=1) AND NOT EXISTS(SELECT 1 FROM ai_cny_events b WHERE b.job_id=e.job_id AND b.attempt=e.attempt AND b.kind='bill_confirmation' AND b.amount_cny IS NOT NULL AND b.detail->>'verified_statement'='true') LIMIT 1",
        (job["user_id"],),
    ).fetchone():
        return "campaign_unacknowledged_unknown_charge_stop"
    peers = conn.execute("SELECT * FROM ai_jobs WHERE settings_snapshot->'review_campaign'->>'scope_id'=%s ORDER BY id", (sid,)).fetchall()
    if job["attempts"] != 0 or sum(p["attempts"] for p in peers) >= len(guard["policy"]["question_payloads"]) + 1:
        return "campaign_finite_attempt_limit"
    for peer in peers:
        try:
            peer_guard, other = check_job(peer)
        except ValueError:
            return "invalid_campaign_peer"
        if peer_guard != guard:
            return "campaign_policy_changed"
        if peer["id"] != job["id"] and other == slot:
            return "campaign_slot_already_bound"
        if peer["state"] == "running":
            return "trial_call_in_flight"
        if peer["state"] in ("blocked", "failed", "retrying"):
            return "campaign_operator_review_required"
        if peer["state"] == "succeeded" and peer["dispatched_at"] is None:
            return "trial_waiting_for_validation"
    if conn.execute("SELECT 1 FROM ai_cny_events WHERE job_id=ANY(%s) AND kind='unknown' LIMIT 1", ([p["id"] for p in peers],)).fetchone():
        return "campaign_unknown_charge_stop"
    if job["kind"] != "compatibility":
        cap = conn.execute("SELECT compatibility FROM ai_model_catalog WHERE model_id='deepseek-flash'").fetchone()["compatibility"]
        if not cap.get("real_api_verified") or "high" not in cap.get("efforts", []) or cap.get("max_tested_output_tokens", 0) < 32768:
            return "campaign_high_output_probe_required"
        if job["kind"] == "answer_review":
            science = conn.execute("SELECT passed FROM auto_quality_events WHERE question_id=%s AND content_sha256=%s AND phase='science' ORDER BY id DESC LIMIT 1", (job["payload"]["question_id"], job["payload"]["content_sha256"])).fetchone()
            if not science or not science["passed"]:
                return "campaign_scientific_review_required"
    return None


def execution_guard(job):
    guard, _ = check_job(job)
    if job["state"] != "running" or job["attempts"] != 1:
        raise ValueError("campaign_committed_single_attempt_required")
    return wire_guard(job["payload"], guard["policy"]["system_sha256"], "high", 32768)


def contract_retry_allowed(rejection, contract):
    """Only a new output-contract version can retry metadata-only rejection.

    It never changes the old event or accepts an answer locally. The next
    independent provider response still must pass the complete formal gate.
    """
    from app import auto_quality
    from app.question_quality import AXES
    finding = rejection["result"].get("finding", {})
    issues = set(rejection["result"].get("issues", []))
    previous = auto_quality.job_contract_version(rejection)
    checks = finding.get("checks", {})
    if not (contract > previous and finding.get("passed") is True and not finding.get("unresolved") and set(checks) == set(AXES)):
        return False
    if issues and issues <= {"unresolved_source_risk", "knowledge_mapping_not_evidenced"} and all(checks.values()):
        return True
    # An internally contradictory PASS=true / duplication=false can be sent
    # once to a new independent run with explicit polarity, never relabelled.
    return (contract == 3 and previous == 2 and issues == {"unresolved_quality_findings"}
            and checks["duplication"] is False and all(v for k,v in checks.items() if k != "duplication"))


def enqueue_review(conn, uid, qid, sid):
    from app import ai_jobs, auto_quality
    campaign = conn.execute("SELECT policy FROM ai_review_campaigns WHERE scope_id=%s", (sid,)).fetchone()
    if not campaign:
        raise ValueError("unknown_review_campaign")
    q, refs, style = auto_quality.evidence(conn, qid)
    checksum = content_sha(q)
    contract = campaign["policy"].get("contract_version", 1)
    rejected = conn.execute("SELECT e.result,j.settings_snapshot FROM auto_quality_events e JOIN ai_jobs j ON j.id=e.job_id WHERE e.question_id=%s AND e.content_sha256=%s AND e.phase IN ('science','answer') AND NOT e.passed", (qid, checksum)).fetchall()
    for rejection in rejected:
        if not contract_retry_allowed(rejection, contract):
            raise ValueError("revision_required_after_scientific_rejection")
    payloads = [(phase, auto_quality.review_payload(phase, qid, checksum, q, refs, style, contract)) for phase in ("science", "answer")]
    if any(campaign["policy"]["question_payloads"].get(f"{qid}:{phase}") != content_sha(p) for phase, p in payloads):
        raise ValueError("approved_campaign_evidence_changed")
    if not auto_quality.preflight(conn, qid)["passed"]:
        raise ValueError("campaign_identity_preflight_failed")
    jobs = []
    for phase, payload in payloads:
        job = ai_jobs.enqueue(conn, uid, f"campaign:{sid}:{qid}:{phase}", phase + "_review", payload)
        bind(conn, job["id"], sid)
        jobs.append(job)
    conn.execute("UPDATE auto_question_states SET status='checking',reason=NULL,updated_at=clock_timestamp() WHERE question_id=%s", (qid,))
    return jobs


def run_once(get_db, sid):
    from app import ai_jobs
    from app.harness_gateway import execute
    from app.auto_worker import dispatch_completed
    ran = ai_jobs.run_once(get_db, execute, scope_id=sid, scope_kind="review_campaign")
    if ran:
        with get_db() as conn:
            dispatch_completed(conn, scope_id=sid, scope_kind="review_campaign")
    with get_db() as conn:
        row = conn.execute("SELECT policy FROM ai_review_campaigns WHERE scope_id=%s", (sid,)).fetchone()
        v2 = row and row["policy"].get("contract_version", 1) >= 2
        if v2:
            # A completed scientific rejection is not a transport failure.
            # Preserve it, skip only that question's answer round, and continue
            # the finite manifest. No automatic revision or optimistic ready.
            conn.execute("UPDATE ai_jobs j SET state='cancelled',error_code='science_review_rejected',finished_at=clock_timestamp() WHERE j.state='pending' AND j.kind='answer_review' AND j.settings_snapshot->'review_campaign'->>'scope_id'=%s AND EXISTS(SELECT 1 FROM auto_quality_events e WHERE e.question_id=(j.payload->>'question_id')::bigint AND e.content_sha256=j.payload->>'content_sha256' AND e.phase='science' AND NOT e.passed AND e.id=(SELECT max(last.id) FROM auto_quality_events last WHERE last.question_id=e.question_id AND last.content_sha256=e.content_sha256 AND last.phase='science'))", (sid,))
        # Includes admission failures before a request and scientific rejection.
        bad = conn.execute("SELECT 1 FROM ai_jobs j LEFT JOIN auto_quality_events e ON e.job_id=j.id WHERE j.settings_snapshot->'review_campaign'->>'scope_id'=%s AND (j.state IN ('blocked','failed','retrying') OR (%s AND e.passed=false)) LIMIT 1", (sid, not v2)).fetchone()
        if bad:
            pause(conn, sid)
    return ran


def recover_dispatch(conn, job_id, reason):
    """Explicit offline recovery of a complete response lost to dispatch rollback.

    Never calls a provider or changes response, usage, attempts or money records.
    Any failed scientific gate rolls the entire recovery transaction back.
    """
    from app import auto_quality
    if not isinstance(reason, str) or not 30 <= len(reason) <= 500:
        raise ValueError("recovery_reason_required")
    with conn.transaction():
        job = conn.execute("SELECT * FROM ai_jobs WHERE id=%s FOR UPDATE", (job_id,)).fetchone()
        if not job:
            raise ValueError("unknown_recovery_job")
        guard, _ = check_job(job)
        approved = conn.execute("SELECT * FROM ai_review_campaigns WHERE scope_id=%s", (guard["scope_id"],)).fetchone()
        if not approved or approved["user_id"] != job["user_id"] or approved["policy"] != guard["policy"]:
            raise ValueError("campaign_authorization_mismatch")
        result = job.get("result") or {}
        diagnostic = result.get("diagnostic", {})
        if (job["state"] != "blocked" or job["error_code"] != "invalid_or_uncertain_result"
            or job["attempts"] != 1 or job["billing_status"] != "usage_estimate"
            or result.get("real_api") is not True or result.get("returned_model_id") != "deepseek-flash"
            or not auto_quality.complete_usage(result.get("usage"))
            or diagnostic.get("sdk_finish_reason") != "completed"
            or diagnostic.get("receipt", {}).get("provider_stop_reason") != "end_turn"):
            raise ValueError("complete_accounted_recovery_response_required")
        auto_quality.Finding.model_validate(result["response"])
        if conn.execute("SELECT 1 FROM auto_quality_events WHERE job_id=%s AND phase IN ('science','answer')", (job_id,)).fetchone():
            raise ValueError("existing_formal_result_cannot_be_relabelled")
        calls = conn.execute("SELECT * FROM ai_call_events WHERE job_id=%s", (job_id,)).fetchall()
        if (len(calls) != 1 or calls[0]["provider_request_confirmed"] is not True
            or calls[0]["usage"] != result["usage"] or calls[0]["model_id"] != "deepseek-flash"
            or calls[0]["effort"] != "high" or calls[0]["outcome"] != "succeeded"):
            raise ValueError("single_persisted_provider_call_required")
        qid = job["payload"]["question_id"]
        if not auto_quality.preflight(conn, qid)["passed"]:
            raise ValueError("recovery_identity_failed")
        before = {k: job[k] for k in ("state", "error_code", "billing_status", "attempts")}
        before["result_sha256"] = content_sha(result)
        conn.execute("UPDATE ai_jobs SET state='succeeded',error_code=NULL WHERE id=%s", (job_id,))
        conn.execute("UPDATE auto_question_states SET status='checking',reason=NULL,updated_at=clock_timestamp() WHERE question_id=%s", (qid,))
        stored = conn.execute("SELECT * FROM ai_jobs WHERE id=%s", (job_id,)).fetchone()
        if not auto_quality.accept_round(conn, stored):
            raise ValueError("recovery_formal_quality_failed")
        phases = conn.execute("SELECT count(DISTINCT phase) AS n FROM auto_quality_events WHERE question_id=%s AND content_sha256=%s AND passed AND phase IN ('science','answer')", (qid, job["payload"]["content_sha256"])).fetchone()["n"]
        if phases == 2 and not auto_quality.promote(conn, qid):
            raise ValueError("recovery_ready_gate_failed")
        conn.execute(
            "INSERT INTO auto_quality_events(question_id,content_sha256,policy_version,phase,run_key,job_id,evidence_snapshot,result,passed) VALUES(%s,%s,%s,'dispatch_recovery',%s,%s,'[]',%s,true)",
            (qid, job["payload"]["content_sha256"], auto_quality.POLICY, f"job:{job_id}:offline-dispatch-recovery", job_id,
             Jsonb(dict(label="application_state_recovery_not_new_model_call", before=before, reason=reason))),
        )
        return dict(job_id=job_id, question_id=qid, ready=auto_quality.ready(conn, qid), new_provider_calls=0)
