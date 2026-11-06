"""Execution guard for ordinary automatic jobs (no frozen owner scope).

The frozen scopes (limited trial, review campaign, QA automation) each approve
one finite, pre-agreed set of requests. The automatic pipeline instead runs
under standing authorization: deployment environment flags, a verified model,
CNY budgets and a bounded job vocabulary. This guard enforces what can be
checked at the moment of the call:

- only the job kinds of the question pipeline may reach the model; the
  corpus-wide sweeps (knowledge/textbook/exam analysis) never execute here;
- the model, effort and output limit are exactly the ones the job froze;
- one question's textbook material is hard-capped (units and characters), so
  a request can never carry more than a few relevant paragraphs;
- the wire validator from `limited_trial.wire_guard` checks the real SDK body:
  approved system prompt, a single user message equal to the approved prompt,
  no tools, no hidden runtime context.

Environment authorization (DEEPSEEK_API_AUTHORIZED / PRIVATE_DATA_AUTHORIZED)
is checked by the parent process before this guard runs; the isolated child
deliberately does not receive those variables.
"""

import json

from app.qa_automation import SYSTEM_SHA

# One question's textbook material: at most this many paragraphs / characters.
MAX_EVIDENCE_UNITS = 4
MAX_EVIDENCE_CHARS = 5000
MAX_PAYLOAD_BYTES = 24000
KINDS = frozenset(
    {"compatibility", "generation", "quality_review", "repair", "grading"}
)
TOKEN_LIMITS = (128, 32768)


def bounded_material(payload):
    """Refuse a request that carries more than one question's worth of textbook."""
    if len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload_exceeds_hard_cap")
    evidence = payload.get("evidence")
    if evidence is None:
        return
    if (
        not isinstance(evidence, list)
        or len(evidence) > MAX_EVIDENCE_UNITS
        or any(not isinstance(unit, dict) for unit in evidence)
        or sum(len(str(unit.get("text", ""))) for unit in evidence) > MAX_EVIDENCE_CHARS
    ):
        raise ValueError("evidence_exceeds_hard_cap")


def execution_guard(job):
    from app.limited_trial import COMPAT_INSTRUCTION, wire_guard
    from app.model_router import EFFORTS

    s = job["settings_snapshot"]
    if job.get("kind") not in KINDS:
        raise ValueError("unsupported_automatic_job_kind")
    if job.get("state") != "running" or not 1 <= job.get("attempts", 0) - job.get("transient_attempts", 0) <= s.get("retry_limit", 0) + 1:
        raise ValueError("leased_automatic_attempt_required")
    if (
        s.get("model_id") != "deepseek-flash"
        or s.get("effort") not in EFFORTS
        or type(s.get("token_limit")) is not int
        or not TOKEN_LIMITS[0] <= s["token_limit"] <= TOKEN_LIMITS[1]
    ):
        raise ValueError("unapproved_automatic_parameters")
    payload = job.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload_required")
    if job["kind"] == "compatibility":
        if set(payload) != {"instructions", "catalog_metadata"} or payload["instructions"] != COMPAT_INSTRUCTION:
            raise ValueError("unapproved_compatibility_input")
    else:
        bounded_material(payload)
    return wire_guard(payload, SYSTEM_SHA, s["effort"], s["token_limit"])
