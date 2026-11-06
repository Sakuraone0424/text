"""Monthly spend caps, off-peak scheduling and thinking levels for the AI pipeline.

- Question writing, review and repair run only in DeepSeek's off-peak hours
  (jobs wait, they are never dropped). Grading is never held back.
- A monthly hard cap (default 90 CNY) applies to all calls; 15 CNY of it is kept
  for grading, so writing/review stop earlier (at 75 when grading has used
  nothing) and grading keeps working up to the cap.
- Spend is measured on settled usage; only a failed, uncertain or still running
  attempt is counted at its worst-case reservation.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app import cny_billing

# Everything that writes or checks questions waits for off-peak hours.
OFF_PEAK_ONLY = ("generation", "quality_review", "repair")
# Calls that keep running while the writing/review budget is exhausted.
RESERVED_FOR = ("grading", "compatibility")
DEFAULT_MONTHLY_CAP = Decimal(90)
DEFAULT_GRADING_RESERVE = Decimal(15)
THINKING_DEFAULTS = {"generation": "off", "repair": "off", "quality_review": "low"}
THINKING_ENV = {
    "generation": "AI_GENERATION_THINKING",
    "repair": "AI_GENERATION_THINKING",
    "quality_review": "AI_REVIEW_THINKING",
    "grading": "AI_GRADING_THINKING",
}
EFFORTS = ("off", "low", "high", "max")


# ---- off-peak ----------------------------------------------------------------------


def is_peak(at=None, calendar=None):
    """DeepSeek peak: Beijing 09:00-12:00 and 14:00-18:00 on weekdays that are not holidays.

    With a verified calendar for that year holidays are off-peak all day. Without
    one the rule still applies on every weekday (holidays then count as workdays:
    never cheaper than reality, so never a surprise bill).
    """
    at = at or datetime.now(timezone.utc)
    local = at.astimezone(cny_billing.SHANGHAI)
    calendar = cny_billing.calendar_2026() if calendar is None else calendar
    if local.weekday() >= 5:
        return False
    if calendar.get("verified") and calendar.get("year") == local.year and local.date().isoformat() in calendar.get("holidays", []):
        return False
    minute = local.hour * 60 + local.minute
    return 540 <= minute < 720 or 840 <= minute < 1080


def held_back_kinds(at=None):
    """Job kinds that must wait right now."""
    return list(OFF_PEAK_ONLY) if is_peak(at) else []


# ---- thinking level -----------------------------------------------------------------


# A single-choice review has to settle the unique answer on its own and refute every wrong option: it thinks harder.
SINGLE_CHOICE_REVIEW_THINKING = ("AI_REVIEW_THINKING_SINGLE_CHOICE", "high")


def effort_for(kind, payload=None):
    """Configured thinking level for a job kind (a single-choice review has its own), or None to keep the model setting."""
    if kind == "quality_review" and isinstance(payload, dict) and (payload.get("question") or {}).get("question_type") == "single_choice":
        env, default = SINGLE_CHOICE_REVIEW_THINKING
        raw = os.environ.get(env, "").strip().lower()
        return raw if raw in EFFORTS else default
    raw = os.environ.get(THINKING_ENV.get(kind, ""), "").strip().lower()
    value = raw or THINKING_DEFAULTS.get(kind)
    return value if value in EFFORTS else THINKING_DEFAULTS.get(kind)


# ---- caps ---------------------------------------------------------------------------


def _money(name, default):
    try:
        value = Decimal(os.environ.get(name, "").strip() or str(default))
    except InvalidOperation:
        return Decimal(default)
    return value if value.is_finite() and value >= 0 else Decimal(default)


def caps():
    """(monthly cap, amount reserved for grading), both in CNY."""
    cap = _money("AI_MONTHLY_CAP_CNY", DEFAULT_MONTHLY_CAP)
    return cap, min(_money("AI_GRADING_RESERVE_CNY", DEFAULT_GRADING_RESERVE), cap)


MONTH_START = "(date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')"
DAY_START = "(date_trunc('day',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')"


def month_split(conn, uid, start=None):
    """(reserved-group spend, writing/review spend) since `start` (default: the start of this Shanghai month), by what each
    attempt counts (cny_billing.COUNTED)."""
    row = conn.execute(
        f"SELECT COALESCE(sum({cny_billing.COUNTED}) FILTER(WHERE j.kind=ANY(%(rf)s)),0) AS reserved, "
        f"COALESCE(sum({cny_billing.COUNTED}) FILTER(WHERE NOT j.kind=ANY(%(rf)s)),0) AS other "
        f"{cny_billing._ATTEMPTS} WHERE j.user_id=%(uid)s AND r.created_at>={start or MONTH_START}",
        {"rf": list(RESERVED_FOR), "uid": uid, "med": cny_billing.medians(conn, uid)},
    ).fetchone()
    return Decimal(row["reserved"]), Decimal(row["other"])


def cap_reason(conn, uid, kind, amount):
    """Why this call may not start under the monthly cap, or None."""
    cap, reserve = caps()
    reserved, other = month_split(conn, uid)
    total = reserved + other
    if kind in RESERVED_FOR:
        limit = cap
    else:
        # Keep back whatever part of the grading reserve is still unused.
        limit = cap - max(Decimal(0), reserve - reserved)
    return "budget_exceeded" if total + Decimal(amount) > limit else None


# ---- daily pace ---------------------------------------------------------------------
#
# Writing and review money is spread over the month: today's allowance = (what is left for writing this month, the unused
# grading reserve already taken out) / (days left including today). When today's allowance is spent, writing/review jobs wait
# in the queue until Beijing midnight. If tomorrow's paper cannot be put together from the stock (some bucket below one paper's
# worth), today may overspend by up to 50% as an exception.

PACE_EXCEPTION = Decimal("1.5")


def _day_numbers(at=None):
    import calendar

    local = (at or datetime.now(timezone.utc)).astimezone(cny_billing.SHANGHAI)
    days_in_month = calendar.monthrange(local.year, local.month)[1]
    return local, days_in_month - local.day + 1, days_in_month


def tomorrow_short(conn, uid):
    """Can tomorrow's paper NOT be put together: is some (subject, type) bucket below one paper's worth of usable stock?"""
    from app import auto_generation, training_spec_v4

    return any(
        auto_generation.eligible_reserve_count(conn, uid, subject, kind, cached=True) < need
        for subject in training_spec_v4.SUBJECTS for kind, need in training_spec_v4.COUNTS.items()
    )


def pace_enabled():
    """AI_DAILY_PACE=0 turns the day-by-day split off: writing/review is then bound by the month's limit and the gap alone."""
    return os.environ.get("AI_DAILY_PACE", "1").strip() != "0"


def pace(conn, uid, at=None, short=None):
    """Today's writing/review allowance and use (CNY), and how far this month's money goes at the current speed."""
    cap, reserve = caps()
    reserved, other = month_split(conn, uid)
    limit = cap - max(Decimal(0), reserve - reserved)             # what writing/review may spend this month in all
    _, today_other = month_split(conn, uid, DAY_START)
    before_today = other - today_other
    local, days_left, days_in_month = _day_numbers(at)
    left_before_today = max(Decimal(0), limit - before_today)
    quota = (left_before_today / days_left).quantize(Decimal("0.0001"))
    if short is None:
        short = tomorrow_short(conn, uid)
    allowed = quota * PACE_EXCEPTION if short else quota
    remaining = max(Decimal(0), limit - other)
    elapsed = max(1, local.day - 1 + (local.hour * 60 + local.minute) / 1440)
    per_day = other / Decimal(str(elapsed))
    covers = None if per_day <= 0 else float(remaining / per_day)
    runs_out = None
    if covers is not None and covers < days_left:
        from datetime import timedelta

        runs_out = (local + timedelta(days=covers)).date().isoformat()
    return dict(
        enabled=pace_enabled(), month_writing_used_cny=str(other.quantize(Decimal("0.01"))), month_writing_limit_cny=str(limit.quantize(Decimal("0.01"))),
        grading_reserve_left_cny=str(max(Decimal(0), reserve - reserved).quantize(Decimal("0.01"))),
        quota_cny=str(quota), allowed_cny=str(allowed.quantize(Decimal("0.0001"))), used_cny=str(today_other.quantize(Decimal("0.0001"))),
        exceeded=today_other >= allowed, exception=bool(short), days_left=days_left, remaining_cny=str(remaining.quantize(Decimal("0.01"))),
        runs_out_on=runs_out, lasts_to_month_end=runs_out is None,
    )


def pace_exhausted(conn, uid):
    """Today's writing/review allowance is used up (jobs wait until Beijing midnight)."""
    if not pace_enabled():
        return False
    p = pace(conn, uid)
    # (a month that is already used up is the monthly cap's business: it blocks with budget_exceeded)
    return bool(p["exceeded"]) and Decimal(p["remaining_cny"]) > 0


# ---- report ---------------------------------------------------------------------------


def spend_report(conn, uid):
    """Today / this month, tokens, per-kind breakdown, and cost per admitted question."""
    cap, reserve = caps()
    reserved, other = month_split(conn, uid)
    base = (
        "FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id "
        "WHERE j.user_id=%s AND e.kind='usage_estimate' AND e.created_at>={start}"
    )
    tokens = (
        "COALESCE(sum((e.detail->'usage'->>'input_tokens')::bigint),0) AS input_tokens,"
        "COALESCE(sum((e.detail->'usage'->>'cache_read_input_tokens')::bigint),0) AS cached_tokens,"
        "COALESCE(sum((e.detail->'usage'->>'output_tokens')::bigint),0) AS output_tokens,"
        "count(*) AS calls,COALESCE(sum(e.amount_cny),0) AS cny"
    )

    def window(start):
        return conn.execute(f"SELECT {tokens} " + base.format(start=start), (uid,)).fetchone()

    today, month = window(DAY_START), window(MONTH_START)
    by_kind = conn.execute(
        f"SELECT j.kind,{tokens} " + base.format(start=MONTH_START) + " GROUP BY j.kind ORDER BY j.kind", (uid,)
    ).fetchall()
    recent = conn.execute(
        "SELECT e.created_at,j.kind,e.amount_cny AS cny,(e.detail->'usage'->>'input_tokens')::bigint AS input_tokens,"
        "(e.detail->'usage'->>'cache_read_input_tokens')::bigint AS cached_tokens,"
        "(e.detail->'usage'->>'output_tokens')::bigint AS output_tokens,e.detail->>'period_start' AS period "
        "FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s AND e.kind='usage_estimate' "
        "ORDER BY e.id DESC LIMIT 50",
        (uid,),
    ).fetchall()
    def writing_spend(start):
        return Decimal(conn.execute(
            "SELECT COALESCE(sum(e.amount_cny),0) AS cny FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id "
            f"WHERE j.user_id=%s AND e.kind='usage_estimate' AND NOT j.kind=ANY(%s) AND e.created_at>={start}",
            (uid, list(RESERVED_FOR)),
        ).fetchone()["cny"])

    def admitted(start):
        return conn.execute(
            "SELECT count(*) AS n FROM auto_question_states s JOIN reviewed_questions q ON q.id=s.question_id "
            f"WHERE s.status='ready' AND q.status='published' AND s.updated_at>={start}"
        ).fetchone()["n"]

    month_admitted, all_admitted = admitted(MONTH_START), admitted("'-infinity'")
    all_writing = writing_spend("'-infinity'")
    month_writing = writing_spend(MONTH_START)
    discarded = conn.execute(
        f"SELECT count(*) AS n FROM auto_question_states WHERE status='retired' AND reason LIKE 'discarded:%%' AND updated_at>={MONTH_START}"
    ).fetchone()["n"]
    total = reserved + other
    writing_limit = cap - max(Decimal(0), reserve - reserved)
    exhausted_writing = total >= writing_limit
    exhausted_all = total >= cap
    return dict(
        currency="CNY",
        today_cny=str(today["cny"]), month_cny=str(month["cny"]),
        month_counted_cny=str(total),  # settled usage + worst case of failed/running attempts
        month_cap_cny=str(cap), grading_reserve_cny=str(reserve),
        writing_review_limit_cny=str(writing_limit),
        remaining_writing_review_cny=str(max(Decimal(0), writing_limit - total)),
        remaining_grading_cny=str(max(Decimal(0), cap - total)),
        writing_review_exhausted=exhausted_writing, ai_quota_exhausted=exhausted_all,
        message=("本月 AI 额度已用完" if exhausted_all else "本月 AI 出题与审核额度已用完，判分仍可使用" if exhausted_writing else None),
        tokens=dict(today={k: int(today[k]) for k in ("input_tokens", "cached_tokens", "output_tokens", "calls")},
                    month={k: int(month[k]) for k in ("input_tokens", "cached_tokens", "output_tokens", "calls")}),
        by_kind=[{**r, "cny": str(r["cny"])} for r in by_kind],
        recent_calls=[{**r, "cny": str(r["cny"]), "created_at": r["created_at"].isoformat()} for r in recent],
        admitted_questions=dict(month=month_admitted, total=all_admitted, discarded_month=discarded),
        cost_per_admitted_question_cny=dict(
            month=str((month_writing / month_admitted).quantize(Decimal("0.0001"))) if month_admitted else None,
            total=str((all_writing / all_admitted).quantize(Decimal("0.0001"))) if all_admitted else None,
            basis="writing + review + repair spend divided by questions now published",
        ),
        off_peak=dict(now_is_peak=is_peak(), held_back_kinds=held_back_kinds(),
                      peak_hours_beijing=["09:00-12:00", "14:00-18:00 (weekdays, not holidays)"]),
        thinking=dict(generation=effort_for("generation"), review=effort_for("quality_review"), grading=effort_for("grading")),
    )


# ---- temporary bank target (first trial batch) ----------------------------------------


def bank_target():
    """AI_BANK_TARGET_PER_BUCKET: stock wanted per subject x question type, or None.

    TEMPORARY. Unset (or invalid / not positive) means the normal rule: top each
    bucket up to two days of reserve. Set, it replaces that reserve target, e.g. 5
    gives the 10 buckets (2 subjects x 5 types) x 5 = 50 question first batch.
    """
    raw = os.environ.get("AI_BANK_TARGET_PER_BUCKET", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def reserve_target(required, days):
    return bank_target() or required * days


def bucket_supply(conn, subject, kind):
    """Questions of a bucket that exist or are already on their way (any pipeline stage)."""
    # A ready question that has already been on a paper or practice set is not stock any more, for good (it never returns).
    made = conn.execute(
        "SELECT count(*) AS n FROM auto_question_states st JOIN reviewed_questions q ON q.id=st.question_id "
        "WHERE st.status IN ('ready','candidate','checking','repairing') "
        "AND q.content->>'subject'=%s AND q.content->>'question_type'=%s "
        "AND NOT (st.status='ready' AND (EXISTS(SELECT 1 FROM paper_items_v2 i WHERE i.question_id=q.id) "
        "OR EXISTS(SELECT 1 FROM practice_sets ps WHERE q.id=ANY(ps.question_ids))))",
        (subject, kind),
    ).fetchone()["n"]
    queued = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE kind='generation' AND dispatched_at IS NULL "
        "AND state IN ('pending','running','retrying','succeeded') "
        "AND payload->>'subject'=%s AND payload->>'question_type'=%s",
        (subject, kind),
    ).fetchone()["n"]
    return made + queued


# ---- peak / off-peak outlook --------------------------------------------------------


def peak_outlook(at=None, calendar=None, horizon_days=9):
    """Is it peak now, when does off-peak next start, and when does the next peak begin."""
    from datetime import timedelta

    at = (at or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    now_peak = is_peak(at, calendar)
    next_off = next_peak = None
    step = timedelta(minutes=1)
    moment = at
    for _ in range(horizon_days * 24 * 60):
        moment += step
        peak = is_peak(moment, calendar)
        if now_peak and not peak and next_off is None:
            next_off = moment
        if not now_peak and peak and next_peak is None:
            next_peak = moment
        if (now_peak and next_off) or (not now_peak and next_peak):
            break
    return dict(is_peak=now_peak, next_off_peak_at=next_off, next_peak_at=next_peak)
