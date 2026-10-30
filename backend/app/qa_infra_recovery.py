"""Conservative replay of explicit ENOSPC failures, never semantic rejections.

A new job has a new key and a fresh frozen grant. Historical jobs, billing and
provider receipts remain immutable. Unknown network/billing outcomes are not
automatically replayed.
"""
from pathlib import Path

from app.ai_jobs import enqueue
from app.candidate_review import snapshot_unit
from app.source_policy import pinned_source_bytes

REPLAYABLE_KINDS = frozenset((
    "textbook_analysis", "knowledge_extraction", "generation", "exam_analysis"
))
RECOVERY_SUFFIX = ":recovered-enospc-v1"


def evidence_is_current(conn, job):
    payload = job.get("payload") or {}
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        units = [evidence]
    elif isinstance(evidence, list):
        units = evidence
    else:
        return False
    if job["kind"] == "generation":
        style = payload.get("style")
        if not isinstance(style, list) or not style:
            return False
        units = units + style
    if not units:
        return False
    for item in units:
        if not isinstance(item, dict) or type(item.get("unit_id")) is not int:
            return False
        try:
            if (snapshot_unit(conn, item["unit_id"]) != item
                    or not pinned_source_bytes(item["file_id"], item["source_sha256"])):
                return False
        except (KeyError, ValueError):
            return False
    return True


def receipt_or_unsafe_path_exists(private_root, job):
    """Fail closed for any uncertain receipt or modified evidence location."""
    root = Path(private_root)
    if root.is_symlink() or not root.is_dir():
        return True
    for attempt in range(1, int(job["attempts"]) + 1):
        home = root / f"job-{job['id']}-attempt-{attempt}"
        if home.is_symlink():
            return True
        receipt = home / "provider-receipt"
        if receipt.exists() or receipt.is_symlink():
            return True
        # A remaining task audit may describe a sent request, even when the
        # provider receipt was not yet written.
        audit = home / "task-audit.json"
        if audit.exists() or audit.is_symlink():
            return True
    return False


def replay_enospc(conn, user_id, private_root, limit=2):
    """Replay only local errno 28, no confirmed provider request, fresh evidence.

    Never requeue historical OSError without errno, a lease expiry, a provider
    error, invalid science, or a job with any surviving receipt/audit.
    """
    if limit < 1 or limit > 2:
        raise ValueError("bounded_recovery_limit_required")
    rows = conn.execute(
        "SELECT * FROM ai_jobs WHERE user_id=%s AND state='blocked' "
        "AND error_code='OSError' "
        "AND result->'execution_failure'->>'errno'='28' "
        "AND kind = ANY(%s) AND NOT EXISTS (SELECT 1 FROM ai_call_events c "
        "WHERE c.job_id=ai_jobs.id AND c.provider_request_confirmed=true) "
        "ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",
        (user_id, list(REPLAYABLE_KINDS), limit),
    ).fetchall()
    count = 0
    for job in rows:
        if (not job["job_key"] or job["job_key"].endswith(RECOVERY_SUFFIX)
                or job["kind"] not in REPLAYABLE_KINDS
                or receipt_or_unsafe_path_exists(private_root, job)
                or not evidence_is_current(conn, job)):
            continue
        new_key = job["job_key"] + RECOVERY_SUFFIX
        # A deterministic new key permits one replay, and enqueue() checks
        # payload/kind/owner identity on conflicting retries.
        enqueue(conn, user_id, new_key, job["kind"], job["payload"])
        count += 1
    return count
