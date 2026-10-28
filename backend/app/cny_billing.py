"""CNY-only immutable estimates; reservations are conservative ceilings, not invoices."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from psycopg.types.json import Jsonb

SHANGHAI = ZoneInfo("Asia/Shanghai")
MILLION = Decimal(1000000)
SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"


def money(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_cny_amount")
    if not result.is_finite() or result < 0:
        raise ValueError("invalid_cny_amount")
    return result


def utcnow():
    return datetime.now(timezone.utc)


def validate_price(price, model, now=None):
    now = now or utcnow()
    if price.get("currency") != "CNY" or price.get("model_id") != model:
        raise ValueError("cny_model_price_required")
    if not price.get("version") or price.get("source_url") != SOURCE:
        raise ValueError("official_cny_price_required")
    start, end = (
        datetime.fromisoformat(price[k]) for k in ("valid_from", "valid_until")
    )
    if start.tzinfo is None or end.tzinfo is None or not start <= now < end:
        raise ValueError("cny_price_expired_or_not_effective")
    import re

    if not re.fullmatch("[0-9a-f]{64}", price.get("source_sha256", "")):
        raise ValueError("price_source_sha256_required")
    verified = datetime.fromisoformat(price["verified_at"])
    if (
        verified.tzinfo is None
        or verified > now + timedelta(minutes=5)
        or end > verified + timedelta(hours=24)
    ):
        raise ValueError("price_observation_time_invalid")
    for period in ("off_peak", "peak"):
        for field in ("cache_hit", "cache_miss", "output"):
            money(price["rules"][period][field])
    return price


def current_price(conn, model, now=None):
    now = now or utcnow()
    rows = conn.execute(
        "SELECT * FROM ai_cny_prices WHERE model_id=%s AND valid_from<=%s AND valid_until>%s ORDER BY id",
        (model, now, now),
    ).fetchall()
    if not rows:
        raise ValueError("verified_cny_price_required")
    # Conflicts do not auto-resolve just because a newer observation expires.
    # A cached old page may never silently replace a newer confirmed tariff.
    decision = conn.execute(
        "SELECT p.rules,p.model_version,d.created_at AS resolved_at FROM ai_cny_price_resolutions d JOIN ai_cny_prices p ON p.id=d.price_id WHERE d.model_id=%s ORDER BY d.id DESC LIMIT 1",
        (model,),
    ).fetchone()
    history = conn.execute(
        "SELECT rules,model_version FROM ai_cny_prices WHERE model_id=%s", (model,)
    ).fetchall()
    identity = lambda r: (str(r["rules"]), r["model_version"])
    identities = {identity(r) for r in history}
    if decision:
        if any(
            identity(r) != identity(decision)
            and r["created_at"] > decision["resolved_at"]
            for r in rows
        ):
            raise ValueError("conflicting_cny_price_versions")
        rows = [r for r in rows if identity(r) == identity(decision)]
        if not rows:
            raise ValueError("verified_cny_price_required")
    elif len(identities) != 1:
        raise ValueError("conflicting_cny_price_versions")
    p = dict(rows[-1])
    for k in (
        "valid_from",
        "valid_until",
        "verified_at",
        "official_effective_at",
        "created_at",
    ):
        if p.get(k) is not None:
            p[k] = p[k].isoformat()
    return validate_price(p, model, now)


def install_price(conn, price):
    """Operator-only immutable insertion after checking the actual official Chinese page.

    No browser endpoint may assert official prices; no automatic stale overwrite.
    """
    validate_price(price, price["model_id"])
    old = conn.execute(
        "SELECT * FROM ai_cny_prices WHERE version=%s", (price["version"],)
    ).fetchone()
    if old:
        if (
            old["rules"] != price["rules"]
            or old["source_sha256"] != price["source_sha256"]
        ):
            raise ValueError("price_version_identity_conflict")
        return old["id"]
    return conn.execute(
        "INSERT INTO ai_cny_prices(model_id,model_version,version,valid_from,valid_until,official_effective_at,source_url,source_sha256,verified_at,rules) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            price["model_id"],
            price.get("model_version"),
            price["version"],
            price["valid_from"],
            price["valid_until"],
            price.get("official_effective_at"),
            price["source_url"],
            price["source_sha256"],
            price["verified_at"],
            Jsonb(price["rules"]),
        ),
    ).fetchone()["id"]


def calendar_2026():
    import json
    from pathlib import Path

    return json.loads(
        (
            Path(__file__).resolve().parents[1] / "config" / "china-calendar-2026.json"
        ).read_text()
    )


def period(at, calendar):
    """Official wording: Mon–Fri excluding holidays; weekends remain off, even make-up days.

    Unknown/unverified annual calendars yield an upper bound, not a fabricated peak fact.
    """
    if at.tzinfo is None:
        raise ValueError("timezone_required")
    local = at.astimezone(SHANGHAI)
    if (
        not calendar
        or calendar.get("year") != local.year
        or not calendar.get("verified")
        or not calendar.get("source_url", "").startswith(("https://www.gov.cn/",))
    ):
        return "unknown"
    if local.date().isoformat() in calendar["holidays"] or local.weekday() >= 5:
        return "off_peak"
    minute = local.hour * 60 + local.minute
    return "peak" if 540 <= minute < 720 or 840 <= minute < 1080 else "off_peak"


def upper_rates(price):
    return {
        k: max(money(price["rules"][p][k]) for p in ("peak", "off_peak"))
        for k in ("cache_hit", "cache_miss", "output")
    }


def reserve_bound(price, token_limit):
    rates = upper_rates(price)
    # Match relay input byte limit; adversarial cache miss and output both at peak.
    return (
        Decimal(32000) * max(rates["cache_hit"], rates["cache_miss"])
        + Decimal(token_limit) * rates["output"]
    ) / MILLION


def estimate(price, usage, start, end, calendar=None):
    # Anthropic input_tokens excludes cache_read/cache_creation. Cache creation is miss.
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens")
    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in keys):
        return {
            "status": "unknown",
            "amount_cny": None,
            "reason": "provider_usage_incomplete",
        }
    creation = usage.get("cache_creation_input_tokens", 0)
    if type(creation) is not int or creation < 0:
        return {"status": "unknown", "amount_cny": None, "reason": "invalid_usage"}
    calendar = calendar or calendar_2026()
    first, last = period(start, calendar), period(end, calendar)
    # A short request wholly within a known band uses that band. Cross-boundary,
    # unknown calendars and unusually long requests retain the conservative ceiling.
    exact_band = (
        first == last
        and first != "unknown"
        and 0 <= (end - start).total_seconds() <= 180
    )
    rates = (
        {k: money(v) for k, v in price["rules"][first].items()}
        if exact_band
        else upper_rates(price)
    )
    amount = (
        Decimal(usage["cache_read_input_tokens"]) * rates["cache_hit"]
        + Decimal(usage["input_tokens"] + creation) * rates["cache_miss"]
        + Decimal(usage["output_tokens"]) * rates["output"]
    ) / MILLION
    return dict(
        status="usage_estimate",
        amount_cny=str(amount),
        currency="CNY",
        invoice_confirmed=False,
        period_start=first,
        period_end=last,
        settlement_rule="single_known_band_usage_estimate"
        if exact_band
        else "peak_upper_bound_boundary_policy_unspecified",
        rule_version=price["version"],
        calendar_version=(calendar or {}).get("version"),
        usage=usage,
        search_fetch_cny="0",
        search_fetch_enabled=False,
    )


def tool_cost(tool, successes, at, mode="direct", price=None):
    if tool not in ("search", "fetch") or type(successes) is not int or successes < 0:
        raise ValueError("invalid_paid_tool_usage")
    if mode not in ("direct", "server"):
        raise ValueError("unknown_tool_billing_mode")
    if at.tzinfo is None:
        raise ValueError("timezone_required")
    if not price:
        raise ValueError("verified_tool_price_required")
    effective = datetime.fromisoformat(price["rules"]["server_tool_effective_at"])
    return (
        Decimal(0)
        if mode == "server" and at < effective
        else Decimal(successes) * money(price["rules"][tool + "_per_success"])
    )


def totals(conn, uid):
    return conn.execute(
        """SELECT COALESCE(sum(r.amount_cny) FILTER(WHERE r.created_at>=(date_trunc('day',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')),0) AS day,
     COALESCE(sum(r.amount_cny) FILTER(WHERE r.created_at>=(date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')),0) AS month,
     COALESCE(sum(r.amount_cny),0) AS trial FROM ai_cny_reservations r JOIN ai_jobs j ON j.id=r.job_id WHERE j.user_id=%s""",
        (uid,),
    ).fetchone()


def settle(conn, job, result, error):
    reservation = conn.execute(
        "SELECT * FROM ai_cny_reservations WHERE job_id=%s AND attempt=%s",
        (job["id"], job["attempts"]),
    ).fetchone()
    if not reservation:
        raise ValueError("cny_reservation_required")
    usage = (result or {}).get("usage", {}) or getattr(error, "usage", {})
    detail = (
        estimate(reservation["price_snapshot"], usage, job["started_at"], utcnow())
        if error is None
        else dict(
            status="unknown",
            amount_cny=None,
            reason="failed_or_uncertain_request",
            usage=usage,
        )
    )
    if (
        detail["amount_cny"] is not None
        and money(detail["amount_cny"]) > reservation["amount_cny"]
    ):
        detail = dict(
            status="unknown",
            amount_cny=None,
            reason="provider_usage_exceeds_reserved_bound",
            usage=usage,
        )
        conn.execute(
            "UPDATE ai_settings SET stopped=true WHERE user_id=%s", (job["user_id"],)
        )
    detail.update(
        price_version=reservation["price_snapshot"]["version"],
        reservation_cny=str(reservation["amount_cny"]),
        official_bill_confirmed=False,
    )
    conn.execute(
        "INSERT INTO ai_cny_events(event_key,job_id,attempt,kind,amount_cny,detail) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING",
        (
            f"attempt:{job['id']}:{job['attempts']}",
            job["id"],
            job["attempts"],
            detail["status"],
            detail["amount_cny"],
            Jsonb(detail),
        ),
    )
    conn.execute(
        "UPDATE ai_jobs SET estimated_cny=(SELECT sum(amount_cny) FROM ai_cny_events WHERE job_id=%s AND kind='usage_estimate'),billing_status=%s WHERE id=%s",
        (job["id"], detail["status"], job["id"]),
    )


def summary(conn, uid):
    from app.model_router import settings

    s = settings(conn, uid)
    t = totals(conn, uid)
    events = conn.execute(
        "SELECT e.*,j.kind AS task_type,j.settings_snapshot->>'model_id' AS model_id FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s ORDER BY e.id DESC LIMIT 100",
        (uid,),
    ).fetchall()
    aggregate = conn.execute(
        "SELECT COALESCE(sum(e.amount_cny) FILTER(WHERE e.kind='usage_estimate' AND e.created_at>=(date_trunc('day',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')),0) AS day, COALESCE(sum(e.amount_cny) FILTER(WHERE e.kind='usage_estimate' AND e.created_at>=(date_trunc('month',clock_timestamp() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai')),0) AS month, count(*) FILTER(WHERE e.kind='unknown') AS unknown FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s",
        (uid,),
    ).fetchone()
    unresolved = conn.execute(
        "SELECT COALESCE(sum(r.amount_cny),0) AS n FROM ai_cny_reservations r JOIN ai_jobs j ON j.id=r.job_id WHERE j.user_id=%s AND NOT EXISTS(SELECT 1 FROM ai_cny_events e WHERE e.job_id=r.job_id AND e.attempt=r.attempt AND e.kind='usage_estimate')",
        (uid,),
    ).fetchone()["n"]
    try:
        p = current_price(conn, s["model_id"])
    except ValueError as exc:
        p = {"available": False, "reason": str(exc)}
    count = conn.execute(
        "SELECT count(*) AS n FROM ai_cny_reservations e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s",
        (uid,),
    ).fetchone()["n"]
    actual = conn.execute(
        "SELECT count(*) AS n FROM ai_call_events e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s AND e.provider_request_confirmed=true",
        (uid,),
    ).fetchone()["n"]
    bills = conn.execute(
        "SELECT sum(e.amount_cny) AS amount FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s AND e.kind IN ('bill_confirmation','bill_adjustment')",
        (uid,),
    ).fetchone()["amount"]
    by_task = conn.execute(
        "SELECT j.kind,count(*) AS event_count,sum(e.amount_cny) FILTER(WHERE e.kind='usage_estimate') AS usage_estimate_cny,count(*) FILTER(WHERE e.kind='unknown') AS unknown_attempts FROM ai_cny_events e JOIN ai_jobs j ON j.id=e.job_id WHERE j.user_id=%s GROUP BY j.kind ORDER BY j.kind",
        (uid,),
    ).fetchall()
    return dict(
        currency="CNY",
        price=p,
        period=period(utcnow(), calendar_2026()),
        reserved_cny=t,
        usage_estimate_cny=aggregate,
        unknown_reserved_cny=str(unresolved),
        remaining_daily_cny=str(max(Decimal(0), s["daily_cny"] - t["day"])),
        remaining_monthly_cny=str(max(Decimal(0), s["monthly_cny"] - t["month"])),
        confirmed_invoice_cny=str(bills) if bills is not None else None,
        invoice_status="has_invoice_events" if bills is not None else "not_reconciled",
        call_attempts=count,
        confirmed_provider_requests=actual,
        request_count_status="unfinished_or_uncertain_attempts_may_have_reached_provider",
        events=events,
        by_task=by_task,
        search_fetch_enabled=False,
        search_fetch_cny="0",
        budget_policy="all_attempt_reservations_retained_including_timeouts",
        settings_history=conn.execute(
            "SELECT settings,created_at FROM ai_settings_events WHERE user_id=%s ORDER BY id DESC LIMIT 30",
            (uid,),
        ).fetchall(),
        railway_cost_included=False,
    )


def record_invoice(conn, job_id, attempt, amount, invoice_sha256, adjustment=False):
    """Operator-only verified statement import; never called by models/mobile clients."""
    import re

    if not re.fullmatch("[0-9a-f]{64}", invoice_sha256):
        raise ValueError("invoice_evidence_required")
    value = money(amount)
    if not conn.execute(
        "SELECT 1 FROM ai_cny_reservations WHERE job_id=%s AND attempt=%s",
        (job_id, attempt),
    ).fetchone():
        raise ValueError("invoice_reservation_not_found")
    kind = "bill_adjustment" if adjustment else "bill_confirmation"
    conn.execute(
        "INSERT INTO ai_cny_events(event_key,job_id,attempt,kind,amount_cny,detail) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            f"invoice:{invoice_sha256}:{job_id}:{attempt}:{kind}",
            job_id,
            attempt,
            kind,
            value,
            Jsonb(dict(invoice_sha256=invoice_sha256, verified_statement=True)),
        ),
    )


def resolve_price_conflict(conn, price_id, evidence_sha256, basis):
    """Operator confirms fresh official evidence; never automatic/API/model approval."""
    import re

    if not re.fullmatch("[0-9a-f]{64}", evidence_sha256) or len(basis.strip()) < 40:
        raise ValueError("price_resolution_evidence_required")
    p = conn.execute("SELECT * FROM ai_cny_prices WHERE id=%s", (price_id,)).fetchone()
    if not p:
        raise ValueError("price_not_found")
    conn.execute(
        "INSERT INTO ai_cny_price_resolutions(model_id,price_id,evidence_sha256,basis) VALUES(%s,%s,%s,%s)",
        (p["model_id"], price_id, evidence_sha256, basis),
    )
