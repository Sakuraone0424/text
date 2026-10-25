from collections import Counter
from datetime import datetime, timezone
from app import automation
from app.selection import pick
from app.quiz_service import ensure_user


def test_daily_automation_disabled_without_explicit_configuration(db, monkeypatch):
    monkeypatch.delenv("QUIZ_DAILY_PREP_ENABLED", raising=False)
    assert automation.prepare_daily(db) == 0
    assert db.execute("SELECT COUNT(*) AS n FROM automation_jobs").fetchone()["n"] == 0


def test_daily_preparation_only_after_20_and_reports_shortage(db, monkeypatch):
    ensure_user(db, "daily")
    monkeypatch.setenv("QUIZ_DAILY_PREP_ENABLED", "1")
    monkeypatch.setattr(
        automation,
        "server_now",
        lambda c: datetime(2026, 10, 25, 11, 59, tzinfo=timezone.utc),
    )
    assert automation.prepare_daily(db) == 0
    monkeypatch.setattr(
        automation,
        "server_now",
        lambda c: datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc),
    )
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
    def row(i, chapter):
        return {
            "id": i,
            "content": {
                "subject": "biochemistry",
                "evidence": [{"chapter": chapter}],
                "difficulty": "core",
                "knowledge_tags": ["Km"],
            },
        }

    pool = [row(1, "A"), row(2, "A"), row(3, "B")]
    selected = pick(pool, 2, Counter(), {}, Counter({("biochemistry", "Km"): 3}), set())
    assert [r["id"] for r in selected] == [1, 3]
    selected = pick(
        pool, 1, Counter(), {2: {"answered": 3, "wrong": 3}}, Counter(), {2}
    )
    assert selected[0]["id"] == 2
