"""The gap is computed and logged every run (peak or not), per bucket, and a pile of stuck work never hides it."""
import pytest

from app import auto_generation as gen, auto_worker, cost_control
from test_gap_generation import corpus  # noqa: F401
from test_ai_automation import setup_ai  # noqa: F401


def test_detail_has_a_row_per_bucket_with_the_numbers_its_gap_is_made_of(db, corpus, setup_ai):
    detail = gen.bucket_detail(db, setup_ai)
    assert len(detail) == 8
    row = detail[0]
    assert {"subject", "kind", "target", "avail", "pipeline", "inflight", "past", "gap"} <= set(row)
    assert row["gap"] == row["target"] - row["avail"] - row["pipeline"] - row["inflight"] - row["past"]


def test_diagnosis_lists_avail_and_gap_for_every_bucket(db, corpus, setup_ai):
    d = gen.gap_diagnosis(db, setup_ai)
    assert d["gap"] > 0 and d["buckets"] > 0
    assert d["avail"].count("=") == 8 and d["gap_detail"].count("=") == 8
    assert "biochemistry"[:4] + ":fill_blank=" in d["avail"]


def test_a_finished_request_nobody_dispatched_for_hours_is_not_supply(db, corpus, setup_ai):
    before = gen.gap_diagnosis(db, setup_ai)["gap"]
    job = gen.plan_gap(db, setup_ai)
    assert gen.gap_diagnosis(db, setup_ai)["gap"] == before - 1       # on its way: deducted
    db.execute("UPDATE ai_jobs SET state='succeeded', created_at=clock_timestamp()-interval '30 hours', finished_at=clock_timestamp()-interval '29 hours' WHERE id=%s", (job["id"],))
    assert gen.gap_diagnosis(db, setup_ai)["gap"] == before          # stuck: no longer hides the gap


def test_imported_past_exam_items_never_cover_more_than_half_the_target(db, corpus, setup_ai):
    for d in gen.bucket_detail(db, setup_ai):
        assert d["past"] <= d["target"] // 2


@pytest.mark.parametrize("peak,expected", [(True, "高峰暂停，低峰时将规划"), (False, None)])
def test_the_run_log_says_what_will_be_planned_when_peak_holds_the_calls(db, corpus, setup_ai, monkeypatch, peak, expected):
    monkeypatch.setattr(cost_control, "is_peak", lambda *a, **k: peak)
    report = {}
    users = [{"user_id": setup_ai, "stopped": False}]
    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "0")   # planning off: the diagnosis still runs
    monkeypatch.setattr(auto_worker, "_plan_queue", auto_worker._plan_queue)
    # the diagnosis block only runs when autoplan is on; turn it on but stop the planner from writing
    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "1")
    monkeypatch.setattr(auto_worker, "plan", lambda *a, **k: None)
    auto_worker._plan_queue(db, report, users, True, False)
    assert report["gap"] > 0 and report["avail"] and report["gap_detail"]
    if expected:
        assert report["plan_note"].startswith(expected) and report["plan_note"].endswith("个")
    else:
        assert report.get("plan_note") == "gap_but_no_request_could_be_written"


def test_a_missing_price_makes_jobs_wait_not_blocks_them(db, setup_ai, monkeypatch):
    from app import ai_jobs, cny_billing
    ai_jobs.enqueue(db, setup_ai, "waits", "science_review", {})
    monkeypatch.setattr(cny_billing, "current_price", lambda *a, **k: (_ for _ in ()).throw(ValueError("verified_cny_price_required")))
    assert ai_jobs.claim(db) is None
    row = db.execute("SELECT state,error_code FROM ai_jobs WHERE job_key='waits'").fetchone()
    assert row["state"] == "pending" and row["error_code"] is None


def test_jobs_blocked_earlier_for_a_missing_price_go_back_to_the_queue(db, setup_ai):
    from app import ai_jobs
    job = ai_jobs.enqueue(db, setup_ai, "old", "science_review", {})
    db.execute("UPDATE ai_jobs SET state='blocked',error_code='verified_cny_price_required' WHERE id=%s", (job["id"],))
    report = {}
    auto_worker._plan_queue(db, report, [{"user_id": setup_ai, "stopped": False}], False, False)
    assert db.execute("SELECT state FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()["state"] == "retrying"


def test_the_funnel_says_where_a_short_bucket_gets_stuck(db, corpus, setup_ai):
    d = gen.bucket_funnel(db, "biochemistry", "essay")
    assert set(d) == {"jobs", "failed_jobs", "states", "top_reason", "past"}
    assert gen.why_nothing_is_written(db, "biochemistry", "single_choice") in ("writable", "no_exam_style", "no_textbook_paragraph")
    line = gen.short_bucket_report(db, setup_ai)
    assert "bioc:essay=" in line and "|jobs" in line and "|q:" in line and "|top:" in line and "|past:" in line


def test_a_bucket_without_any_exam_style_is_named(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setattr(gen, "select_exam_style", lambda *a, **k: None)
    assert gen.why_nothing_is_written(db, "biochemistry", "essay") == "no_exam_style"


def test_a_small_gap_is_not_starved_by_big_ones(db, corpus, setup_ai):
    from app import ai_jobs
    # 20 recent requests, none for biochemistry essay, which has a gap of 1 among gaps of 40+
    for i in range(20):
        db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload) VALUES (%s,%s,'generation','{}',%s)",
                   (f"old-{i}", setup_ai, __import__("psycopg").types.json.Jsonb({"subject": "biochemistry", "question_type": "fill_blank"})))
    gaps = [("biochemistry", "fill_blank", 40), ("biochemistry", "single_choice", 30), ("biochemistry", "essay", 1)]
    first = gen.allocate_order(db, gaps)[0]
    assert first[:2] == ("biochemistry", "essay") or first[:2] == ("biochemistry", "single_choice")   # single-choice floor may go first
    assert gen.allocate_order(db, [("biochemistry", "fill_blank", 40), ("biochemistry", "essay", 1)])[0][1] == "essay"
