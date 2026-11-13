"""The planning pass after the point-scale change: no idempotency conflict, and the per-run housekeeping stays cheap."""
import pytest
from psycopg.types.json import Jsonb

from app import auto_quality, auto_worker, past_exam_import as pei, scoring_rescale
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_past_exam_import import corpus, items  # noqa: F401  (fixtures/helpers)


def test_a_past_exam_request_written_under_other_rules_never_stops_the_planning_pass(db, corpus, setup_ai):
    pei.import_items(db)
    assert pei.plan(db, setup_ai) >= 1
    jobs = db.execute("SELECT id,job_key FROM ai_jobs WHERE payload->>'origin'='past_exam' ORDER BY id").fetchall()
    assert jobs and all(j["job_key"].startswith("past-exam:v5:") for j in jobs)       # the key version follows the point scale
    # the request of an earlier version of the rules (other points, other instructions) is still in the table under the same key
    db.execute("UPDATE ai_jobs SET payload=jsonb_set(payload,'{max_points}','6'::jsonb),state='blocked',error_code='superseded_scoring' "
               "WHERE payload->>'origin'='past_exam'")
    db.execute("UPDATE past_exam_items SET status='imported',job_id=NULL WHERE status='queued'")
    assert pei.plan(db, setup_ai) >= 1                                                  # used to raise ValueError: idempotency_conflict
    fresh = db.execute("SELECT job_key,payload->>'max_points' AS points,state FROM ai_jobs WHERE payload->>'origin'='past_exam' ORDER BY id").fetchall()
    assert len(fresh) > len(jobs)
    assert any(j["state"] in ("pending", "retrying") and j["points"] in ("3", "2", "5", "15", "25") for j in fresh)


def test_the_whole_planning_pass_runs_on_a_database_that_holds_old_past_exam_requests(db, corpus, setup_ai):
    pei.import_items(db)
    pei.plan(db, setup_ai)
    db.execute("UPDATE ai_jobs SET payload=jsonb_set(payload,'{max_points}','6'::jsonb),state='blocked',error_code='superseded_scoring' WHERE payload->>'origin'='past_exam'")
    db.execute("UPDATE past_exam_items SET status='imported',job_id=NULL WHERE status='queued'")
    report = {}
    auto_worker.plan(db, setup_ai, target=4, report=report)                            # no ValueError anywhere in the pass
    assert "past" in report["plan_steps"]


def test_the_rescale_pass_loads_only_questions_still_at_an_old_maximum(db, monkeypatch):
    from stats_helpers import question

    question(db, "biochemistry", "single_choice", 3, "第一章", stem="已经是新分值的题")         # already at the new scale
    seen = []
    real = scoring_rescale.rescale_content
    monkeypatch.setattr(scoring_rescale, "rescale_content", lambda content: seen.append(content) or real(content))
    assert scoring_rescale.run(db) == {"rescaled": 0}
    assert seen == []                                                                  # not even read: the SQL filter leaves it out
    old = question(db, "biochemistry", "single_choice", 4, "第一章", stem="旧分值的题")
    db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (old,))
    assert scoring_rescale.run(db)["rescaled"] == 1 and len(seen) == 1


def test_a_question_that_passed_is_not_re_verified_on_every_planning_tick(db, monkeypatch):
    from stats_helpers import question

    ids = [question(db, "biochemistry", "single_choice", 3, "第一章", stem=f"题{n}") for n in range(3)]
    for qid in ids:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready',%s)", (qid, "sha-1"))
    calls = []
    monkeypatch.setattr(auto_quality, "evidence", lambda conn, qid: calls.append(qid))
    auto_quality.retire_stale(db, now=1000.0)
    assert sorted(calls) == sorted(ids)                                               # the first pass checks every question
    auto_quality.retire_stale(db, now=1000.0 + 5 * 60)                                # the next tick, five minutes later: nothing
    assert len(calls) == 3
    db.execute("UPDATE auto_question_states SET content_sha256='sha-2' WHERE question_id=%s", (ids[0],))
    auto_quality.retire_stale(db, now=1000.0 + 6 * 60)                                # changed content is looked at again at once
    assert calls.count(ids[0]) == 2 and calls.count(ids[1]) == 1
    auto_quality.retire_stale(db, now=1000.0 + 45 * 60)                               # after the window every question is due again
    assert len(calls) == 3 + 1 + 3


def test_a_superseded_question_is_still_withdrawn_when_it_is_due(db, monkeypatch):
    from stats_helpers import question

    qid = question(db, "biochemistry", "single_choice", 3, "第一章", stem="将被取代的题")
    db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    monkeypatch.setattr(auto_quality, "evidence", lambda conn, q: None)
    auto_quality.retire_stale(db, now=0.0)

    def gone(conn, q):
        raise ValueError("superseded_candidate")

    monkeypatch.setattr(auto_quality, "evidence", gone)
    auto_quality.retire_stale(db, now=60.0)                                           # inside the window: not looked at
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["status"] == "ready"
    auto_quality.retire_stale(db, now=60.0 * 60)                                      # due: withdrawn
    row = db.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()
    assert (row["status"], row["reason"]) == ("retired", "source_or_candidate_superseded")
