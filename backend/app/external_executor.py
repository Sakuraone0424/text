"""Work done outside the website's own DeepSeek worker: a Claude Code scheduled task (Routine) leases queued tasks over an API, runs
them on the owner's Claude subscription and submits the result.

What does not change: the task a Routine receives is exactly the payload DeepSeek would be sent (rules, textbook paragraphs, past-exam style
examples, output schema, the question's full score); its result goes through exactly the same program checks as a DeepSeek result (it IS
dispatched by the same code: citation matching, point sums, wording checks, one repair ...). What differs: nothing is billed (the call
events and CNY ledger are never touched), the executor is recorded as claude-subscription with the model label as sent, and a session never
reviews a question that the same session wrote.
"""

import os
import threading
import time
import uuid
from collections import deque

from psycopg.types.json import Jsonb

LEASE_MINUTES = 30
MAX_PER_LEASE = 10
RESULT_LIMIT_BYTES = 131072
MAX_EXPIRED_LEASES = 3                 # a task that keeps timing out in Routines is left to DeepSeek's fallback
CLAUDE = "claude-subscription"
DEEPSEEK = "deepseek"
MODES = ("deepseek", "claude", "claude_first")
TASKS = ("generation", "review", "grading")
STATE_KEY = "executor_routing"
# DeepSeek does everything until the owner has seen a Routine run through and switches "出题" to Claude first himself
DEFAULTS = {"generation": "deepseek", "review": "deepseek", "grading": "deepseek", "fallback_hours": 4}
ENV_FOR = {"generation": "EXECUTOR_GENERATION", "review": "EXECUTOR_REVIEW", "grading": "EXECUTOR_GRADING"}

# which queued jobs belong to which kind of task a Routine can lease
FAMILY_SQL = {
    "generation": "(kind='generation' OR (kind='repair' AND payload ? 'repair_of_job'))",
    "review": "kind='quality_review'",
    "grading": "kind='grading'",
}


# A question written for the blind comparison is reviewed by DeepSeek whatever the review route says (both sides get the same reviewer).
COMPARISON_REVIEW_SQL = (
    "EXISTS(SELECT 1 FROM candidate_review_packets cp JOIN ai_jobs cg ON starts_with(cp.candidate_key, 'auto-generated-' || cg.id || '-') "
    "WHERE cp.question_id=(ai_jobs.payload->>'question_id')::bigint AND starts_with(cg.job_key, 'compare:'))")


class ExternalError(Exception):
    """Carries an HTTP status and a fixed, application-owned code."""

    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


# ---- authentication and rate limit ------------------------------------------------------------------------------------

_HITS = deque()
_HITS_LOCK = threading.Lock()
RATE_LIMIT_PER_MINUTE = 60


def check_token(supplied):
    """The token is its own secret (EXTERNAL_EXECUTOR_TOKEN), separate from the login. Never logged."""
    from app.auth import same_secret

    configured = os.environ.get("EXTERNAL_EXECUTOR_TOKEN", "")
    if not configured:
        raise ExternalError(503, "external_executor_disabled")
    if not supplied or not same_secret(supplied, configured):
        raise ExternalError(401, "external_token_invalid")


def rate_limit(now=None):
    now = time.monotonic() if now is None else now
    with _HITS_LOCK:
        while _HITS and now - _HITS[0] > 60:
            _HITS.popleft()
        if len(_HITS) >= RATE_LIMIT_PER_MINUTE:
            raise ExternalError(429, "external_rate_limited")
        _HITS.append(now)


def reset_rate_limit():
    with _HITS_LOCK:
        _HITS.clear()


# ---- who does which kind of task ----------------------------------------------------------------------------------------

def policy(conn):
    """{generation, review, grading: deepseek | claude | claude_first, fallback_hours}: the saved setting, else the environment, else the default."""
    from app import legacy_reuse

    saved = legacy_reuse._state(conn, STATE_KEY) or {}
    out = {}
    for task in TASKS:
        value = saved.get(task) or os.environ.get(ENV_FOR[task]) or DEFAULTS[task]
        out[task] = value if value in MODES else DEFAULTS[task]
    hours = saved.get("fallback_hours") or os.environ.get("EXECUTOR_FALLBACK_HOURS") or DEFAULTS["fallback_hours"]
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = DEFAULTS["fallback_hours"]
    out["fallback_hours"] = hours if 1 <= hours <= 72 else DEFAULTS["fallback_hours"]
    return out


def save_policy(conn, values):
    from app import legacy_reuse

    current = policy(conn)
    for task in TASKS:
        if task in values:
            if values[task] not in MODES:
                raise ValueError("unknown_executor_mode")
            current[task] = values[task]
    if "fallback_hours" in values:
        if type(values["fallback_hours"]) is not int or not 1 <= values["fallback_hours"] <= 72:
            raise ValueError("fallback_hours_out_of_range")
        current["fallback_hours"] = values["fallback_hours"]
    legacy_reuse._set_state(conn, STATE_KEY, current)
    return current


def waiting_for_claude(conn):
    """Tasks that only a Routine may take for now (routed to Claude, or pinned to it), not leased at the moment: how many there are, how long the
    oldest has waited and, under 'Claude first', in how many hours DeepSeek takes them over. None when nothing waits."""
    pol = policy(conn)
    parts, params = [], []
    for task in TASKS:
        mode = pol[task]
        if mode == "deepseek":
            parts.append(f"({FAMILY_SQL[task]} AND executor='claude-subscription')")
        else:
            parts.append(f"({FAMILY_SQL[task]} AND (executor='claude-subscription' OR executor IS NULL))")
    row = conn.execute(
        "SELECT count(*) AS n,EXTRACT(EPOCH FROM clock_timestamp()-min(created_at))/3600 AS oldest_hours FROM ai_jobs "
        "WHERE state IN ('pending','retrying') AND lease_token IS NULL AND (" + " OR ".join(parts) + ") "
        "AND NOT EXISTS(SELECT 1 FROM external_leases l WHERE l.job_id=ai_jobs.id AND l.state='active' AND l.expires_at>clock_timestamp())"
    ).fetchone()
    if not row["n"]:
        return None
    fallback = pol["fallback_hours"] if "claude_first" in (pol["generation"], pol["review"], pol["grading"]) else None
    left = None if fallback is None else max(0.0, round(fallback - float(row["oldest_hours"] or 0), 1))
    return dict(count=row["n"], oldest_hours=round(float(row["oldest_hours"] or 0), 1), hand_over_hours=left, fallback_hours=fallback)


def deepseek_hold(conn):
    """(SQL, params) appended to the DeepSeek worker's claim query: a task pinned to Claude, one a Routine is working on, and one whose
    kind is routed to Claude (until the fallback time of 'claude first' has passed) are not claimed by the DeepSeek worker."""
    pol = policy(conn)
    sql = (" AND COALESCE(executor,'deepseek')<>'claude-subscription'"
           " AND NOT EXISTS(SELECT 1 FROM external_leases l WHERE l.job_id=ai_jobs.id AND l.state='active' AND l.expires_at>clock_timestamp())")
    params = []
    for task in TASKS:
        mode = pol[task]
        exempt = f" AND NOT {COMPARISON_REVIEW_SQL}" if task == "review" else ""
        if mode == "claude":
            sql += f" AND NOT (executor IS NULL AND {FAMILY_SQL[task]}{exempt})"
        elif mode == "claude_first":
            sql += f" AND NOT (executor IS NULL AND {FAMILY_SQL[task]}{exempt} AND created_at>clock_timestamp()-make_interval(hours => %s))"
            params.append(pol["fallback_hours"])
    return sql, params


# ---- leasing ---------------------------------------------------------------------------------------------------------------

def expire_leases(conn):
    return conn.execute(
        "UPDATE external_leases SET state='expired',finished_at=clock_timestamp() WHERE state='active' AND expires_at<=clock_timestamp()"
    ).rowcount


def _satisfied(conn):
    from app import auto_generation, cost_control

    if os.environ.get("AI_SKIP_SATISFIED_BUCKETS", "1") == "0":
        return []
    owners = conn.execute("SELECT DISTINCT user_id FROM ai_jobs WHERE state IN ('pending','retrying') AND kind=ANY(%s)",
                          (list(cost_control.OFF_PEAK_ONLY),)).fetchall()
    keys = []
    for owner in owners:
        keys += auto_generation.satisfied_keys(conn, owner["user_id"])
    return keys


def _eligible_sql(task, session, pol):
    mode = pol[task]
    route = "executor='claude-subscription'" + (" OR executor IS NULL" if mode in ("claude", "claude_first") else "")
    sql = (f" AND {FAMILY_SQL[task]} AND ({route})"
           " AND NOT EXISTS(SELECT 1 FROM external_leases l WHERE l.job_id=ai_jobs.id AND l.state='active' AND l.expires_at>clock_timestamp())"
           f" AND (SELECT count(*) FROM external_leases e WHERE e.job_id=ai_jobs.id AND e.state='expired')<{MAX_EXPIRED_LEASES}"
           " AND NOT (settings_snapshot ?| ARRAY['limited_trial','review_campaign','qa_automation'])")
    params = []
    if task == "review":
        # review independence: the session that wrote a question never reviews it; the comparison's questions are DeepSeek's to review
        sql += (" AND NOT EXISTS(SELECT 1 FROM external_question_sessions es WHERE es.question_id=(payload->>'question_id')::bigint"
                " AND es.session=%s) AND NOT " + COMPARISON_REVIEW_SQL)
        params.append(session)
    return sql, params


def _claimable_where(conn, task, session):
    pol = policy(conn)
    sql, params = _eligible_sql(task, session, pol)
    satisfied = _satisfied(conn) if task in ("generation", "review") else []
    if satisfied:
        sql += (" AND NOT ((COALESCE(payload->>'subject',payload->'question'->>'subject') || '|' "
                "|| COALESCE(payload->>'question_type',payload->'question'->>'question_type'))=ANY(%s))")
        params.append(satisfied)
    return sql, params


def lease(conn, task, max_n, session):
    """Hand out up to max_n tasks of one kind to a session. The task content is the job's payload, untouched."""
    if task not in TASKS:
        raise ExternalError(422, "unknown_task_kind")
    if not isinstance(session, str) or not 6 <= len(session) <= 80 or not session.replace("-", "").replace("_", "").isalnum():
        raise ExternalError(422, "session_id_invalid")
    from app.harness_runner import build_prompt

    max_n = max(1, min(int(max_n), MAX_PER_LEASE))
    expire_leases(conn)
    sql, params = _claimable_where(conn, task, session)
    rows = conn.execute(
        "SELECT * FROM ai_jobs WHERE state IN ('pending','retrying') AND next_retry<=clock_timestamp()" + sql
        + " AND NOT (payload ? 'question_id' AND EXISTS(SELECT 1 FROM ai_jobs r WHERE r.state='running' AND r.id<>ai_jobs.id"
        " AND r.payload->>'question_id'=ai_jobs.payload->>'question_id'))"
        " ORDER BY CASE kind WHEN 'grading' THEN 1 WHEN 'quality_review' THEN 2 WHEN 'repair' THEN 2 ELSE 3 END,id FOR UPDATE SKIP LOCKED LIMIT %s",
        tuple(params) + (max_n,),
    ).fetchall()
    out = []
    for job in rows:
        lease_id = uuid.uuid4().hex
        row = conn.execute(
            "INSERT INTO external_leases(lease_id,job_id,kind,session,expires_at) VALUES (%s,%s,%s,%s,clock_timestamp()+make_interval(mins => %s)) "
            "RETURNING expires_at", (lease_id, job["id"], task, session, LEASE_MINUTES)).fetchone()
        p = job["payload"]
        out.append(dict(
            lease_id=lease_id, expires_at=row["expires_at"], kind=task,
            subject=p.get("subject") or (p.get("question") or {}).get("subject"),
            question_type=p.get("question_type") or (p.get("question") or {}).get("question_type"),
            max_points=p.get("max_points") or (p.get("question") or {}).get("max_points"),
            payload=p,        # byte for byte what DeepSeek is sent: rules, textbook paragraphs, style examples, schema
            prompt=build_prompt(p),       # ... and the very message built from it (the same function the DeepSeek call uses)
        ))
    return out


def release(conn, lease_id):
    row = conn.execute("UPDATE external_leases SET state='released',finished_at=clock_timestamp() WHERE lease_id=%s AND state='active' RETURNING lease_id",
                       (lease_id,)).fetchone()
    if not row:
        exists = conn.execute("SELECT state FROM external_leases WHERE lease_id=%s", (lease_id,)).fetchone()
        if not exists:
            raise ExternalError(404, "lease_not_found")
        raise ExternalError(409, "lease_not_active_" + exists["state"])
    return {"released": True}


# ---- submitting ------------------------------------------------------------------------------------------------------------

def _question_of_job(conn, job_id):
    row = conn.execute("SELECT question_id FROM candidate_review_packets WHERE candidate_key LIKE %s ORDER BY question_id DESC LIMIT 1",
                       (f"auto-generated-{job_id}-%",)).fetchone()
    return row["question_id"] if row else None


def submit(conn, lease_id, session, model_label, result):
    """Store a Routine's result as the job's result and run it through the very same checks a DeepSeek result goes through."""
    import json

    from app import auto_worker

    if not isinstance(result, dict):
        raise ExternalError(422, "result_must_be_a_json_object")
    if not isinstance(model_label, str) or not 1 <= len(model_label) <= 120:
        raise ExternalError(422, "model_label_invalid")
    if len(json.dumps(result, ensure_ascii=False).encode()) > RESULT_LIMIT_BYTES:
        raise ExternalError(413, "result_too_large")
    row = conn.execute("SELECT * FROM external_leases WHERE lease_id=%s FOR UPDATE", (lease_id,)).fetchone()
    if not row:
        raise ExternalError(404, "lease_not_found")
    if row["session"] != session:
        raise ExternalError(403, "lease_belongs_to_another_session")
    if row["state"] == "expired":
        raise ExternalError(410, "lease_expired")
    if row["state"] != "active":
        raise ExternalError(409, "lease_not_active_" + row["state"])
    expired = conn.execute("SELECT expires_at<=clock_timestamp() AS gone FROM external_leases WHERE lease_id=%s", (lease_id,)).fetchone()["gone"]
    if expired:
        conn.execute("UPDATE external_leases SET state='expired',finished_at=clock_timestamp() WHERE lease_id=%s", (lease_id,))
        raise ExternalError(410, "lease_expired")
    job = conn.execute("SELECT * FROM ai_jobs WHERE id=%s FOR UPDATE", (row["job_id"],)).fetchone()
    if job["state"] not in ("pending", "retrying"):
        conn.execute("UPDATE external_leases SET state='released',finished_at=clock_timestamp() WHERE lease_id=%s", (lease_id,))
        raise ExternalError(409, "job_no_longer_open")
    stored = dict(real_api=True, executor=CLAUDE, model_label=model_label, session=session, response=result,
                  usage=dict(input_tokens=0, output_tokens=0, cache_read_input_tokens=0), json_extracted=False)
    # cost 0: no reservation, no call event, no ledger entry (cny_billing is never touched)
    conn.execute(
        "UPDATE ai_jobs SET state='succeeded',result=%s,error_code=NULL,attempts=attempts+1,executor=%s,ext_session=%s,ext_model_label=%s,"
        "started_at=COALESCE(started_at,%s),finished_at=clock_timestamp(),lease_token=NULL,lease_until=NULL WHERE id=%s",
        (Jsonb(stored), CLAUDE, session, model_label, row["created_at"], job["id"]),
    )
    try:
        with conn.transaction():
            auto_worker.dispatch_completed(conn, only_job_id=job["id"])
    except Exception as exc:  # noqa: BLE001  (a result the checks cannot even read is reported, never a 500)
        conn.execute("UPDATE ai_jobs SET state='blocked',error_code='invalid_or_uncertain_result',dispatched_at=clock_timestamp() WHERE id=%s", (job["id"],))
        outcome, detail = "blocked", dict(code=type(exc).__name__)
    else:
        done = conn.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
        failure = (done["result"] or {}).get("dispatch_failure")
        if failure and failure.get("code") == "repair_requested":
            outcome, detail = "repair_requested", failure
        elif done["state"] == "blocked" or failure:
            outcome, detail = "blocked", failure or dict(code=done["error_code"])
        else:
            outcome, detail = "passed", {}
            qid = (done["result"] or {}).get("repaired_question") or _question_of_job(conn, job["id"])
            if qid and row["kind"] == "generation":
                conn.execute("INSERT INTO external_question_sessions(question_id,session,job_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                             (qid, session, job["id"]))
                detail = dict(question_id=qid)
    conn.execute("UPDATE external_leases SET state='submitted',finished_at=clock_timestamp(),model_label=%s,outcome=%s,outcome_detail=%s WHERE lease_id=%s",
                 (model_label, outcome, Jsonb(detail), lease_id))
    return dict(lease_id=lease_id, passed=outcome == "passed", outcome=outcome,
                reasons=[e.get("reason") or e.get("code") for e in (detail.get("errors") or [])] or ([detail.get("code")] if detail.get("code") else []),
                detail={k: v for k, v in detail.items() if k in ("code", "errors")})


# ---- status and per-executor figures ----------------------------------------------------------------------------------------

def executor_stats(conn):
    """Today's (Beijing day) writing tasks per executor: done, program-check pass rate, average seconds."""
    rows = conn.execute(
        "SELECT COALESCE(executor,'deepseek') AS executor,count(*) AS done,"
        "count(*) FILTER (WHERE state='succeeded' AND dispatched_at IS NOT NULL AND COALESCE(result->'dispatch_failure'->>'code','')='') AS passed,"
        "avg(EXTRACT(EPOCH FROM (finished_at-started_at))) FILTER (WHERE started_at IS NOT NULL) AS seconds "
        "FROM ai_jobs WHERE (kind='generation' OR (kind='repair' AND payload ? 'repair_of_job')) AND finished_at IS NOT NULL "
        "AND (finished_at AT TIME ZONE 'Asia/Shanghai')::date=(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date "
        "AND COALESCE(payload->>'origin','')<>'past_exam' GROUP BY 1").fetchall()
    out = {name: dict(done=0, passed=0, pass_rate=None, avg_seconds=None) for name in (DEEPSEEK, CLAUDE)}
    for r in rows:
        out[r["executor"]] = dict(done=r["done"], passed=r["passed"], pass_rate=round(r["passed"] / r["done"], 3) if r["done"] else None,
                                  avg_seconds=round(float(r["seconds"]), 1) if r["seconds"] is not None else None)
    return out


def status(conn):
    expire_leases(conn)
    pol = policy(conn)
    claimable = {}
    for task in TASKS:
        sql, params = _claimable_where(conn, task, "status-probe")
        claimable[task] = conn.execute(
            "SELECT count(*) AS n FROM ai_jobs WHERE state IN ('pending','retrying') AND next_retry<=clock_timestamp()" + sql, tuple(params)).fetchone()["n"]
    return dict(claimable=claimable, routing=pol, today=executor_stats(conn),
                open_leases=conn.execute("SELECT count(*) AS n FROM external_leases WHERE state='active'").fetchone()["n"])
