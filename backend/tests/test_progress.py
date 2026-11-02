from decimal import Decimal
"""Part 2: the question-writing progress page. Read-only numbers; fake data only."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

from app import ai_jobs, auto_worker, cost_control, progress
from app.question_quality import Question
from app.training_spec_v4 import COUNTS, POINTS, SUBJECTS
from spec_helpers import rubric
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_api_v2 import LEARNER

SECRET_TEXT = "绝不应出现在进度页里的题干文字"


def question(subject="biochemistry", kind="term", points=6, stem=SECRET_TEXT):
    return Question.model_validate(dict(
        subject=subject, question_type=kind, stem=stem, options={}, answer="参考答案" * 8, accepted_answers=[], blanks=[],
        difficulty="core", knowledge_tags=[], max_points=points, rubric=rubric(kind, points),
        evidence=[dict(textbook="教材", authors="作者", edition="版次", chapter="章", locator="OCR:1:UNIT:1", excerpt="原文")],
    ))


def add_question(db, status, reason=None, subject="biochemistry", kind="term", points=6):
    qid = db.execute("INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id",
                     (Jsonb(question(subject, kind, points).model_dump()),)).fetchone()["id"]
    db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256,reason) VALUES (%s,%s,'x',%s)",
               (qid, status, reason))
    return qid


def add_job(db, uid, key, state, kind="generation", subject="biochemistry", qtype="term", error=None):
    return db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code) VALUES (%s,%s,%s,%s,'{}',%s,%s) RETURNING id",
        (key, uid, kind, state, Jsonb({"subject": subject, "question_type": qtype, "max_points": POINTS[qtype]}), error)).fetchone()["id"]


def bucket(report, subject, kind):
    return next(b for b in report["buckets"] if b["subject"] == subject and b["question_type"] == kind)


def test_an_empty_system_reports_ten_buckets_with_zeroes_and_an_honest_worker_state(db, setup_ai):
    report = progress.report(db, setup_ai)
    assert len(report["buckets"]) == 10 and {b["subject"] for b in report["buckets"]} == set(SUBJECTS)
    assert all(b["target"] == 2 * COUNTS[b["question_type"]] and b["stocked"] == 0 for b in report["buckets"])
    assert report["totals"]["required"] == 60 and report["totals"]["target"] == 120
    assert report["worker"]["outcome"] == "never_ran" and report["worker"]["stale"] is True
    assert report["next_paper"]["missing_questions"] == 60 and report["next_paper"]["eta_hours"] is None
    assert report["recent_jobs"] == []


def test_each_stage_is_counted_in_the_right_bucket_with_block_reasons(db, setup_ai, monkeypatch):
    monkeypatch.setenv("AI_BANK_TARGET_PER_BUCKET", "5")
    add_question(db, "checking")
    add_question(db, "candidate")
    add_question(db, "repairing")
    add_question(db, "blocked", "unresolved_quality_findings")
    add_question(db, "blocked", "unresolved_quality_findings")
    add_question(db, "retired", "superseded_scoring")  # not counted anywhere
    add_job(db, setup_ai, "q1", "pending")
    add_job(db, setup_ai, "q2", "retrying")
    add_job(db, setup_ai, "g1", "running")
    add_job(db, setup_ai, "b1", "blocked", error="budget_exceeded")
    add_job(db, setup_ai, "old", "blocked", error="superseded_scoring")  # replaced scoring: not shown
    add_job(db, setup_ai, "other", "pending", subject="cell_biology", qtype="essay")
    row = bucket(progress.report(db, setup_ai), "biochemistry", "term")
    assert (row["queued"], row["generating"], row["reviewing"], row["blocked"], row["target"]) == (2, 1, 3, 3, 5)
    assert row["blocked_reasons"] == {"unresolved_quality_findings": 2, "budget_exceeded": 1}
    other = bucket(progress.report(db, setup_ai), "cell_biology", "essay")
    assert other["queued"] == 1 and other["blocked"] == 0


def test_stocked_comes_from_the_verified_eligible_count(db, setup_ai, monkeypatch):
    monkeypatch.setattr(progress, "eligible_reserve_count", lambda conn, uid, subject, kind: 3 if kind == "term" else 0)
    report = progress.report(db, setup_ai)
    assert bucket(report, "biochemistry", "term")["stocked"] == 3 and report["totals"]["stocked"] == 6
    assert report["next_paper"]["missing_questions"] == 60 - 2 * 3 + 0  # both term buckets hold 3 of 4


def test_recent_jobs_show_kind_state_reason_and_time_but_never_content(db, setup_ai):
    ids = [add_job(db, setup_ai, f"j{n}", "pending" if n % 2 else "succeeded") for n in range(25)]
    add_job(db, setup_ai, "held", "pending", kind="quality_review")
    jobs = progress.report(db, setup_ai)["recent_jobs"]
    assert len(jobs) == 20 and set(jobs[0]) == {"id", "kind", "label", "source", "state", "reason", "detail", "at", "subject", "question_type", "question_id", "attempts", "next_retry", "outcome", "awaiting_dispatch"}
    assert len(ids) == 25
    text = json.dumps(progress.report(db, setup_ai), default=str, ensure_ascii=False)
    assert SECRET_TEXT not in text


def test_a_job_waiting_for_peak_hours_says_so(db, setup_ai, monkeypatch):
    monkeypatch.setattr(cost_control, "held_back_kinds", lambda at=None: ["generation", "quality_review", "repair"])
    add_job(db, setup_ai, "wait", "pending", kind="quality_review")
    assert progress.report(db, setup_ai)["recent_jobs"][0]["reason"] == "waiting_for_off_peak"


def test_the_peak_outlook_names_the_next_off_peak_start():
    monday_10 = datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc)  # 10:00 Beijing, Monday
    out = cost_control.peak_outlook(monday_10)
    assert out["is_peak"] is True and out["next_off_peak_at"] == datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)
    evening = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)  # 20:00 Beijing
    out = cost_control.peak_outlook(evening)
    assert out["is_peak"] is False and out["next_peak_at"] == datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)
    assert cost_control.peak_outlook(saturday)["is_peak"] is False


def test_the_eta_comes_from_recent_approvals(db, setup_ai, monkeypatch):
    monkeypatch.setattr(progress, "approved_recently", lambda conn: 12)
    p = progress.report(db, setup_ai)["next_paper"]
    assert p["approved_in_window"] == 12 and p["per_hour"] == 0.25
    assert p["eta_hours"] == 240.0 and p["complete"] is False  # 60 missing at a quarter question per hour
    monkeypatch.setattr(progress, "eligible_reserve_count", lambda conn, uid, subject, kind: 99)
    done = progress.report(db, setup_ai)["next_paper"]
    assert done["complete"] is True and done["missing_questions"] == 0 and done["eta_hours"] is None


def test_a_heartbeat_is_written_each_run_and_read_back(db, setup_ai, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    monkeypatch.setattr(auto_worker, "get_db", test_db)
    monkeypatch.setattr(auto_worker, "tick", lambda: None)
    monkeypatch.setattr(auto_worker, "integrations_tick", lambda: None)
    monkeypatch.setattr(auto_worker.model_router, "authorized", lambda private=False: None)
    monkeypatch.setattr("app.deployment_target.verify_isolated_target", lambda: None)
    monkeypatch.setenv("AI_AUTOMATION_ENABLED", "1")
    monkeypatch.setenv("QUIZ_DB_SCHEMA", "source_staging_x")
    monkeypatch.setattr(cost_control, "held_back_kinds", lambda at=None: [])
    auto_worker.main()
    w = progress.report(db, setup_ai)["worker"]
    assert w["outcome"] == "ok" and w["stale"] is False and w["minutes_ago"] == 0
    monkeypatch.setenv("AI_AUTOMATION_ENABLED", "0")
    auto_worker.main()
    assert progress.report(db, setup_ai)["worker"]["outcome"] == "disabled"
    monkeypatch.setenv("AI_AUTOMATION_ENABLED", "1")
    monkeypatch.setattr("app.deployment_target.verify_isolated_target", lambda: (_ for _ in ()).throw(PermissionError("x")))
    with pytest.raises(SystemExit):
        auto_worker.main()
    assert progress.report(db, setup_ai)["worker"]["outcome"] == "error:PermissionError"


def test_an_old_heartbeat_is_flagged_stale(db, setup_ai):
    db.execute("INSERT INTO worker_heartbeats(started_at,finished_at,outcome) VALUES (clock_timestamp()-interval '1 hour',clock_timestamp()-interval '50 minutes','ok')")
    w = progress.report(db, setup_ai)["worker"]
    assert w["stale"] is True and w["minutes_ago"] >= 49


def test_heartbeats_older_than_two_weeks_are_pruned(db, setup_ai, monkeypatch):
    db.execute("INSERT INTO worker_heartbeats(started_at,finished_at,outcome) VALUES (now()-interval '20 days',now()-interval '20 days','ok')")
    from contextlib import contextmanager

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    monkeypatch.setattr(auto_worker, "get_db", test_db)
    auto_worker.record_heartbeat({"enabled": "yes"}, datetime.now(timezone.utc), "")
    assert db.execute("SELECT count(*) AS n FROM worker_heartbeats").fetchone()["n"] == 1


def test_the_endpoint_needs_login_and_returns_only_numbers(client, db, monkeypatch):
    from contextlib import contextmanager
    from app import model_api

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    monkeypatch.setattr(model_api, "get_db", test_db)
    assert client.get("/v2/model/progress").status_code == 401
    response = client.get("/v2/model/progress", headers=LEARNER)
    assert response.status_code == 200
    body = response.json()
    assert len(body["buckets"]) == 10 and "spend" in body and "worker" in body and "next_paper" in body
    assert SECRET_TEXT not in response.text and "DEEPSEEK" not in response.text
    # the same "today's remaining budget" the model page's ledger shows (daily budget minus today's usage)
    assert body["spend"]["remaining_daily_cny"] == "0"  # no daily budget set yet
    db.execute("UPDATE ai_settings SET daily_cny=15")
    again = client.get("/v2/model/progress", headers=LEARNER).json()
    assert Decimal(again["spend"]["remaining_daily_cny"]) == Decimal("15")
    page = client.get("/progress")
    assert page.status_code == 200 and "出题进度" in page.text and page.headers["cache-control"] == "no-store"
    home = client.get("/training").text
    assert 'href="/progress"' in home


# ---- only the current specification counts in the stages --------------------------------------------------


def old_job(db, uid, key, state="blocked", error="invalid_qa_automation_grant", points=2, kind="generation"):
    return db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code) VALUES (%s,%s,%s,%s,'{}',%s,%s) RETURNING id",
        (key, uid, kind, state, Jsonb({"subject": "biochemistry", "question_type": "single_choice", "max_points": points}), error)).fetchone()["id"]


def test_work_from_an_earlier_scale_is_folded_into_one_history_line(db, setup_ai):
    old_job(db, setup_ai, "o1")
    old_job(db, setup_ai, "o2", error="OSError")
    old_job(db, setup_ai, "o3", state="pending")  # a stale queued job: neither a stage nor history
    db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code) VALUES ('nopoints',%s,'generation','blocked','{}',%s,'OSError')",
               (setup_ai, Jsonb({"subject": "cell_biology", "question_type": "term"})))  # GPT-era trial job without a point value
    add_question(db, "blocked", "unresolved_quality_findings", kind="term", points=4)  # the old term scale was 4
    add_question(db, "blocked", "rubric_not_fully_evidenced", kind="term", points=6)  # current: stays in its cell
    add_job(db, setup_ai, "now", "pending")
    report = progress.report(db, setup_ai)
    term = bucket(report, "biochemistry", "term")
    assert (term["queued"], term["blocked"]) == (1, 1) and term["blocked_reasons"] == {"rubric_not_fully_evidenced": 1}
    assert bucket(report, "biochemistry", "single_choice")["blocked"] == 0 and bucket(report, "cell_biology", "term")["blocked"] == 0
    assert report["legacy"]["count"] == 4
    assert {r["code"]: r["count"] for r in report["legacy"]["reasons"]} == {"invalid_qa_automation_grant": 1, "OSError": 2, "unresolved_quality_findings": 1}
    assert sum(b["blocked"] for b in report["buckets"]) == 1  # the stage totals never include history


def test_each_cell_shows_how_many_were_admitted_today_and_the_review_pass_rate(db, setup_ai):
    from stats_helpers import publish

    first = add_question(db, "ready")
    second = add_question(db, "ready")
    third = add_question(db, "blocked", "rubric_not_fully_evidenced")
    publish(db, [first, second])
    db.execute("UPDATE auto_question_states SET updated_at=clock_timestamp()-interval '3 days' WHERE question_id=%s", (second,))  # admitted earlier
    for qid, key in ((first, "a"), (second, "b"), (third, "c")):
        db.execute(
            "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,finished_at) VALUES (%s,%s,'quality_review','succeeded','{}',%s,clock_timestamp())",
            (key, setup_ai, Jsonb({"question_id": qid})))
    report = progress.report(db, setup_ai)
    assert bucket(report, "biochemistry", "term")["admitted_today"] == 1 and report["totals"]["admitted_today"] == 1
    assert report["review_today"] == {"reviewed": 3, "passed": 2, "rate": round(2 / 3, 4)}
    assert progress.report(db, setup_ai) and True


def test_the_target_says_whether_it_is_the_first_batch_or_the_two_day_reserve(db, setup_ai, monkeypatch):
    monkeypatch.delenv("AI_BANK_TARGET_PER_BUCKET", raising=False)
    report = progress.report(db, setup_ai)
    assert report["target_kind"] == "two_day_reserve" and {b["target_kind"] for b in report["buckets"]} == {"two_day_reserve"}
    monkeypatch.setenv("AI_BANK_TARGET_PER_BUCKET", "5")
    report = progress.report(db, setup_ai)
    assert report["target_kind"] == "first_batch" and report["totals"]["target"] == 50


def test_no_reviews_today_means_no_rate_not_zero(db, setup_ai):
    assert progress.report(db, setup_ai)["review_today"] == {"reviewed": 0, "passed": 0, "rate": None}
