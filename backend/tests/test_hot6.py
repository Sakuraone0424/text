"""Planning is not held back by tasks that will never be claimed; the log says what each route wanted and did; a rekeyed request is found again."""
import pytest
from psycopg.types.json import Jsonb

from app import ai_jobs, auto_generation as gen, auto_worker, past_exam_import as pei, progress
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_gap_generation import corpus  # noqa: F401  (fixture)


def add(db, uid, key, subject, kind, state="pending", origin=None):
    payload = {"subject": subject, "question_type": kind, "evidence": [], "max_points": {"term": 5, "fill_blank": 2.5}[kind]}
    if origin:
        payload["origin"] = origin
    return db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload) VALUES (%s,%s,'generation',%s,'{}',%s) RETURNING id",
        (key, uid, state, Jsonb(payload))).fetchone()["id"]


@pytest.fixture
def surplus_terms(db, setup_ai, monkeypatch):
    """Both term buckets are stocked above their reserve (so their waiting tasks are never claimed) and 21 term tasks wait."""
    keys = lambda conn, uid, cached=True: ["biochemistry|term", "cell_biology|term"]
    monkeypatch.setattr(gen, "satisfied_keys", keys)
    monkeypatch.setattr(progress, "satisfied_keys", keys)
    for n in range(21):
        add(db, setup_ai, f"surplus-{n}", "biochemistry" if n < 10 else "cell_biology", "term")
    return setup_ai


def test_tasks_of_stocked_buckets_do_not_count_as_work_on_the_way(db, surplus_terms):
    assert gen.claimable_count(db) == 21 and gen.claimable_count(db, surplus_terms) == 0
    total, per = gen.deferred_count(db, surplus_terms)
    assert total == 21 and per == {"biochemistry|term": 10, "cell_biology|term": 11}


def test_a_big_fill_in_gap_is_planned_to_the_target_although_surplus_tasks_wait(db, corpus, surplus_terms):
    queued = gen.fill_queue(db, surplus_terms, 6)                       # used to queue exactly 1: the 21 waiting term tasks counted as claimable
    assert queued >= 2
    assert gen.FILL_DETAIL["want"] == 6 and gen.FILL_DETAIL["queued"] == queued and gen.FILL_DETAIL["deferred"] == 21
    assert gen.FILL_DETAIL["stop"] in ("target_reached", "no_request_could_be_written", "no_gap") or gen.FILL_DETAIL["stop"].startswith("active_cap")
    fresh = db.execute("SELECT payload->>'question_type' AS t FROM ai_jobs WHERE job_key LIKE 'gap:%'").fetchall()
    assert len(fresh) == queued and {r["t"] for r in fresh} <= {"fill_blank", "term", "short_answer", "essay"} - {"term"}   # nothing for the stocked bucket


def test_the_past_exam_route_does_not_count_surplus_tasks_as_its_own_active_work(db, corpus, surplus_terms):
    for n in range(5):
        add(db, surplus_terms, f"past-surplus-{n}", "biochemistry", "term", origin="past_exam")
    pei.import_items(db)
    pei.plan(db, surplus_terms, batch=2)
    assert pei.PLAN_DETAIL["want"] == 2 and pei.PLAN_DETAIL["active"] == 0       # the five stocked-bucket tasks are not "active"
    assert "queued" in pei.PLAN_DETAIL


def test_the_log_says_what_each_route_wanted_did_and_was_stopped_by(db, corpus, surplus_terms, monkeypatch):
    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "1")
    report = {}
    auto_worker.plan(db, surplus_terms, target=6, report=report)
    line = report["plan_routes"]
    assert "gap:want=6/had=0/queued=" in line and "/stop=" in line and "|past:want=" in line and "/stocked=" in line
    assert report["deferred_surplus"] == 21


def test_a_stuck_gap_says_why_for_every_bucket(db, setup_ai):
    queued = gen.fill_queue(db, setup_ai, 6)                                # no corpus: nothing can be written
    assert queued == 0
    assert gen.FILL_DETAIL["stop"] in ("no_request_could_be_written", "no_gap") and gen.FILL_DETAIL["buckets"]
    assert "no_exam_style" in gen.FILL_DETAIL["buckets"] or "no_paragraph_pool" in gen.FILL_DETAIL["buckets"]


# ---- the progress page ---------------------------------------------------------------------------------------------------------

def test_waiting_tasks_of_a_stocked_bucket_are_shown_apart_not_as_queued(db, surplus_terms):
    report = progress.report(db, surplus_terms)
    term = next(b for b in report["buckets"] if b["subject"] == "biochemistry" and b["question_type"] == "term")
    assert term["queued"] == 0 and term["deferred"] == 10
    fill = next(b for b in report["buckets"] if b["subject"] == "biochemistry" and b["question_type"] == "fill_blank")
    assert fill["deferred"] == 0


# ---- a rekeyed request is kept, not re-resolved every pass ---------------------------------------------------------------------------

def test_the_key_conflict_of_a_request_happens_once(db, setup_ai):
    ai_jobs.enqueue(db, setup_ai, "repair:75", "repair", {"question_id": 75, "schema": {"old": True}})
    new = {"question_id": 75, "schema": {"new": True}}
    ai_jobs.RESOLVED_CONFLICTS.clear()
    first = ai_jobs.enqueue_rekeyed(db, setup_ai, "repair:75", "repair", new)
    assert len(ai_jobs.RESOLVED_CONFLICTS) == 1
    ai_jobs.RESOLVED_CONFLICTS.clear()
    for _ in range(3):                                                   # the next planning passes
        again = ai_jobs.enqueue_rekeyed(db, setup_ai, "repair:75", "repair", new)
        assert again["id"] == first["id"]
    assert ai_jobs.RESOLVED_CONFLICTS == []                               # no conflict met, none reported
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE job_key LIKE 'repair:75%%'").fetchone()["n"] == 2
