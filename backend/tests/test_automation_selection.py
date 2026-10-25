from collections import Counter
from datetime import timedelta
from app import automation
from app.selection import pick
from app.common import ensure_user


def test_daily_automation_disabled_without_explicit_configuration(db, monkeypatch):
    monkeypatch.delenv("QUIZ_DAILY_PREP_ENABLED", raising=False)
    assert automation.prepare_daily(db) == 0
    assert db.execute("SELECT COUNT(*) AS n FROM automation_jobs").fetchone()["n"] == 0


def test_daily_preparation_only_after_20_and_reports_shortage(db, monkeypatch):
    ensure_user(db, "daily")
    monkeypatch.setenv("QUIZ_DAILY_PREP_ENABLED", "1")
    # Keep the mocked schedule after the database's real retry timestamp. A
    # fixed calendar date becomes the past and falsely leaves a new job pending.
    from app.training_sessions import server_now

    actual = server_now(db)
    scheduled = actual.replace(hour=12, minute=0, second=0, microsecond=0)
    if scheduled <= actual:
        scheduled += timedelta(days=1)
    monkeypatch.setattr(
        automation, "server_now", lambda c: scheduled - timedelta(minutes=1)
    )
    assert automation.prepare_daily(db) == 0
    assert db.execute("SELECT COUNT(*) AS n FROM automation_jobs").fetchone()["n"] == 0
    monkeypatch.setattr(automation, "server_now", lambda c: scheduled)
    assert automation.prepare_daily(db) == 0
    row = db.execute("SELECT * FROM automation_jobs").fetchone()
    assert row["state"] == "failed" and row["paper_id"] is None and row["attempts"] == 1
    assert automation.prepare_daily(db) == 0
    assert db.execute("SELECT COUNT(*) AS n FROM papers_v2").fetchone()["n"] == 0


def test_daily_preparation_idempotent_and_does_not_start_clock(db, bank, monkeypatch):
    ensure_user(db, "daily")
    monkeypatch.setenv("QUIZ_DAILY_PREP_ENABLED", "1")
    # Use an actual future date for consistency with database retry timestamps.
    from app.training_sessions import server_now

    now = server_now(db)
    fixed = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if fixed < now:
        fixed += __import__("datetime").timedelta(days=1)
    monkeypatch.setattr(automation, "server_now", lambda c: fixed)
    assert automation.prepare_daily(db) == 1
    assert automation.prepare_daily(db) == 0
    assert (
        db.execute("SELECT state FROM automation_jobs").fetchone()["state"]
        == "prepared"
    )
    assert (
        db.execute("SELECT COUNT(*) AS n FROM training_sessions").fetchone()["n"] == 0
    )


def test_ranking_balances_chapters_and_uses_recorded_signals():
    from app.dynamic_model import Policy

    def row(i, chapter):
        return {
            "id": i,
            "content": {
                "subject": "biochemistry",
                "question_type": "single_choice",
                "evidence": [{"chapter": chapter}],
                "knowledge_tags": ["mapped"],
                "difficulty": "core",
            },
        }

    pool = [row(1, "A"), row(2, "A"), row(3, "B")]
    context = {
        "topics": {
            "biochemistry": {"mapped": {"priority": 0.5, "mastery": {"recent": []}}}
        },
        "exam_subjects": {"biochemistry": {"cognitive_counts": {}}},
    }
    decisions = []
    selected = pick(
        pool,
        2,
        Counter(),
        context,
        Policy(),
        "fixed",
        "biochemistry",
        "single_choice",
        decisions,
    )
    repeated = []
    again = pick(
        pool,
        2,
        Counter(),
        context,
        Policy(),
        "fixed",
        "biochemistry",
        "single_choice",
        repeated,
    )
    assert [r["id"] for r in selected] == [r["id"] for r in again]
    first = selected[0]["content"]["evidence"][0]["chapter"]
    remaining = {r["id"]: r["content"]["evidence"][0]["chapter"] for r in pool}
    assert all(r["draw_weight"] > 0 for d in decisions for r in d["candidates"])
    for candidate in decisions[1]["candidates"]:
        if remaining[candidate["question_id"]] == first:
            assert candidate["coverage_factor"] < 1
