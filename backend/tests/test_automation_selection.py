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


def test_daily_preparation_idempotent_and_does_not_start_clock(
    db, question_factory, passing_audit, monkeypatch
):
    from app import auto_quality, source_policy, quiz_v2 as q
    from app.question_quality import Question
    from spec_helpers import rubric
    from app.training_spec_v5 import COUNTS, POINTS, OBJECTIVE, SUBJECTIVE_PAPER

    # Synthetic 60q bank; review predicates are stubbed for this mechanics test.
    bank_ids = []
    for subject in ("biochemistry", "cell_biology"):
        for kind, count in COUNTS.items():
            for _ in range(count):
                raw = question_factory(subject, kind).model_dump()
                raw["max_points"] = POINTS[kind]
                raw["rubric"] = rubric(kind, POINTS[kind])
                qid = q.create_question(db, Question.model_validate(raw))["id"]
                assert q.review_question(db, qid, passing_audit)["passed"]
                q.publish_question(db, qid)
                db.execute(
                    "INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready',%s)",
                    (qid, "synthetic-only-test"),
                )
                bank_ids.append(qid)
    monkeypatch.setattr(auto_quality, "ready", lambda c, qid: qid in bank_ids)
    monkeypatch.setattr(source_policy, "pinned_science_evidence", lambda content: True)

    ensure_user(db, "daily")
    monkeypatch.setenv("QUIZ_DAILY_PREP_ENABLED", "1")
    monkeypatch.delenv("QUIZ_DAILY_SPECIFICATION", raising=False)
    # Use an actual future date for consistency with database retry timestamps.
    from app.training_sessions import server_now

    now = server_now(db)
    fixed = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if fixed < now:
        fixed += __import__("datetime").timedelta(days=1)
    monkeypatch.setattr(automation, "server_now", lambda c: fixed)
    assert automation.prepare_daily(db) == 2          # the objective and the subjective paper
    assert automation.prepare_daily(db) == 0
    assert {r["state"] for r in db.execute("SELECT state FROM automation_jobs").fetchall()} == {"prepared"}
    assert (
        db.execute("SELECT COUNT(*) AS n FROM training_sessions").fetchone()["n"] == 0
    )
    for module in (OBJECTIVE, SUBJECTIVE_PAPER):
        paper = db.execute("SELECT id,blueprint FROM papers_v2 WHERE blueprint->>'specification'=%s", (module.SPECIFICATION,)).fetchone()
        expected = module.default_blueprint()
        assert paper["blueprint"]["quotas"] == expected["quotas"]
        items = [r["snapshot"] for r in db.execute("SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s", (paper["id"],)).fetchall()]
        module.validate_items(items)
        assert len(items) == module.TOTAL_QUESTIONS and sum(i["max_points"] for i in items) == 150


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
