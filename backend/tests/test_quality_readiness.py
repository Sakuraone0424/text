"""Synthetic rollback-only task provenance tests, no external API calls."""

from copy import deepcopy
import pytest
from app import auto_quality as quality
from app.quality_readiness import inspect
from test_ai_automation import setup_ai, candidate, synthetic_result  # noqa: F401


def test_readiness_is_read_only_and_science_is_answer_blind(db, candidate):  # noqa: F811
    before = {
        t: db.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
        for t in ("ai_jobs", "auto_quality_events", "auto_question_states")
    }
    result = inspect(db, candidate)
    assert result["within_enqueue_limit"] and not result["formal_ready"]
    assert "answer" not in result["materials"]["science"]["question"]
    assert "rubric" not in result["materials"]["science"]["question"]
    assert "answer" in result["materials"]["answer"]["question"]
    assert result["human_decision_replaces_rounds"] is False
    assert before == {
        t: db.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in before
    }


def test_fabricated_in_memory_round_cannot_override_stored_job(db, setup_ai, candidate):  # noqa: F811
    job = quality.schedule_checks(db, setup_ai, candidate)[0]
    result = synthetic_result(job, db)
    forged = deepcopy(result)
    forged["result"]["response"]["passed"] = False
    with pytest.raises(ValueError, match="stored_review_job"):
        quality.accept_round(db, forged)
    assert not quality.ready(db, candidate)


def test_round_role_and_independent_job_identity_are_required(db, setup_ai, candidate):  # noqa: F811
    planned = quality.schedule_checks(db, setup_ai, candidate)
    for job in planned:
        assert quality.accept_round(db, synthetic_result(job, db))
    assert quality.promote(db, candidate)
    db.execute(
        "UPDATE ai_jobs SET kind='science_review' WHERE id=%s", (planned[1]["id"],)
    )
    assert not quality.ready(db, candidate)
    current = db.execute(
        "SELECT * FROM ai_jobs WHERE id=%s", (planned[1]["id"],)
    ).fetchone()
    with pytest.raises(ValueError, match="phase_mismatch"):
        quality.accept_round(db, current)
