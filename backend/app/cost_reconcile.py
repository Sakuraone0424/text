"""Reconcile the ledger against DeepSeek's real balance (GET /user/balance): hourly, and at Beijing midnight.

Between two balance readings the balance fell by the money actually spent. Calls whose provider usage is known
keep their own cost; calls that ended without usage (a restart, a timeout, a lease that ran out) are counted by an
estimate until now. What the balance says they really cost replaces that estimate: money counted too much is given
back to the budget, every correction is kept (time, before, after, difference). A failing balance endpoint never
stops question writing: the next run simply tries again.
"""

import os
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import httpx

from app import cny_billing
from app.cny_billing import COUNTED, _ATTEMPTS, medians
from psycopg.types.json import Jsonb  # noqa: F401  (kept for callers)

BALANCE_URL = "https://api.deepseek.com/user/balance"
HOURLY = timedelta(hours=1)


def fetch_balance(timeout=10):
    """The CNY balance as {total, granted, topped_up} (Decimals). Raises on any failure."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("deepseek_secret_not_configured")
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(BALANCE_URL, headers={"Authorization": "Bearer " + key})
    if response.status_code != 200:
        raise ValueError("balance_http_" + str(response.status_code))
    infos = response.json().get("balance_infos") or []
    cny = next((i for i in infos if i.get("currency") == "CNY"), None)
    if cny is None:
        raise ValueError("balance_has_no_cny")
    try:
        return dict(
            total=Decimal(str(cny["total_balance"])),
            granted=Decimal(str(cny["granted_balance"])) if cny.get("granted_balance") is not None else None,
            topped_up=Decimal(str(cny["topped_up_balance"])) if cny.get("topped_up_balance") is not None else None,
        )
    except (InvalidOperation, KeyError):
        raise ValueError("balance_unreadable")


def last_snapshot(conn):
    return conn.execute("SELECT * FROM ai_balance_snapshots ORDER BY id DESC LIMIT 1").fetchone()


def reconcile(conn, uid, balance, source, note=None):
    """Store the reading; if there is an earlier one, correct the uncertain attempts in between. Returns a status string."""
    previous = last_snapshot(conn)
    now = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    conn.execute(
        "INSERT INTO ai_balance_snapshots(source,total_balance,granted_balance,topped_up_balance) VALUES (%s,%s,%s,%s)",
        (source, balance["total"], balance.get("granted"), balance.get("topped_up")),
    )
    if previous is None:
        return "baseline"
    if balance["total"] > previous["total_balance"]:
        return "topped_up"  # the balance rose: a top-up or a refund, nothing was spent in this interval
    spent = previous["total_balance"] - balance["total"]
    start = previous["taken_at"]
    rows = conn.execute(
        "SELECT ev.kind AS ek, ev.amount_cny AS known, r.job_id, r.attempt, r.amount_cny AS reserved "
        "FROM ai_cny_events ev JOIN ai_cny_reservations r ON r.job_id=ev.job_id AND r.attempt=ev.attempt "
        "JOIN ai_jobs j ON j.id=ev.job_id WHERE j.user_id=%s AND ev.event_key LIKE 'attempt:%%' "
        "AND ev.created_at>%s AND ev.created_at<=%s "
        "AND NOT EXISTS(SELECT 1 FROM ai_cny_adjustments x WHERE x.job_id=r.job_id AND x.attempt=r.attempt)",
        (uid, start, now),
    ).fetchall()
    known = sum((Decimal(r["known"]) for r in rows if r["ek"] == "usage_estimate" and r["known"] is not None), Decimal(0))
    uncertain = [r for r in rows if r["ek"] != "usage_estimate" or r["known"] is None]
    if not uncertain:
        _log(conn, source, start, now, previous, balance, spent, known, Decimal(0), Decimal(0), 0, "nothing uncertain in the interval")
        return "nothing_to_correct"
    pairs = dict(jobs=[r["job_id"] for r in uncertain], atts=[r["attempt"] for r in uncertain], uid=uid, med=medians(conn, uid))
    counted = {
        (r["job_id"], r["attempt"]): Decimal(r["n"])
        for r in conn.execute(
            f"SELECT r.job_id, r.attempt, {COUNTED} AS n {_ATTEMPTS} WHERE j.user_id=%(uid)s "
            "AND (r.job_id, r.attempt) IN (SELECT * FROM unnest(%(jobs)s::bigint[], %(atts)s::int[]))",
            pairs,
        ).fetchall()
    }
    before = sum(counted.values(), Decimal(0))
    actual = max(Decimal(0), spent - known)  # what the uncertain calls really cost together
    log_id = None
    after_total = Decimal(0)
    adjustments = []
    for r in uncertain:
        c = counted[(r["job_id"], r["attempt"])]
        share = (c / before) if before > 0 else Decimal(1) / len(uncertain)
        value = min(Decimal(r["reserved"]), (actual * share)).quantize(Decimal("0.00000001"))
        after_total += value
        adjustments.append((r["job_id"], r["attempt"], value))
    log_id = _log(conn, source, start, now, previous, balance, spent, known, before, after_total, len(uncertain), note)
    for job_id, attempt, value in adjustments:
        conn.execute(
            "INSERT INTO ai_cny_adjustments(job_id,attempt,amount_cny,reason,reconciliation_id) VALUES (%s,%s,%s,%s,%s)",
            (job_id, attempt, value, "balance_reconciliation:" + source, log_id),
        )
    return "corrected"


def _log(conn, source, start, end, previous, balance, spent, known, before, after, n, note):
    return conn.execute(
        "INSERT INTO ai_cost_corrections(source,interval_start,interval_end,balance_before,balance_after,actual_spent_cny,"
        "settled_known_cny,counted_before_cny,counted_after_cny,difference_cny,attempts,note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (source, start, end, previous["total_balance"], balance["total"], spent, known, before, after, before - after, n, note),
    ).fetchone()["id"]


def due(conn, now=None):
    """'midnight' if no reading exists since Beijing midnight (first run of the day), 'hourly' if the last is an hour old, else None."""
    last = last_snapshot(conn)
    now = now or conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    if last is None:
        return "hourly"
    if last["taken_at"].astimezone(cny_billing.SHANGHAI).date() < now.astimezone(cny_billing.SHANGHAI).date():
        return "midnight"
    return "hourly" if now - last["taken_at"] >= HOURLY else None


def reconcile_if_due(conn, uid, fetch=None):
    """One worker-run step. Never raises: a balance failure is reported and tried again next run."""
    source = due(conn)
    if source is None:
        return "not_due"
    try:
        balance = (fetch or fetch_balance)()
    except (ValueError, httpx.HTTPError) as exc:
        return "failed:" + (str(exc) if isinstance(exc, ValueError) and len(str(exc)) < 60 else type(exc).__name__)
    return "ok:" + reconcile(conn, uid, balance, source)


def status(conn):
    """Last reading and last corrections, for the cost card and the model page."""
    last = last_snapshot(conn)
    corrections = conn.execute("SELECT * FROM ai_cost_corrections ORDER BY id DESC LIMIT 20").fetchall()
    return dict(
        last_checked_at=last["taken_at"] if last else None,
        balance_cny=str(last["total_balance"]) if last else None,
        corrections=[
            dict(at=c["corrected_at"], source=c["source"], before_cny=str(c["counted_before_cny"]), after_cny=str(c["counted_after_cny"]),
                 difference_cny=str(c["difference_cny"]), attempts=c["attempts"], actual_spent_cny=str(c["actual_spent_cny"]),
                 balance_before=str(c["balance_before"]), balance_after=str(c["balance_after"]), note=c["note"])
            for c in corrections
        ],
    )
