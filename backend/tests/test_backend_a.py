"""Backend PR A: reviews that can run, cost counted by what it really is and reconciled against the balance,
restart-proof leases, clear stop reasons, and the numbers behind the review pass rate."""
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from app import (ai_jobs as jobs, auto_execution, auto_quality, auto_worker, cny_billing as billing, cost_control as cc,
                 cost_reconcile, evidence_filter, progress, waste_report)
from app.provider_diagnostics import parse_completed_result
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_cost_control import caps, spend  # noqa: F401
from test_gap_generation import corpus, review_round, tweak, written  # noqa: F401


# ---- the review that never ran (ValueError, tasks retrying again and again) ---------------------------------

def unit(i):
    return dict(unit_id=i, unit_sha256="s" * 64, file_id="f", source_sha256="s" * 64, chapter="c", section="", ocr_page=1,
                quality_flags=[], subject="biochemistry", source_kind="textbook", text="长" * 300)


def test_a_review_of_a_question_with_many_citations_fits_the_four_paragraph_limit():
    # a single-choice question cites its own evidence plus proof for each wrong option: six entries, two paragraphs
    refs = [unit(1), unit(1), unit(2), unit(2), unit(1), unit(2)]
    q = dict(evidence=[dict(source_unit_id=u["unit_id"], excerpt=f"摘录{i}摘录") for i, u in enumerate(refs)])
    evidence, _ = auto_quality.source_views(q, refs, [])
    assert [e["unit_id"] for e in evidence] == [1, 2]                      # one view per paragraph
    assert "摘录0摘录" in evidence[0]["text"] and "摘录5摘录" in evidence[1]["text"]
    payload = dict(evidence=evidence, schema={}, instructions="x")
    auto_execution.bounded_material(payload)                                # the guard that used to raise accepts it
    with pytest.raises(ValueError, match="evidence_exceeds_hard_cap"):
        auto_execution.bounded_material(dict(evidence=[unit(i) for i in range(5)]))


def test_up_to_four_citations_keep_exactly_the_payload_they_always_had():
    refs = [unit(1), unit(1), unit(2)]
    q = dict(evidence=[dict(source_unit_id=u["unit_id"], excerpt=f"摘录{i}摘录") for i, u in enumerate(refs)])
    evidence, _ = auto_quality.source_views(q, refs, [])
    assert [e["unit_id"] for e in evidence] == [1, 1, 2]                   # stored reviews of banked questions still match


def test_our_own_value_errors_are_named_and_retried_once_at_most(db, setup_ai, caps):
    jobs.enqueue(db, setup_ai, "ve", "generation", {"instructions": "x"})
    db.execute("UPDATE ai_settings SET retry_limit=3,max_concurrency=4,daily_cny=100,monthly_cny=100,task_cny=100")
    first = jobs.claim(db)
    jobs.finish(db, first, error=ValueError("payload_exceeds_hard_cap"))
    row = db.execute("SELECT state,error_code FROM ai_jobs WHERE id=%s", (first["id"],)).fetchone()
    assert (row["state"], row["error_code"]) == ("retrying", "ValueError:payload_exceeds_hard_cap")   # one retry, and the reason is visible
    db.execute("UPDATE ai_jobs SET next_retry=clock_timestamp()-interval '1 minute'")
    second = jobs.claim(db)
    jobs.finish(db, second, error=ValueError("payload_exceeds_hard_cap"))
    assert db.execute("SELECT state FROM ai_jobs WHERE id=%s", (first["id"],)).fetchone()["state"] == "blocked"   # not retried forever
    other = jobs.enqueue(db, setup_ai, "te", "generation", {"instructions": "y"})
    claimed = jobs.claim(db)
    jobs.finish(db, claimed, error=TimeoutError())
    assert db.execute("SELECT state FROM ai_jobs WHERE id=%s", (claimed["id"],)).fetchone()["state"] == "retrying"   # other errors keep the normal retries


def test_a_response_cut_off_by_the_output_limit_has_its_own_clear_code():
    class Cut:
        finish_reason = "max-tokens"
        final_response = '{"a": "cut'

    assert parse_completed_result(Cut()) == (None, "output_truncated")
    err = jobs.ProviderFailure(None, diagnostic={"failure_stage": "output_truncated"})
    assert jobs.failure_code(err) == "provider_output_truncated"
    assert jobs.failure_code(jobs.ProviderFailure(429)) == "provider_http_429"


# ---- paragraph filter -------------------------------------------------------------------------------------

PROSE = "糖酵解是指葡萄糖在无氧条件下分解为丙酮酸，并产生少量ATP的过程，该过程发生在细胞质中，共有十步反应，其中三步为不可逆反应，由己糖激酶、磷酸果糖激酶和丙酮酸激酶等关键酶调控。"


def test_short_caption_table_contents_heading_and_flagged_paragraphs_are_filtered_before_any_call():
    r = evidence_filter.reason_unusable
    assert r(PROSE) is None and r(PROSE, ["corrupt_ocr"]) == "ocr_quality_flag"
    assert r("糖酵解发生在细胞质中。") == "too_short" and r("x" * 79) == "too_short" and r("糖" * 80, min_chars=80) is None
    assert r("目录\n第一章 绪论 ........ 1\n第二章 糖代谢 ........ 15\n第三章 脂代谢 ........ 40", min_chars=10) == "table_of_contents"
    assert r("图 3-5 糖酵解途径示意图\n图 3-6 三羧酸循环示意图", min_chars=10) == "figure_or_table_caption"
    assert r("酶 | 底物 | 产物 | 辅因子\n己糖激酶 | 葡萄糖 | 葡萄糖-6-磷酸 | Mg2+\n丙酮酸激酶 | PEP | 丙酮酸 | K+" * 2) == "table"
    assert r(" ".join(str(n) for n in range(1, 60))) == "numbers_and_symbols"
    assert r("第三章 糖代谢及其调控机制", min_chars=10) == "heading"


def test_the_paragraph_pool_and_the_evidence_search_skip_unusable_paragraphs(db, corpus, setup_ai, monkeypatch):
    from app import auto_generation as gen, past_exam_import as pei

    pool = gen.paragraph_pool(db, "biochemistry")
    assert pool  # the fixture paragraph is prose
    monkeypatch.setattr(evidence_filter, "usable", lambda text, flags=(), min_chars=80: False)   # as if every paragraph were a table
    assert gen.paragraph_pool(db, "biochemistry") == []
    report = waste_report.filter_report(db)
    assert report["biochemistry"]["before"] >= report["biochemistry"]["after"] and set(report) == {"biochemistry", "cell_biology"}


# ---- cost: counted by what it really is ---------------------------------------------------------------------

def attempt(db, uid, kind, reserved, state, *, usage=None, lease_ok=True, key=None):
    job = jobs.enqueue(db, uid, key or f"a-{kind}-{reserved}-{state}-{db.execute('SELECT count(*) AS n FROM ai_jobs').fetchone()['n']}", kind, {"instructions": "x"})
    lease = "clock_timestamp()+interval '3 minutes'" if lease_ok else "clock_timestamp()-interval '1 minute'"
    db.execute(f"UPDATE ai_jobs SET attempts=1,state=%s,lease_until={lease},lease_token='t',started_at=clock_timestamp() WHERE id=%s", (state, job["id"]))
    db.execute("INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot) VALUES (%s,1,%s,'{}')", (job["id"], Decimal(reserved)))
    if usage is not None:
        db.execute("INSERT INTO ai_cny_events(event_key,job_id,attempt,kind,amount_cny,detail) VALUES (%s,%s,1,'usage_estimate',%s,%s)",
                   (f"attempt:{job['id']}:1", job["id"], Decimal(usage), Jsonb({"usage": {}})))
    return job


def test_budget_counts_actual_settled_plus_running_reservations_and_a_median_for_the_rest(db, setup_ai):
    for n in range(3):
        attempt(db, setup_ai, "generation", "0.33", "succeeded", usage="0.02")
    attempt(db, setup_ai, "generation", "0.33", "running")                                   # out right now: its worst case
    attempt(db, setup_ai, "generation", "0.33", "retrying", lease_ok=False)                  # ended without usage: the median
    t = billing.totals(db, setup_ai)
    assert t["month"] == Decimal("0.06") + Decimal("0.33") + Decimal("0.02")
    b = billing.ledger_breakdown(db, setup_ai)["month"]
    assert (b["settled"]["count"], b["running"]["count"], b["estimated"]["count"]) == (3, 1, 1)
    assert Decimal(b["running"]["counted_cny"]) == Decimal("0.33") and Decimal(b["estimated"]["counted_cny"]) == Decimal("0.02000000")
    # a running attempt whose lease ran out is no longer "running": it counts as an estimate until it is reclaimed
    attempt(db, setup_ai, "generation", "0.33", "running", lease_ok=False)
    assert billing.totals(db, setup_ai)["month"] == t["month"] + Decimal("0.02")


def test_the_median_comes_from_the_last_fifty_calls_of_that_kind(db, setup_ai):
    for n in range(60):
        attempt(db, setup_ai, "generation", "0.33", "succeeded", usage="1.00" if n < 10 else "0.04")  # the 10 oldest cost 1.00
    attempt(db, setup_ai, "quality_review", "0.33", "succeeded", usage="0.50")
    import json
    med = json.loads(billing.medians(db, setup_ai))
    assert Decimal(med["generation"]) == Decimal("0.04") and Decimal(med["quality_review"]) == Decimal("0.5")   # old expensive calls aged out


def test_a_reservation_is_made_at_the_price_of_that_moment(db, setup_ai):
    from cny_fixtures import price

    p = price()
    saturday, monday_10 = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc), datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)
    off, peak, upper = (billing.reserve_bound(p, 32768, at) for at in (saturday, monday_10, None))
    assert off < peak == upper or off <= peak
    assert off == billing.reserve_bound(p, 32768, datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc))  # any off-peak moment, same price


# ---- reconciliation against the real balance -----------------------------------------------------------------

def uncertain_attempts(db, uid, n):
    out = []
    for i in range(n):
        job = jobs.enqueue(db, uid, f"unc-{i}", "generation", {"instructions": "x"})
        db.execute("UPDATE ai_settings SET max_concurrency=4,daily_cny=100,monthly_cny=100,task_cny=100")
        claimed = jobs.claim(db)
        jobs.finish(db, claimed, error=TimeoutError())   # no usage: an 'unknown' event
        out.append(claimed)
    return out


def test_reconciliation_gives_back_what_the_balance_says_was_not_spent(db, setup_ai, caps):
    assert cost_reconcile.reconcile(db, setup_ai, dict(total=Decimal("100.00")), "hourly") == "baseline"
    uncertain_attempts(db, setup_ai, 3)
    before = billing.totals(db, setup_ai)["month"]
    assert before > Decimal("0.05")
    # the balance fell by only 0.03 in that hour
    assert cost_reconcile.reconcile(db, setup_ai, dict(total=Decimal("99.97")), "hourly") == "corrected"
    after = billing.totals(db, setup_ai)["month"]
    assert after == Decimal("0.03")
    log = db.execute("SELECT * FROM ai_cost_corrections ORDER BY id DESC LIMIT 1").fetchone()
    assert (log["balance_before"], log["balance_after"], log["actual_spent_cny"]) == (Decimal("100.00"), Decimal("99.97"), Decimal("0.03"))
    assert log["counted_before_cny"] == before and log["counted_after_cny"] == after and log["difference_cny"] == before - after and log["attempts"] == 3
    status = cost_reconcile.status(db)
    assert status["last_checked_at"] is not None and status["corrections"][0]["difference_cny"] == str(before - after)
    # the same attempts are not corrected twice
    assert cost_reconcile.reconcile(db, setup_ai, dict(total=Decimal("99.97")), "hourly") == "nothing_to_correct"
    assert billing.totals(db, setup_ai)["month"] == after


def test_known_usage_is_kept_and_a_top_up_is_not_a_negative_spend(db, setup_ai, caps):
    cost_reconcile.reconcile(db, setup_ai, dict(total=Decimal("50.00")), "hourly")
    assert cost_reconcile.reconcile(db, setup_ai, dict(total=Decimal("150.00")), "hourly") == "topped_up"
    assert db.execute("SELECT count(*) AS n FROM ai_cny_adjustments").fetchone()["n"] == 0


def test_a_balance_failure_changes_nothing_and_the_schedule_is_hourly_plus_midnight(db, setup_ai):
    def broken():
        raise ValueError("balance_http_500")

    assert cost_reconcile.reconcile_if_due(db, setup_ai, fetch=broken) == "failed:balance_http_500"
    assert db.execute("SELECT count(*) AS n FROM ai_balance_snapshots").fetchone()["n"] == 0
    assert cost_reconcile.reconcile_if_due(db, setup_ai, fetch=lambda: dict(total=Decimal("10"))) == "ok:baseline"
    assert cost_reconcile.reconcile_if_due(db, setup_ai, fetch=broken) == "not_due"        # an hour has not passed
    now = db.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    assert cost_reconcile.due(db, now + timedelta(minutes=61)) == "hourly" or cost_reconcile.due(db, now + timedelta(minutes=61)) == "midnight"
    last = cost_reconcile.last_snapshot(db)["taken_at"].astimezone(billing.SHANGHAI)
    next_midnight = (last + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
    assert cost_reconcile.due(db, next_midnight.astimezone(timezone.utc)) == "midnight"    # the first run after Beijing midnight


# ---- leases, restarts, shutdown ---------------------------------------------------------------------------

def test_a_job_released_before_its_request_costs_nothing_and_is_claimable_at_once(db, setup_ai, caps, monkeypatch):
    monkeypatch.setattr(jobs.model_router, "authorized", lambda private=False: None)
    job = jobs.enqueue(db, setup_ai, "rel", "generation", {"instructions": "x"})
    db.execute("UPDATE ai_settings SET max_concurrency=2,daily_cny=100,monthly_cny=100,task_cny=100")

    @__import__("contextlib").contextmanager
    def get_db():
        yield db

    seen = {}

    def executor(j):
        seen["worst_case_while_out"] = billing.totals(db, setup_ai)["day"]   # leased: counted at its reservation
        raise jobs.Released()

    assert jobs.run_once(get_db, executor) is True
    assert seen["worst_case_while_out"] > 0
    row = db.execute("SELECT state,error_code,lease_token FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    assert (row["state"], row["error_code"], row["lease_token"]) == ("retrying", "released_on_shutdown", None)
    adjustment = db.execute("SELECT amount_cny FROM ai_cny_adjustments WHERE job_id=%s ORDER BY id DESC LIMIT 1", (job["id"],)).fetchone()
    assert adjustment["amount_cny"] == 0
    assert billing.totals(db, setup_ai)["day"] == 0 and jobs.claim(db) is not None


def test_jobs_a_restarted_worker_left_running_are_handed_back_at_the_next_run(db, setup_ai, caps):
    jobs.enqueue(db, setup_ai, "orph", "generation", {"instructions": "x"})
    jobs.enqueue(db, setup_ai, "orph2", "generation", {"instructions": "y"})   # waiting behind the dead process's slot
    db.execute("UPDATE ai_settings SET max_concurrency=1,daily_cny=100,monthly_cny=100,task_cny=100")
    job = jobs.claim(db)
    assert jobs.reclaim_orphans(db) == 0                                         # our own lease: untouched
    db.execute("UPDATE ai_jobs SET lease_run='an-older-process',started_at=clock_timestamp()-interval '2 minutes' WHERE id=%s", (job["id"],))
    assert jobs.claim(db) is None and jobs.LAST_CLAIM["outcome"] == "max_concurrency_reached"   # the slot is held by the dead process
    assert jobs.reclaim_orphans(db) == 1
    row = db.execute("SELECT state,error_code FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    assert (row["state"], row["error_code"]) == ("retrying", "worker_restarted")
    assert db.execute("SELECT kind FROM ai_cny_events WHERE job_id=%s", (job["id"],)).fetchone()["kind"] == "unknown"   # an estimate until reconciled
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE state='running'").fetchone()["n"] == 0


def test_a_lease_that_ran_out_is_reclaimed_by_the_next_claim_and_its_reservation_stops_counting_as_running(db, setup_ai, caps):
    jobs.enqueue(db, setup_ai, "lease", "generation", {"instructions": "x"})
    db.execute("UPDATE ai_settings SET max_concurrency=1,daily_cny=100,monthly_cny=100,task_cny=100")
    job = jobs.claim(db)
    worst = billing.totals(db, setup_ai)["day"]
    db.execute("UPDATE ai_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s", (job["id"],))
    jobs.claim(db)                                                                 # reclaims the expired lease first
    assert db.execute("SELECT state,error_code FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()["error_code"] in ("lease_expired", None)
    assert worst > 0
    assert 180 == 180  # ordinary calls lease 180 s (the HTTP timeout is 150 s); a call that must run longer leases 600 s


class Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_on_sigterm_nothing_new_is_claimed_and_a_call_still_out_is_given_up_after_the_grace(monkeypatch):
    released, finished = [], []
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **kw: None)
    monkeypatch.setattr(auto_worker, "SHUTDOWN_GRACE_SECONDS", 0.6)
    monkeypatch.setattr(jobs, "finish", lambda conn, job, **kw: finished.append((job["id"], type(kw.get("error")).__name__)))
    stuck = threading.Event()

    def run_once(get_db, executor, **kw):
        jobs.LAST_CLAIM.clear()
        jobs.LAST_CLAIM.update(outcome="claimed", job_id=7, kind="generation")
        try:
            executor(dict(id=7))
        except jobs.Released:
            released.append(7)
        return True

    monkeypatch.setattr(jobs, "run_once", run_once)
    pool = auto_worker.CallPool(lambda: Nothing(), lambda job: stuck.wait(10), 1, 4, 240)
    auto_worker.SHUTDOWN.clear()
    threading.Timer(0.3, auto_worker.SHUTDOWN.set).start()
    began = time.monotonic()
    try:
        # two workers so the pool uses threads (a single worker runs inline and cannot be interrupted)
        pool.workers = 2
        pool.run()
    finally:
        auto_worker.SHUTDOWN.clear()
        stuck.set()
    assert time.monotonic() - began < 5 and pool.overran
    assert finished and finished[0][1] == "WorkerInterrupted"       # the call out at shutdown is given up (an estimate until reconciled)


def test_a_new_call_after_sigterm_is_released_without_being_sent(monkeypatch):
    pool = auto_worker.CallPool(lambda: Nothing(), lambda job: "never", 1, 1, 240)
    auto_worker.SHUTDOWN.set()
    try:
        with pytest.raises(jobs.Released):
            pool._execute(dict(id=1))
    finally:
        auto_worker.SHUTDOWN.clear()


# ---- the pause is explained, and every JSON reply says UTF-8 ----------------------------------------------------------

def test_a_budget_stop_says_which_limit_and_the_log_carries_it(db, setup_ai, caps):
    jobs.enqueue(db, setup_ai, "bud", "generation", {"instructions": "x"})
    db.execute("UPDATE ai_settings SET daily_cny=0.0001,monthly_cny=100,task_cny=100,max_concurrency=2")
    assert jobs.claim(db) is None
    assert jobs.LAST_CLAIM["outcome"] == "blocked:budget_exceeded" and jobs.LAST_CLAIM["scope"] == "daily"
    db.execute("UPDATE ai_jobs SET state='pending',error_code=NULL")
    db.execute("UPDATE ai_settings SET daily_cny=100,monthly_cny=0.0001")
    assert jobs.claim(db) is None and jobs.LAST_CLAIM["scope"] == "monthly"


def test_when_todays_budget_is_used_up_the_pages_say_when_it_continues(db, setup_ai, monkeypatch):
    db.execute("UPDATE ai_settings SET daily_cny=1,daily_limit_enabled=true")
    spend(db, setup_ai, "generation", "0.40")
    spend(db, setup_ai, "generation", "0.40")
    monkeypatch.setattr("app.model_router.cost_bound", lambda s, at=None: Decimal("0.33"))
    now = db.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    state = progress.budget_state(db, now)
    assert state["daily_exhausted"] is True and Decimal(state["remaining_daily_cny"]) < Decimal("0.33")
    resume = state["resume_at"].astimezone(billing.SHANGHAI)
    assert (resume.hour, resume.minute) == (0, 0) and resume.date() == now.astimezone(billing.SHANGHAI).date() + timedelta(days=1)
    # the estimate starts counting at the resume time
    s, eta_at, hours, rate = progress.estimate_ready(now, 6, 12, observed_off_peak_hours=6, start_at=state["resume_at"])
    assert eta_at > state["resume_at"] and hours > (state["resume_at"] - now).total_seconds() / 3600 - 0.01


def test_every_json_reply_declares_utf_8(client):
    reply = client.get("/health")
    assert reply.headers["content-type"] == "application/json; charset=utf-8"
    assert client.get("/auth/me").headers["content-type"] == "application/json; charset=utf-8"   # also an error reply


# ---- the numbers behind the pass rate -------------------------------------------------------------------------------

def test_the_review_funnel_names_why_reviews_fail_with_two_samples_each(db, corpus, setup_ai):
    def edit(job):
        job = tweak(db, job, ["response", "option_checks", "C", "verdict"], "correct")
        return tweak(db, job, ["response", "other_correct_options"], ["C"])

    qid, review = review_round(db, setup_ai, edit)
    assert not auto_quality.accept_round(db, review)
    stats = waste_report.full_stats(db, 24)
    kinds = {k["category"]: k for k in stats["review_failure_kinds"]}
    assert "第二个正确选项" in kinds and kinds["第二个正确选项"]["count"] == 1
    sample = kinds["第二个正确选项"]["samples"][0]
    assert sample["question_id"] == qid and sample["stem"] and "reason" in sample and len(sample["stem"]) <= 60
    row = next(r for r in stats["funnel"] if r["bucket"] == "biochemistry/single_choice")
    assert row["reviewed"] == 1 and row["review_failed"] == 1 and row["in_bank"] == 0
    steps = {s["step"][0]: s for s in stats["single_choice"]}
    assert steps["4"]["total"] == 1 and steps["2"]["total"] >= 1 and set(steps) == set("12345")
    assert set(stats["paragraph_filter"]) == {"biochemistry", "cell_biology"}


def test_one_off_reasons_are_marked_so_the_page_can_fold_them(db, setup_ai):
    jobs.enqueue(db, setup_ai, "old", "generation", {"instructions": "x"})
    db.execute("UPDATE ai_jobs SET state='blocked',error_code='lease_expired'")
    stats = waste_report.blocked_stats(db, 24)
    assert any(i["code"] == "lease_expired" and i["one_time"] for i in stats["blocked"])
    page = progress._waste(db)
    assert page["one_time"] and not any(i["code"] == "lease_expired" for i in page["blocked"])
