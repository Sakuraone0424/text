"""Read-only numbers for the question-writing progress page.

Counts, codes and times only: never a question, a textbook paragraph, a prompt or a secret.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from app import cost_control
from app.auto_generation import STARTUP_RESERVE_DAYS, eligible_reserve_count
from app.training_spec_v4 import COUNTS, SUBJECTS

STALE_AFTER_MINUTES = 15  # the worker is a cron job every 5 minutes
BLOCKED_JOB_DAYS = 14
RATE_WINDOW_HOURS = 48


def _by_bucket(rows):
    out = defaultdict(lambda: defaultdict(int))
    for r in rows:
        out[(r["s"], r["t"])][r["k"]] += r["n"]
    return out


def buckets(conn, uid):
    states = conn.execute(
        "SELECT q.content->>'subject' AS s,q.content->>'question_type' AS t,"
        "CASE WHEN st.status='blocked' THEN 'blocked' ELSE 'reviewing' END AS k,count(*) AS n "
        "FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id "
        "WHERE st.status IN ('candidate','checking','repairing','blocked') GROUP BY 1,2,3"
    ).fetchall()
    state_reasons = conn.execute(
        "SELECT q.content->>'subject' AS s,q.content->>'question_type' AS t,COALESCE(NULLIF(st.reason,''),'unspecified') AS code,count(*) AS n "
        "FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id "
        "WHERE st.status='blocked' GROUP BY 1,2,3"
    ).fetchall()
    jobs = conn.execute(
        "SELECT payload->>'subject' AS s,payload->>'question_type' AS t,"
        "CASE WHEN state IN ('pending','retrying') THEN 'queued' WHEN state='running' THEN 'generating' "
        "WHEN state='succeeded' THEN 'generating' ELSE 'blocked' END AS k,count(*) AS n "
        "FROM ai_jobs WHERE kind='generation' AND ("
        " state IN ('pending','retrying','running') OR (state='succeeded' AND dispatched_at IS NULL)"
        " OR (state='blocked' AND COALESCE(error_code,'')<>'superseded_scoring' AND created_at>clock_timestamp()-%s*interval '1 day')"
        ") GROUP BY 1,2,3",
        (BLOCKED_JOB_DAYS,),
    ).fetchall()
    job_reasons = conn.execute(
        "SELECT payload->>'subject' AS s,payload->>'question_type' AS t,COALESCE(error_code,'unspecified') AS code,count(*) AS n "
        "FROM ai_jobs WHERE kind='generation' AND state='blocked' AND COALESCE(error_code,'')<>'superseded_scoring' "
        "AND created_at>clock_timestamp()-%s*interval '1 day' GROUP BY 1,2,3",
        (BLOCKED_JOB_DAYS,),
    ).fetchall()
    counted = _by_bucket(states)
    for key, values in _by_bucket(jobs).items():
        for k, n in values.items():
            counted[key][k] += n
    reasons = defaultdict(lambda: defaultdict(int))
    for r in list(state_reasons) + list(job_reasons):
        reasons[(r["s"], r["t"])][r["code"]] += r["n"]
    result = []
    for subject in SUBJECTS:
        for kind, required in COUNTS.items():
            c = counted[(subject, kind)]
            result.append(dict(
                subject=subject, question_type=kind, required=required,
                target=cost_control.reserve_target(required, STARTUP_RESERVE_DAYS),
                stocked=eligible_reserve_count(conn, uid, subject, kind),
                queued=c["queued"], generating=c["generating"], reviewing=c["reviewing"], blocked=c["blocked"],
                blocked_reasons=dict(sorted(reasons[(subject, kind)].items(), key=lambda kv: -kv[1])),
            ))
    return result


def recent_jobs(conn, limit=20):
    held = cost_control.held_back_kinds()
    rows = conn.execute(
        "SELECT id,kind,state,error_code,payload->>'subject' AS subject,payload->>'question_type' AS question_type,"
        "COALESCE(finished_at,started_at,created_at) AS at FROM ai_jobs "
        "ORDER BY COALESCE(finished_at,started_at,created_at) DESC,id DESC LIMIT %s",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        reason = r["error_code"]
        if not reason and r["state"] in ("pending", "retrying") and r["kind"] in held:
            reason = "waiting_for_off_peak"
        out.append(dict(id=r["id"], kind=r["kind"], state=r["state"], reason=reason, at=r["at"],
                        subject=r["subject"], question_type=r["question_type"]))
    return out


def worker_status(conn, now):
    row = conn.execute("SELECT * FROM worker_heartbeats ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return dict(last_run_at=None, outcome="never_ran", minutes_ago=None, stale=True)
    minutes = int((now - row["finished_at"]).total_seconds() // 60)
    return dict(
        last_run_at=row["finished_at"], outcome=row["outcome"], calls=row["calls"], cost_cny=str(row["cost_cny"]),
        stopped_because=row["stopped_because"], queue=row["queue"], minutes_ago=minutes,
        stale=minutes > STALE_AFTER_MINUTES,
    )


def approved_recently(conn):
    return conn.execute(
        "SELECT count(*) AS n FROM auto_question_states s JOIN reviewed_questions q ON q.id=s.question_id "
        "WHERE s.status='ready' AND q.status='published' AND s.updated_at>clock_timestamp()-%s*interval '1 hour'",
        (RATE_WINDOW_HOURS,),
    ).fetchone()["n"]


def next_paper(conn, rows, now):
    """How long until the bank holds one complete paper, from the recent rate of approvals."""
    missing = sum(max(0, b["required"] - b["stocked"]) for b in rows)
    admitted = approved_recently(conn)
    rate = admitted / RATE_WINDOW_HOURS
    out = dict(missing_questions=missing, approved_last_hours=RATE_WINDOW_HOURS, approved_in_window=admitted,
               per_hour=round(rate, 3), complete=missing == 0, eta_hours=None, eta_at=None)
    if missing and rate > 0:
        hours = missing / rate
        out["eta_hours"] = round(hours, 1)
        out["eta_at"] = now + __import__("datetime").timedelta(hours=hours)
    return out


def daily_spend(conn, uid):
    """Settled spend per Beijing day this month (for the daily bar chart)."""
    rows = conn.execute(
        "SELECT to_char(e.created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS day,COALESCE(sum(e.amount_cny),0) AS cny "
        "FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id "
        f"WHERE j.user_id=%s AND e.kind='usage_estimate' AND e.created_at>={cost_control.MONTH_START} GROUP BY 1 ORDER BY 1",
        (uid,),
    ).fetchall()
    return [dict(day=r["day"], cny=str(r["cny"])) for r in rows]


def report(conn, uid):
    now = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    rows = buckets(conn, uid)
    spend = cost_control.spend_report(conn, uid)
    return dict(
        generated_at=now,
        buckets=rows,
        totals={k: sum(b[k] for b in rows) for k in ("required", "target", "stocked", "queued", "generating", "reviewing", "blocked")},
        recent_jobs=recent_jobs(conn),
        spend={k: spend[k] for k in (
            "today_cny", "month_cny", "month_counted_cny", "month_cap_cny", "grading_reserve_cny",
            "writing_review_limit_cny", "remaining_writing_review_cny", "remaining_grading_cny",
            "writing_review_exhausted", "ai_quota_exhausted", "message", "admitted_questions",
            "cost_per_admitted_question_cny",
        )} | dict(daily=daily_spend(conn, uid)),
        period=cost_control.peak_outlook(now),
        worker=worker_status(conn, now),
        next_paper=next_paper(conn, rows, now),
    )
