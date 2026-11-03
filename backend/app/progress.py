"""Read-only numbers for the question-writing progress page.

Counts, codes and times only: never a question, a textbook paragraph, a prompt or a secret.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from app import cny_billing, cost_control, cost_reconcile
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
        f"FROM ai_jobs WHERE (kind='generation' OR (kind='repair' AND payload ? 'repair_of_job')) AND {JOB_CURRENT} AND ("
        " state IN ('pending','retrying','running') OR (state='succeeded' AND dispatched_at IS NULL)"
        " OR (state='blocked' AND COALESCE(error_code,'')<>'superseded_scoring' AND created_at>clock_timestamp()-%s*interval '1 day')"
        ") GROUP BY 1,2,3",
        (BLOCKED_JOB_DAYS,),
    ).fetchall()
    job_reasons = conn.execute(
        "SELECT payload->>'subject' AS s,payload->>'question_type' AS t,"
        "COALESCE(result->'dispatch_failure'->>'code',error_code,'unspecified') AS code,count(*) AS n "
        f"FROM ai_jobs WHERE (kind='generation' OR (kind='repair' AND payload ? 'repair_of_job')) AND state='blocked' AND {JOB_CURRENT} AND COALESCE(error_code,'')<>'superseded_scoring' "
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
                stocked=eligible_reserve_count(conn, uid, subject, kind, cached=True), admitted_today=today.get((subject, kind), 0),
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
    after = failure.get("after")
    if isinstance(after, dict) and after.get("code"):  # the one repair did not fix it
        parts.append("修复后:" + after["code"])
        for error in after.get("errors") or []:
            parts.append("修复后:" + (error.get("reason") or error.get("code") or error.get("type") or "unspecified"))
        parts += ["修复后:" + r for r in after.get("reasons") or []]
    return dict(code=failure["code"], text="；".join(dict.fromkeys(p for p in parts if p)))


def _notes(norm):
    """Plain-Chinese lines: what was corrected automatically and which layer matched each quote."""
    if not isinstance(norm, dict):
        return []
    from app.auto_fixes import FIX_LABELS
    from app.evidence_match import LAYER_NAMES

    names = {1: "原样", 2: "规范化", 3: "去标点", 4: "省略号", 5: "跨段落", 6: "严格模糊"}
    lines = ["自动修正：" + FIX_LABELS.get(f, f) for f in norm.get("fixes", [])]
    layers = [f"第 {e['layer']} 层（{names.get(e['layer'], '')}）" for e in norm.get("evidence", []) if e.get("matched")]
    if layers:
        lines.append("引用匹配：" + "、".join(layers))
    return lines


def recent_jobs(conn, limit=20):
    held = cost_control.held_back_kinds()
    rows = conn.execute(
        "SELECT id,kind,state,attempts,error_code,next_retry,dispatched_at,payload->>'subject' AS subject,"
        "payload->>'question_type' AS question_type,payload->>'origin' AS origin,payload->>'item_id' AS item_id,"
        "payload->>'question_id' AS question_id,settings_snapshot->>'effort' AS effort,settings_snapshot->>'model_id' AS model,"
        "COALESCE(finished_at,started_at,created_at) AS at,result->'dispatch_failure' AS failure,result->'normalization' AS norm "
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
            detail=detail["text"] if detail else None, notes=_notes(r["norm"]), at=r["at"], subject=r["subject"], question_type=r["question_type"],
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
        concurrency=row["concurrency"], duration_seconds=float(row["duration_seconds"]),
        stale=minutes > STALE_AFTER_MINUTES,
    )


def approved_recently(conn, hours=None):
    return conn.execute(
        "SELECT count(*) AS n FROM auto_question_states s JOIN reviewed_questions q ON q.id=s.question_id "
        "WHERE s.status='ready' AND q.status='published' AND s.updated_at>clock_timestamp()-%s*interval '1 hour'",
        (hours or RATE_WINDOW_HOURS,),
    ).fetchone()["n"]


MIN_APPROVALS_FOR_ETA = 3      # fewer recent approvals than this is a guess, not an estimate
MIN_OFF_PEAK_HOURS_FOR_ETA = 1.0
RATE_OFF_PEAK_HOURS = 6        # the rate comes from the last 6 off-peak hours (fewer if that is all the data there is)
ETA_HORIZON_HOURS = 48         # beyond this the page says "more than 2 days" instead of a date


def _off_peak_hours(start, end, step_minutes=15):
    """Hours of off-peak time in [start, end) (DeepSeek peak windows are excluded)."""
    from datetime import timedelta

    step = timedelta(minutes=step_minutes)
    moment, hours = start, 0.0
    while moment < end:
        if not cost_control.is_peak(moment):
            hours += step_minutes / 60
        moment += step
    return hours


def trailing_window(now, off_peak_hours=RATE_OFF_PEAK_HOURS, limit_hours=RATE_WINDOW_HOURS):
    """(hours back, off-peak hours inside) for the most recent `off_peak_hours` of off-peak time, looking back at most `limit_hours`."""
    from datetime import timedelta

    step, moment, seen, back = 15, now, 0.0, 0.0
    while seen < off_peak_hours and back < limit_hours:
        moment -= timedelta(minutes=step)
        back += step / 60
        if not cost_control.is_peak(moment):
            seen += step / 60
    return round(back, 2), seen


def estimate_ready(now, missing, approved, window_hours=RATE_WINDOW_HOURS, horizon_hours=ETA_HORIZON_HOURS, observed_off_peak_hours=None, start_at=None):
    """When will `missing` more questions be in the bank?

    The rate is approvals per OFF-PEAK hour over the last window (nothing is written in peak hours), and the
    forecast walks forward through the coming off-peak hours only. Returns (state, eta_at, eta_hours,
    per_off_peak_hour): state is "complete", "estimated", "estimating" (too little data) or "slow".
    """
    from datetime import timedelta

    if missing <= 0:
        return "complete", None, None, None
    window_hours_off = observed_off_peak_hours if observed_off_peak_hours is not None else _off_peak_hours(now - timedelta(hours=window_hours), now)
    if approved < MIN_APPROVALS_FOR_ETA or window_hours_off < MIN_OFF_PEAK_HOURS_FOR_ETA:
        return "estimating", None, None, None
    rate = approved / window_hours_off
    needed_off_peak = missing / rate
    moment, done, step = max(now, start_at) if start_at else now, 0.0, timedelta(minutes=15)
    while done < needed_off_peak:
        if (moment - now).total_seconds() / 3600 > horizon_hours:
            return "slow", None, None, round(rate, 3)
        if not cost_control.is_peak(moment):
            done += 0.25
        moment += step
    return "estimated", moment, round((moment - now).total_seconds() / 3600, 1), round(rate, 3)


def budget_state(conn, now):
    """Is today's AI budget used up? Then writing resumes by itself at the next Beijing midnight."""
    from datetime import timedelta

    local = now.astimezone(cny_billing.SHANGHAI)
    resume = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    out = dict(daily_exhausted=False, resume_at=resume, remaining_daily_cny=None)
    user = conn.execute("SELECT user_id FROM ai_settings ORDER BY user_id LIMIT 1").fetchone()
    if not user:
        return out
    try:
        from app import model_router

        settings = model_router.settings(conn, user["user_id"])
        if not settings.get("daily_limit_enabled", True):
            return out
        used = cny_billing.totals(conn, user["user_id"])["day"]
        remaining = max(Decimal(0), Decimal(settings["daily_cny"]) - Decimal(used))
        try:
            snap = model_router.snapshot(conn, user["user_id"])
            one_call = model_router.cost_bound({**snap, "price_snapshot": cny_billing.current_price(conn, snap["model_id"])}, at=now)
        except ValueError:
            one_call = Decimal(0)
        out.update(remaining_daily_cny=str(remaining), daily_exhausted=remaining <= 0 or remaining < one_call)
    except (ValueError, KeyError):
        pass
    return out


def next_paper(conn, rows, now):
    """How long until the bank holds one complete paper (counting each cell only up to one paper's need)."""
    missing = sum(max(0, b["required"] - b["stocked"]) for b in rows)
    back, off_hours = trailing_window(now)
    admitted = approved_recently(conn, back)
    budget = budget_state(conn, now)
    state, eta_at, eta_hours, per_off_peak = estimate_ready(
        now, missing, admitted, back, observed_off_peak_hours=off_hours,
        start_at=budget['resume_at'] if budget['daily_exhausted'] else None)
    return dict(missing_questions=missing, approved_last_hours=round(back), approved_in_window=admitted,
                per_hour=round(admitted / back, 3) if back else 0, per_off_peak_hour=per_off_peak, rate_off_peak_hours=round(off_hours, 2),
                complete=missing == 0, eta_state=state, eta_hours=eta_hours, eta_at=eta_at, budget=budget)


def daily_spend(conn, uid):
    """Settled spend per Beijing day this month (for the daily bar chart)."""
    rows = conn.execute(
        "SELECT to_char(e.created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS day,COALESCE(sum(e.amount_cny),0) AS cny "
        "FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id "
        f"WHERE j.user_id=%s AND e.kind='usage_estimate' AND e.created_at>={cost_control.MONTH_START} GROUP BY 1 ORDER BY 1",
        (uid,),
    ).fetchall()
    return [dict(day=r["day"], cny=str(r["cny"])) for r in rows]


def _reconciliation(conn):
    status = cost_reconcile.status(conn)
    return dict(last_checked_at=status["last_checked_at"], balance_cny=status["balance_cny"], last_correction=(status["corrections"] or [None])[0])


def _waste(conn):
    from app import waste_report

    stats = waste_report.blocked_stats(conn, 24)
    recurring = [i for i in stats["blocked"] if not i.get("one_time")]
    once = [i for i in stats["blocked"] if i.get("one_time")]
    return dict(hours=24, blocked=recurring[:8], one_time=once, auto_fixed=stats["auto_fixed"], evidence_layers=stats["evidence_layers"])


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
        waste=_waste(conn),
        ledger=dict(breakdown=cny_billing.ledger_breakdown(conn, uid), reconciliation=_reconciliation(conn)),
        spend={k: spend[k] for k in (
            "today_cny", "month_cny", "month_counted_cny", "month_cap_cny", "grading_reserve_cny",
            "writing_review_limit_cny", "remaining_writing_review_cny", "remaining_grading_cny",
            "writing_review_exhausted", "ai_quota_exhausted", "message", "admitted_questions",
            "cost_per_admitted_question_cny",
        )} | dict(daily=daily_spend(conn, uid), remaining_daily_cny=cny_billing.summary(conn, uid)["remaining_daily_cny"]),
        period=cost_control.peak_outlook(now),
        worker=worker_status(conn, now),
        next_paper=next_paper(conn, rows, now),
    )
