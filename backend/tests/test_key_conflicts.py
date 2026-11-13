"""A job key used earlier for a request written under other rules never stops a planning pass."""
import json

import pytest

from app import ai_jobs, auto_worker, past_exam_import as pei
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_past_exam_import import corpus, items  # noqa: F401  (fixtures/helpers)

ROUTE_KEYS = (
    ("past-exam:v5:1:abcd", "generation", "past_exam_generation"),
    ("gap:v4:biochemistry:fill_blank:7:abcd", "generation", "ai_new_question"),
    ("repair:12", "repair", "repair"),
    ("compare:3:1:claude", "generation", "comparison"),
    ("quality:5:sha:review:c10", "quality_review", "quality_review"),
)


@pytest.mark.parametrize("key,kind,route", ROUTE_KEYS)
def test_a_conflicting_key_gets_its_own_key_on_every_planning_route(db, setup_ai, key, kind, route):
    old = {"max_points": 4, "instructions": "old rules", "evidence": [1]}
    new = {"max_points": 2.5, "instructions": "new rules", "evidence": [1]}
    first = ai_jobs.enqueue(db, setup_ai, key, kind, old)
    with pytest.raises(ai_jobs.IdempotencyConflict) as raised:
        ai_jobs.enqueue(db, setup_ai, key, kind, new)
    detail = raised.value.detail
    assert detail["key"] == key and detail["kind"] == kind and detail["route"] == route      # the log can say what conflicted
    assert detail["differs"] == ["instructions", "max_points"]                                # field names only
    ai_jobs.RESOLVED_CONFLICTS.clear()
    second = ai_jobs.enqueue_rekeyed(db, setup_ai, key, kind, new)
    assert second["id"] != first["id"] and second["job_key"].startswith(key + ":") and second["payload"] == new
    assert ai_jobs.enqueue_rekeyed(db, setup_ai, key, kind, new)["id"] == second["id"]       # asking again reuses it
    assert ai_jobs.enqueue_rekeyed(db, setup_ai, key, kind, old)["id"] == first["id"]        # an identical request is untouched
    assert ai_jobs.RESOLVED_CONFLICTS and ai_jobs.RESOLVED_CONFLICTS[0]["route"] == route
    ai_jobs.RESOLVED_CONFLICTS.clear()


def test_a_database_full_of_old_requests_still_plans_new_work(db, corpus, setup_ai):
    """Production-like state: past-exam, AI new-question and repair requests from earlier rules sit under the keys the planner will use."""
    pei.import_items(db)
    pei.plan(db, setup_ai)
    report = {}
    auto_worker.plan(db, setup_ai, target=6, report=report)
    # every earlier request is now one written under other rules: v4 points, other instructions, a schema that has since changed
    db.execute(
        "UPDATE ai_jobs SET payload=jsonb_set(jsonb_set(payload,'{max_points}','6'::jsonb),'{instructions}','\"old rules\"'::jsonb),"
        "state='blocked',error_code='superseded_scoring'")
    db.execute("UPDATE past_exam_items SET status='imported',job_id=NULL WHERE status='queued'")
    ai_jobs.enqueue(db, setup_ai, "repair:99999", "repair", {"question_id": 99999, "schema": {"old": True}})
    ai_jobs.RESOLVED_CONFLICTS.clear()
    report = {}
    auto_worker.plan(db, setup_ai, target=6, report=report)
    assert report.get("plan_steps")
    fresh = db.execute("SELECT count(*) AS n FROM ai_jobs WHERE state IN ('pending','retrying')").fetchone()["n"]
    assert fresh > 0


def test_plan_error_names_the_route_and_other_routes_still_run(db, monkeypatch, setup_ai):
    ran = []
    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "1")
    monkeypatch.setenv("AI_FULL_CORPUS_ENABLED", "1")

    def conflict(conn, uid, *a, **k):
        raise ai_jobs.IdempotencyConflict({"key": "supplement:x", "kind": "generation", "route": "ai_new_question", "differs": ["max_points"]})

    monkeypatch.setattr(auto_worker.auto_knowledge, "plan_small_batch", conflict)
    monkeypatch.setattr(auto_worker.auto_generation, "plan", lambda conn, uid: ran.append("generation"))
    monkeypatch.setattr(auto_worker.auto_exams, "plan", lambda conn, uid: ran.append("exams"))
    monkeypatch.setattr(auto_worker.auto_insights, "plan", lambda conn, uid: ran.append("insights"))
    monkeypatch.setattr(auto_worker, "plan", lambda conn, uid, **k: ran.append("plan"))
    report = {}
    user = {"user_id": setup_ai, "stopped": False}
    auto_worker._plan_queue(db, report, [user], True, False)
    assert ran == ["plan", "generation", "exams", "insights"]                              # the failing route did not stop the rest
    assert report["plan_error"].startswith("knowledge=IdempotencyConflict:idempotency_conflict:")
    assert '"route":"ai_new_question"' in report["plan_error"] and '"differs":["max_points"]' in report["plan_error"]
    assert json.dumps(report)                                                                # the run's log line can carry it


def test_the_same_request_is_never_a_conflict_whatever_python_types_it_is_built_from(db, setup_ai):
    first = ai_jobs.enqueue(db, setup_ai, "gap:v4:x", "generation", {"ids": [1, 2], "points": 2.5, "nested": {"a": [3]}})
    again = ai_jobs.enqueue(db, setup_ai, "gap:v4:x", "generation", {"nested": {"a": (3,)}, "points": 2.5, "ids": (1, 2)})
    assert again["id"] == first["id"]
