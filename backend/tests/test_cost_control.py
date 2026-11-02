"""Step 7.5: off-peak running, thinking levels, monthly caps, reuse fallback, spend report.

Fake results only; no request is ever sent.
"""
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from app import ai_jobs, cost_control as cc, cny_billing, harness_runner, quiz_v2
from test_ai_automation import setup_ai  # noqa: F401  (fixture)

BJ = cny_billing.SHANGHAI


def beijing(text):
    return datetime.fromisoformat(text).replace(tzinfo=BJ).astimezone(timezone.utc)


# ---- off-peak --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "when,peak",
    [
        ("2026-11-03 08:59", False), ("2026-11-03 09:00", True), ("2026-11-03 11:59", True),
        ("2026-11-03 12:00", False), ("2026-11-03 13:59", False), ("2026-11-03 14:00", True),
        ("2026-11-03 17:59", True), ("2026-11-03 18:00", False), ("2026-11-03 23:30", False),
        ("2026-11-03 03:00", False),
        ("2026-11-07 10:00", False), ("2026-11-08 15:00", False),  # weekend
        ("2026-10-01 10:00", False),  # National Day holiday (a Thursday)
        ("2026-02-17 10:00", False),  # Spring Festival
    ],
)
def test_peak_is_beijing_weekday_working_hours_and_holidays_are_off_peak(when, peak):
    assert cc.is_peak(beijing(when)) is peak


def test_without_a_verified_calendar_the_weekday_rule_still_applies():
    assert cc.is_peak(beijing("2027-03-03 10:00"), calendar={}) is True  # no 2027 calendar: never cheaper than reality
    assert cc.is_peak(beijing("2027-03-06 10:00"), calendar={}) is False


def test_the_billing_modules_own_band_agrees_with_the_off_peak_rule():
    calendar = cny_billing.calendar_2026()
    for text in ("2026-11-03 10:00", "2026-11-03 13:00", "2026-11-03 15:00", "2026-10-02 10:00", "2026-11-07 10:00"):
        at = beijing(text)
        assert (cny_billing.period(at, calendar) == "peak") == cc.is_peak(at, calendar), text


def enqueue_all(db, uid):
    for kind in ("grading", "generation", "quality_review", "repair"):
        ai_jobs.enqueue(db, uid, f"k-{kind}", kind, {"instructions": "x", "evidence": [{"unit_id": 1, "text": "教材"}], "n": kind})


def test_writing_and_review_wait_at_peak_but_grading_runs(db, setup_ai, monkeypatch):
    enqueue_all(db, setup_ai)
    monkeypatch.setattr(cc, "is_peak", lambda at=None, calendar=None: True)
    claimed = ai_jobs.claim(db)
    assert claimed["kind"] == "grading"
    assert ai_jobs.claim(db) is None  # everything else waits ...
    waiting = {r["kind"] for r in db.execute("SELECT kind FROM ai_jobs WHERE state='pending'").fetchall()}
    assert waiting == {"generation", "quality_review", "repair"}  # ... and is not dropped
    db.execute("UPDATE ai_jobs SET state='succeeded' WHERE id=%s", (claimed["id"],))
    monkeypatch.setattr(cc, "is_peak", lambda at=None, calendar=None: False)
    started = set()
    for _ in range(3):
        job = ai_jobs.claim(db)
        started.add(job["kind"])
        db.execute("UPDATE ai_jobs SET state='succeeded' WHERE id=%s", (job["id"],))
    assert started == {"generation", "quality_review", "repair"}


# ---- thinking levels -----------------------------------------------------------------------


@pytest.mark.parametrize("kind,env,expected", [
    ("generation", None, "off"), ("repair", None, "off"), ("quality_review", None, "low"), ("grading", None, None),
    ("generation", "high", "high"), ("quality_review", "max", "max"), ("grading", "low", "low"),
    ("generation", "nonsense", "off"), ("quality_review", "", "low"),
])
def test_thinking_defaults_and_environment_overrides(monkeypatch, kind, env, expected):
    for name in ("AI_GENERATION_THINKING", "AI_REVIEW_THINKING", "AI_GRADING_THINKING"):
        monkeypatch.delenv(name, raising=False)
    if env is not None:
        monkeypatch.setenv(cc.THINKING_ENV[kind], env)
    assert cc.effort_for(kind) == expected


def test_jobs_are_frozen_with_their_kinds_thinking_level(db, setup_ai):
    efforts = {}
    for kind in ("generation", "quality_review", "grading"):
        job = ai_jobs.enqueue(db, setup_ai, f"e-{kind}", kind, {"instructions": "x", "n": kind})
        efforts[kind] = job["settings_snapshot"]["effort"]
    assert efforts == {"generation": "off", "quality_review": "low", "grading": "high"}  # grading keeps the model setting


def test_a_level_the_model_was_never_probed_at_is_refused_not_silently_used(db, setup_ai, monkeypatch):
    monkeypatch.setenv("AI_REVIEW_THINKING", "max")  # the test model is verified for off/low/high only
    with pytest.raises(ValueError, match="model_effort_not_compatibility_verified"):
        ai_jobs.enqueue(db, setup_ai, "e", "quality_review", {"instructions": "x"})


# ---- prompt ordering for the prefix cache ------------------------------------------------


def test_the_fixed_part_of_every_prompt_comes_first_and_is_shared():
    review = lambda q: {"question": q, "evidence": [q], "phase": "review", "schema": {"a": 1}, "instructions": "fixed rules",
                        "content_sha256": q, "question_id": 1, "source_level": "x"}
    one, two = (harness_runner.build_prompt(review(q)) for q in ("第一道题的内容", "另一道完全不同的题"))
    prefix = harness_runner.PROMPT_PREFIX
    head = '{"instructions": "fixed rules", "schema": {"a": 1}, "phase": "review", "source_level": "x"'
    assert one.startswith(prefix + head) and two.startswith(prefix + head)
    common = len(__import__("os").path.commonprefix([one, two]))
    assert common > len(prefix + head)  # the shared prefix covers all fixed text
    body = one[len(prefix):]
    assert body.index("第一道题") > body.index("fixed rules") and body.index('"evidence"') > body.index('"schema"')
    assert json.loads(one[len(prefix):]) == review("第一道题的内容")  # nothing lost or altered


def test_real_review_and_generation_payloads_put_instructions_and_schema_first(db, setup_ai):
    payload = {"question": {"stem": "s"}, "evidence": [], "style": [], "schema": {"s": 1}, "instructions": "rules", "phase": "review"}
    text = harness_runner.build_prompt(payload)[len(harness_runner.PROMPT_PREFIX):]
    assert list(json.loads(text)) == ["instructions", "schema", "phase", "evidence", "question", "style"]


# ---- monthly cap with a grading reserve ------------------------------------------------------


def spend(db, uid, kind, amount, *, settled=True, month_offset=0, key=None):
    """One attempt of the given kind that cost `amount` (settled) or is counted at its reservation."""
    job = ai_jobs.enqueue(db, uid, key or f"s-{kind}-{amount}-{settled}-{month_offset}-{db.execute('SELECT count(*) AS n FROM ai_jobs').fetchone()['n']}",
                          kind, {"instructions": "x"})
    db.execute("UPDATE ai_jobs SET attempts=1,state='succeeded',dispatched_at=clock_timestamp() WHERE id=%s", (job["id"],))
    created = "clock_timestamp()" if not month_offset else "clock_timestamp()-interval '40 days'"
    db.execute(f"INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot,created_at) VALUES (%s,1,%s,'{{}}',{created})",
               (job["id"], Decimal("1") if settled else Decimal(amount)))
    if settled:
        db.execute(f"INSERT INTO ai_cny_events(event_key,job_id,attempt,kind,amount_cny,detail,created_at) VALUES (%s,%s,1,'usage_estimate',%s,%s,{created})",
                   (f"e{job['id']}", job["id"], Decimal(amount),
                    Jsonb({"usage": {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 3000}, "period_start": "off_peak"})))
    return job


@pytest.fixture
def caps(monkeypatch):
    monkeypatch.setenv("AI_MONTHLY_CAP_CNY", "90")
    monkeypatch.setenv("AI_GRADING_RESERVE_CNY", "15")


def test_the_defaults_are_90_with_15_reserved_and_bad_values_fall_back(monkeypatch):
    for name in ("AI_MONTHLY_CAP_CNY", "AI_GRADING_RESERVE_CNY"):
        monkeypatch.delenv(name, raising=False)
    assert cc.caps() == (Decimal(90), Decimal(15))
    monkeypatch.setenv("AI_MONTHLY_CAP_CNY", "abc")
    monkeypatch.setenv("AI_GRADING_RESERVE_CNY", "-3")
    assert cc.caps() == (Decimal(90), Decimal(15))
    monkeypatch.setenv("AI_MONTHLY_CAP_CNY", "60")
    monkeypatch.setenv("AI_GRADING_RESERVE_CNY", "100")
    assert cc.caps() == (Decimal(60), Decimal(60))  # the reserve can never exceed the cap


def test_writing_and_review_stop_at_75_while_grading_continues_to_90(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "40")
    spend(db, setup_ai, "quality_review", "34.9")
    assert cc.cap_reason(db, setup_ai, "generation", Decimal("0.1")) is None  # 74.9 + 0.1 = 75
    assert cc.cap_reason(db, setup_ai, "generation", Decimal("0.2")) == "budget_exceeded"
    assert cc.cap_reason(db, setup_ai, "quality_review", Decimal("0.2")) == "budget_exceeded"
    assert cc.cap_reason(db, setup_ai, "repair", Decimal("0.2")) == "budget_exceeded"
    assert cc.cap_reason(db, setup_ai, "grading", Decimal("14")) is None  # grading may still use the reserve
    spend(db, setup_ai, "grading", "15")  # 74.9 + 15 = 89.9
    assert cc.cap_reason(db, setup_ai, "grading", Decimal("0.1")) is None
    assert cc.cap_reason(db, setup_ai, "grading", Decimal("0.2")) == "budget_exceeded"  # hard cap 90


def test_money_spent_on_grading_shrinks_only_the_unused_part_of_the_reserve(db, setup_ai, caps):
    spend(db, setup_ai, "grading", "5")
    spend(db, setup_ai, "generation", "70")  # 75 total; the writing limit is 90 - (15 - 5) = 80
    assert cc.cap_reason(db, setup_ai, "generation", Decimal("5")) is None
    assert cc.cap_reason(db, setup_ai, "generation", Decimal("5.01")) == "budget_exceeded"
    spend(db, setup_ai, "grading", "20", key="more-grading")  # reserve fully used and then some
    # 5 + 20 + 70 = 95 is past the hard cap: now nothing runs, grading included
    assert cc.cap_reason(db, setup_ai, "generation", Decimal("0.01")) == "budget_exceeded"
    assert cc.cap_reason(db, setup_ai, "grading", Decimal("0.01")) == "budget_exceeded"


def test_the_cap_is_per_calendar_month_not_all_time(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "80", month_offset=1)
    assert cc.cap_reason(db, setup_ai, "generation", Decimal("1")) is None


def test_a_settled_call_counts_what_it_cost_a_failed_one_its_worst_case(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "0.02", settled=True)  # reservation was 1, real cost 0.02
    assert cc.month_split(db, setup_ai) == (Decimal(0), Decimal("0.02"))
    spend(db, setup_ai, "generation", "0.5", settled=False, key="failed")  # timed out: nobody knows, keep the ceiling
    assert cc.month_split(db, setup_ai)[1] == Decimal("0.52")
    assert cny_billing.totals(db, setup_ai)["month"] == Decimal("0.52")  # the user limits use the same measure


def test_claim_blocks_writing_when_the_cap_is_reached_and_still_serves_grading(db, setup_ai, caps, monkeypatch):
    from app import model_router

    # the learner's own limits are generous here; only the global monthly cap is under test
    model_router.configure(db, setup_ai, "deepseek-flash", "high", stopped=False, daily_cny=500, monthly_cny=500, task_cny=500, trial_cny=500)
    monkeypatch.setattr(cc, "is_peak", lambda at=None, calendar=None: False)
    spend(db, setup_ai, "generation", "75")
    ai_jobs.enqueue(db, setup_ai, "w", "generation", {"instructions": "x"})
    assert ai_jobs.claim(db) is None
    row = db.execute("SELECT state,error_code FROM ai_jobs WHERE job_key='w'").fetchone()
    assert (row["state"], row["error_code"]) == ("blocked", "budget_exceeded")
    ai_jobs.enqueue(db, setup_ai, "g", "grading", {"instructions": "x"})
    assert ai_jobs.claim(db)["kind"] == "grading"


# ---- spend report --------------------------------------------------------------------------------


def test_the_spend_report_gives_today_month_tokens_calls_and_cost_per_admitted_question(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "0.30")
    spend(db, setup_ai, "quality_review", "0.20")
    spend(db, setup_ai, "grading", "0.50")
    spend(db, setup_ai, "generation", "9", month_offset=1)
    report = cc.spend_report(db, setup_ai)
    assert Decimal(report["today_cny"]) == Decimal("1.00")
    assert Decimal(report["month_cny"]) == Decimal("1.00")
    assert report["tokens"]["month"] == {"input_tokens": 3000, "cached_tokens": 9000, "output_tokens": 1500, "calls": 3}
    assert {r["kind"]: r["calls"] for r in report["by_kind"]} == {"generation": 1, "quality_review": 1, "grading": 1}
    assert len(report["recent_calls"]) == 4 and report["recent_calls"][0]["cached_tokens"] == 3000
    assert report["cost_per_admitted_question_cny"]["month"] is None  # nothing admitted yet
    assert report["month_cap_cny"] == "90" and report["grading_reserve_cny"] == "15"
    assert report["ai_quota_exhausted"] is False and report["message"] is None
    # two questions are admitted this month: writing+review spend 0.50 / 2
    for n in (1, 2):
        qid = db.execute("INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id", (Jsonb({"n": n}),)).fetchone()["id"]
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
        db.execute("ALTER TABLE reviewed_questions DISABLE TRIGGER USER")
        db.execute("UPDATE reviewed_questions SET status='published' WHERE id=%s", (qid,))
        db.execute("ALTER TABLE reviewed_questions ENABLE TRIGGER USER")
    again = cc.spend_report(db, setup_ai)
    assert again["admitted_questions"]["month"] == 2
    assert again["cost_per_admitted_question_cny"]["month"] == "0.2500"


def test_the_report_says_so_when_the_quota_is_used_up(db, setup_ai, caps):
    spend(db, setup_ai, "generation", "76")
    report = cc.spend_report(db, setup_ai)
    assert report["writing_review_exhausted"] is True and report["ai_quota_exhausted"] is False
    assert report["message"] == "本月 AI 出题与审核额度已用完，判分仍可使用"
    spend(db, setup_ai, "grading", "14")
    report = cc.spend_report(db, setup_ai)
    assert report["ai_quota_exhausted"] is True and report["message"] == "本月 AI 额度已用完"
    assert report["remaining_grading_cny"] == "0"


def test_the_spend_endpoint_is_authenticated_and_returns_the_report(client, db, setup_ai, monkeypatch):
    from contextlib import contextmanager

    from app import model_api

    @contextmanager
    def wrapped():
        yield db

    monkeypatch.setattr(model_api, "get_db", wrapped)
    assert client.get("/v2/model/spend").status_code in (401, 403)
    body = client.get("/v2/model/spend", headers={"x-api-key": "learner-test-key"})
    assert body.status_code == 200 and {"today_cny", "month_cny", "cost_per_admitted_question_cny", "off_peak"} <= set(body.json())


# ---- topping up with older questions -----------------------------------------------------------------


@pytest.fixture
def old_paper(db, bank, blueprint):
    from app.common import ensure_user

    uid = ensure_user(db, "reuse-learner")
    first_day = date.today() - timedelta(days=20)
    first = quiz_v2.create_paper(db, uid, blueprint, day=first_day)
    return uid, first, first_day


def items(db, paper_id):
    return {r["question_id"]: r["is_reused"] for r in db.execute("SELECT question_id,is_reused FROM paper_items_v2 WHERE paper_id=%s", (paper_id,)).fetchall()}


def test_when_new_questions_run_out_questions_seen_14_or_more_days_ago_fill_the_paper(db, old_paper, blueprint):
    from app import training_sessions

    uid, first, first_day = old_paper
    paper = quiz_v2.create_paper(db, uid, blueprint, day=date.today())
    assert paper["count"] == 28 and paper["includes_reused"] is True
    assert set(items(db, paper["paper_id"])) == set(items(db, first["paper_id"])) and all(items(db, paper["paper_id"]).values())
    assert not any(items(db, first["paper_id"]).values()) and not quiz_v2.load_paper(db, uid, first["paper_id"])["includes_reused"]
    training_sessions.start(db, uid, paper["paper_id"])
    assert all(q["reused"] for q in quiz_v2.load_paper(db, uid, paper["paper_id"])["questions"])  # every card says so


def test_questions_seen_recently_are_never_reused(db, bank, blueprint):
    from app.common import ensure_user

    uid = ensure_user(db, "recent-learner")
    quiz_v2.create_paper(db, uid, blueprint, day=date.today() - timedelta(days=13))
    with pytest.raises(quiz_v2.BankShortage):
        quiz_v2.create_paper(db, uid, blueprint, day=date.today())


def test_fresh_questions_come_first_and_only_the_gap_is_reused(db, old_paper, blueprint, question_factory, passing_audit):
    from app.quiz_v2 import create_question, publish_question, review_question

    uid, first, _ = old_paper
    fresh = []
    for subject in ("biochemistry", "cell_biology"):
        for _ in range(3):  # a whole new single_choice quota for each subject
            qid = create_question(db, question_factory(subject, "single_choice"))["id"]
            assert review_question(db, qid, passing_audit)["passed"]
            publish_question(db, qid)
            fresh.append(qid)
    paper = quiz_v2.create_paper(db, uid, blueprint, day=date.today())
    flags = items(db, paper["paper_id"])
    reused = {qid for qid, flag in flags.items() if flag}
    chosen = set(flags)
    assert set(fresh) <= chosen  # every new single-choice question is used
    assert len(reused) == 28 - 6 and not reused & set(fresh)


def test_a_reported_question_is_not_reused(db, old_paper, blueprint):
    uid, first, first_day = old_paper
    quiz_v2.load_paper(db, uid, first["paper_id"])
    from app import training_sessions

    training_sessions.start(db, uid, first["paper_id"])
    victim = quiz_v2.load_paper(db, uid, first["paper_id"])["questions"][0]
    quiz_v2.report_question(db, uid, first["paper_id"], victim["id"])
    with pytest.raises(quiz_v2.BankShortage) as short:
        quiz_v2.create_paper(db, uid, blueprint, day=date.today())
    assert [(s["subject"], s["question_type"], s["missing"]) for s in short.value.shortages if s["missing"]] == [(victim["subject"], victim["question_type"], 1)]


# ---- dates up to the project end (2026-12-19) --------------------------------------------

PROJECT_END = date(2026, 12, 19)


def test_every_half_hour_until_the_project_end_has_one_verified_answer_in_both_modules():
    calendar = cny_billing.calendar_2026()
    assert calendar["verified"] and calendar["year"] == 2026
    at = beijing("2026-11-01 00:00")
    end = beijing("2026-12-20 00:00")
    peaks = 0
    while at < end:
        assert cny_billing.period(at, calendar) in ("peak", "off_peak"), at  # never "unknown" before the end
        assert (cny_billing.period(at, calendar) == "peak") == cc.is_peak(at, calendar), at
        peaks += cc.is_peak(at, calendar)
        at += timedelta(minutes=30)
    assert peaks > 0  # weekdays in Nov-Dec 2026 do have peak hours


def test_no_holiday_is_listed_between_november_and_the_project_end():
    # 2026 official notice: the last public holiday of the year is National Day; Nov-Dec are ordinary.
    holidays = [d for d in cny_billing.calendar_2026()["holidays"] if "2026-11-01" <= d <= PROJECT_END.isoformat()]
    assert holidays == []


def test_month_and_day_windows_follow_beijing_time_across_the_december_boundary(db):
    row = db.execute(
        "SELECT (date_trunc('month',%s::timestamptz AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai') AS m",
        (beijing("2026-11-30 23:59"),),
    ).fetchone()
    assert row["m"] == beijing("2026-11-01 00:00")
    row = db.execute(
        "SELECT (date_trunc('month',%s::timestamptz AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai') AS m",
        (beijing("2026-12-01 00:00"),),
    ).fetchone()
    assert row["m"] == beijing("2026-12-01 00:00")
