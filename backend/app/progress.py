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


def _current_points(type_expr, points_expr):
    """SQL: the question's point value is the current specification's value for its type."""
    from app.training_spec_v4 import POINTS

    cases = " ".join(f"WHEN '{kind}' THEN {points}" for kind, points in POINTS.items())
    return f"(({points_expr})::int IS NOT DISTINCT FROM (CASE {type_expr} {cases} END))"


# Only work created under the current specification counts in the stages. A job or question written on an
# earlier point scale (old 2/2/4/9 points, the GPT-era trials) belongs to the folded "history" line instead.
JOB_CURRENT = _current_points("payload->>'question_type'", "payload->>'max_points'")
QUESTION_CURRENT = _current_points("q.content->>'question_type'", "q.content->>'max_points'")


def legacy_summary(conn):
    """Blocked work from before the current specification, folded into one line with reasons."""
    jobs = conn.execute(
        "SELECT COALESCE(result->'dispatch_failure'->>'code',error_code,'unspecified') AS code,count(*) AS n FROM ai_jobs "
        f"WHERE state='blocked' AND kind IN ('generation','quality_review','repair') AND NOT {JOB_CURRENT} "
        "AND COALESCE(error_code,'')<>'superseded_scoring' GROUP BY 1"
    ).fetchall()
    states = conn.execute(
        "SELECT COALESCE(NULLIF(st.reason,''),'unspecified') AS code,count(*) AS n FROM auto_question_states st "
        f"JOIN reviewed_questions q ON q.id=st.question_id WHERE st.status='blocked' AND NOT {QUESTION_CURRENT} GROUP BY 1"
    ).fetchall()
    merged = defaultdict(int)
    for r in list(jobs) + list(states):
        merged[r["code"]] += r["n"]
    return dict(count=sum(merged.values()), reasons=[dict(code=k, count=v) for k, v in sorted(merged.items(), key=lambda kv: -kv[1])])


def admitted_today(conn):
    return {
        (r["s"], r["t"]): r["n"]
        for r in conn.execute(
            "SELECT q.content->>'subject' AS s,q.content->>'question_type' AS t,count(*) AS n "
            "FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id "
            f"WHERE st.status='ready' AND q.status='published' AND {QUESTION_CURRENT} "
            f"AND st.updated_at>={cost_control.DAY_START} GROUP BY 1,2"
        ).fetchall()
    }


def review_pass_rate_today(conn):
    """Of today's finished reviews of current-specification questions, how many passed."""
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE st.status IN ('ready','blocked','retired')) AS reviewed,"
        "count(*) FILTER (WHERE st.status='ready') AS passed "
        "FROM ai_jobs j JOIN reviewed_questions q ON q.id=(j.payload->>'question_id')::bigint "
        "JOIN auto_question_states st ON st.question_id=q.id "
        f"WHERE j.kind='quality_review' AND j.state='succeeded' AND {QUESTION_CURRENT} "
        f"AND j.finished_at>={cost_control.DAY_START}"
    ).fetchone()
    return dict(reviewed=row["reviewed"], passed=row["passed"], rate=round(row["passed"] / row["reviewed"], 4) if row["reviewed"] else None)


def buckets(conn, uid):
    states = conn.execute(
        "SELECT q.content->>'subject' AS s,q.content->>'question_type' AS t,"
        "CASE WHEN st.status='blocked' THEN 'blocked' ELSE 'reviewing' END AS k,count(*) AS n "
        "FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id "
        f"WHERE st.status IN ('candidate','checking','repairing','blocked') AND {QUESTION_CURRENT} GROUP BY 1,2,3"
    ).fetchall()
    state_reasons = conn.execute(
        "SELECT q.content->>'subject' AS s,q.content->>'question_type' AS t,"
        "COALESCE(CASE WHEN st.reason='invalid_or_uncertain_result' THEN "
        " (SELECT j.result->'dispatch_failure'->>'code' FROM ai_jobs j WHERE j.kind IN ('quality_review','repair') "
        "  AND j.payload->>'question_id'=st.question_id::text AND j.result->'dispatch_failure' IS NOT NULL ORDER BY j.id DESC LIMIT 1) END,"
        " NULLIF(st.reason,''),'unspecified') AS code,count(*) AS n "
        "FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id "
        f"WHERE st.status='blocked' AND {QUESTION_CURRENT} GROUP BY 1,2,3"
    ).fetchall()
    jobs = conn.execute(
        "SELECT payload->>'subject' AS s,payload->>'question_type' AS t,"
        "CASE WHEN state IN ('pending','retrying') THEN 'queued' WHEN state='running' THEN 'generating' "
        "WHEN state='succeeded' THEN 'generating' ELSE 'blocked' END AS k,count(*) AS n "
        f"FROM ai_jobs WHERE kind='generation' AND {JOB_CURRENT} AND ("
        " state IN ('pending','retrying','running') OR (state='succeeded' AND dispatched_at IS NULL)"
        " OR (state='blocked' AND COALESCE(error_code,'')<>'superseded_scoring' AND created_at>clock_timestamp()-%s*interval '1 day')"
        ") GROUP BY 1,2,3",
        (BLOCKED_JOB_DAYS,),
    ).fetchall()
    job_reasons = conn.execute(
        "SELECT payload->>'subject' AS s,payload->>'question_type' AS t,"
        "COALESCE(result->'dispatch_failure'->>'code',error_code,'unspecified') AS code,count(*) AS n "
        f"FROM ai_jobs WHERE kind='generation' AND state='blocked' AND {JOB_CURRENT} AND COALESCE(error_code,'')<>'superseded_scoring' "
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
    today = admitted_today(conn)
    first_batch = cost_control.bank_target() is not None
    result = []
    for subject in SUBJECTS:
        for kind, required in COUNTS.items():
            c = counted[(subject, kind)]
            result.append(dict(
                subject=subject, question_type=kind, required=required,
                target=cost_control.reserve_target(required, STARTUP_RESERVE_DAYS),
                target_kind="first_batch" if first_batch else "two_day_reserve",
                stocked=eligible_reserve_count(conn, uid, subject, kind), admitted_today=today.get((subject, kind), 0),
                queued=c["queued"], generating=c["generating"], reviewing=c["reviewing"], blocked=c["blocked"],
                blocked_reasons=dict(sorted(reasons[(subject, kind)].items(), key=lambda kv: -kv[1])),
            ))
    return result


SUBJECT_NAME = {"biochemistry": "生化", "cell_biology": "细胞"}
TYPE_NAME = {"single_choice": "单选", "fill_blank": "填空", "term": "名词解释", "short_answer": "简答", "essay": "问答"}
KIND_NAME = {
    "generation": "出题", "quality_review": "审核", "repair": "修复", "grading": "判分", "compatibility": "兼容性探测",
    "science_review": "科学审核", "answer_review": "答案审核", "knowledge_extraction": "知识提取",
    "textbook_analysis": "教材分析", "exam_analysis": "真题分析", "explanation": "讲解", "study_plan": "学习计划",
}


def _bucket_name(subject, qtype):
    return (SUBJECT_NAME.get(subject, "") + TYPE_NAME.get(qtype, "")) if qtype else ""


def failure_detail(result):
    """The safe reason recorded when a finished call's result could not be used (codes only)."""
    failure = (result or {}).get("dispatch_failure") if isinstance(result, dict) else None
    if not isinstance(failure, dict) or not failure.get("code"):
        return None
    parts = [failure["code"]]
    for error in failure.get("errors") or []:
        parts.append(":".join(x for x in (error.get("field"), error.get("reason") or error.get("type")) if x))
    parts += failure.get("reasons") or []
    return dict(code=failure["code"], text="；".join(dict.fromkeys(p for p in parts if p)))


def recent_jobs(conn, limit=20):
    held = cost_control.held_back_kinds()
    rows = conn.execute(
        "SELECT id,kind,state,attempts,error_code,next_retry,dispatched_at,payload->>'subject' AS subject,"
        "payload->>'question_type' AS question_type,payload->>'origin' AS origin,payload->>'item_id' AS item_id,"
        "payload->>'question_id' AS question_id,settings_snapshot->>'effort' AS effort,settings_snapshot->>'model_id' AS model,"
        "COALESCE(finished_at,started_at,created_at) AS at,result->'dispatch_failure' AS failure "
        "FROM ai_jobs ORDER BY COALESCE(finished_at,started_at,created_at) DESC,id DESC LIMIT %s",
        (limit,),
    ).fetchall()
    qids = [int(r["question_id"]) for r in rows if (r["question_id"] or "").isdigit()]
    questions = {
        r["id"]: r
        for r in conn.execute(
            "SELECT q.id,q.content->>'subject' AS subject,q.content->>'question_type' AS qtype,"
            "s.status,s.reason,s.revision_count,o.origin,o.past_exam_item_id,o.source_ref "
            "FROM reviewed_questions q LEFT JOIN auto_question_states s ON s.question_id=q.id "
            "LEFT JOIN question_origins o ON o.question_id=q.id WHERE q.id=ANY(%s)",
            (qids,),
        ).fetchall()
    }
    out = []
    for r in rows:
        q = questions.get(int(r["question_id"])) if (r["question_id"] or "").isdigit() else None
        kind = KIND_NAME.get(r["kind"], r["kind"])
        source = None
        if q:  # a review / repair / grading task of one specific question
            bucket = _bucket_name(q["subject"], q["qtype"])
            if q["origin"] == "past_exam":
                source, label = "past_exam", f"{kind} · 真题{TYPE_NAME.get(q['qtype'], '')} #{q['id']}"
            elif (q["source_ref"] or {}).get("legacy_draft_id"):
                source, label = "legacy", f"{kind} · 旧候选复用 {bucket} #{q['id']}"
            else:
                source, label = "generated", f"{kind} · {bucket} #{q['id']}"
            if (q["revision_count"] or 0) > 0:
                label += "（修复后）"
                source = (source or "generated") + "+repaired"
        elif r["kind"] == "generation":
            bucket = _bucket_name(r["subject"], r["question_type"])
            if r["origin"] == "past_exam":
                source, label = "past_exam", f"{kind} · 真题{TYPE_NAME.get(r['question_type'], '')}答案" + (f"（真题条目 #{r['item_id']}）" if r["item_id"] else "")
            else:
                source, label = "generated", f"{kind} · {bucket}（新题）"
        elif r["kind"] == "compatibility":
            label = f"{kind} · {r['model'] or ''} / {r['effort'] or ''}"
        else:
            label = kind + (" · " + _bucket_name(r["subject"], r["question_type"]) if r["question_type"] else "")
        reason = r["error_code"]
        detail = failure_detail({"dispatch_failure": r["failure"]}) if r["failure"] else None
        if detail:
            reason = detail["code"]
        if not reason and r["state"] in ("pending", "retrying") and r["kind"] in held:
            reason = "waiting_for_off_peak"
        outcome = None
        if r["kind"] == "quality_review" and q and r["state"] == "succeeded":
            outcome = dict(status=q["status"], reason=q["reason"])
        out.append(dict(
            id=r["id"], kind=r["kind"], label=label, source=source, state=r["state"], reason=reason,
            detail=detail["text"] if detail else None, at=r["at"], subject=r["subject"], question_type=r["question_type"],
            question_id=q["id"] if q else None, attempts=r["attempts"],
            next_retry=r["next_retry"] if r["state"] == "retrying" else None, outcome=outcome,
            awaiting_dispatch=bool(r["state"] == "succeeded" and r["dispatched_at"] is None),
        ))
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
        totals={k: sum(b[k] for b in rows) for k in ("required", "target", "stocked", "queued", "generating", "reviewing", "blocked", "admitted_today")},
        target_kind=rows[0]["target_kind"],
        review_today=review_pass_rate_today(conn),
        legacy=legacy_summary(conn),
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
