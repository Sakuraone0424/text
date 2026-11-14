"""A unique-key collision never stops a run: where it happened is logged, the other tasks go on; a task whose retry budget is renewed is
claimed under a new attempt number; a repair is not requested twice; a rekeyed request never collides."""
import psycopg
import pytest
from psycopg.types.json import Jsonb

from app import ai_jobs, auto_quality, auto_worker, grading_recovery
from test_ai_automation import setup_ai  # noqa: F401  (fixture)


def job(db, uid, key, kind="grading", state="retrying", payload=None, error=None, attempts=0):
    return db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,error_code,attempts) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (key, uid, kind, state, Jsonb({}), Jsonb(payload or {}), error, attempts)).fetchone()


# ---- the root cause seen in production: a renewed task collided with the reservations of its earlier attempts ------------------------

def test_a_task_put_right_after_failing_on_the_evidence_cap_keeps_its_attempts_and_is_claimed_without_a_collision(db, setup_ai):
    s = ai_jobs.enqueue(db, setup_ai, "g-2722", "grading", {"instructions": "x", "evidence": [dict(unit_id=i % 3, text=f"段落{i}") for i in range(8)]})
    for attempt in (1, 2):                               # two earlier claims, each reserved its own attempt
        claimed = ai_jobs.claim(db)
        assert claimed["id"] == s["id"] and claimed["attempts"] == attempt
        with pytest.raises(ValueError):                  # (the execution guard refused it: more than four entries)
            auto_guard = __import__("app.auto_execution", fromlist=["x"]).bounded_material(dict(claimed["payload"], evidence=[dict(unit_id=i, text="x") for i in range(8)]))
        ai_jobs.finish(db, claimed, error=ValueError("evidence_exceeds_hard_cap"))
        db.execute("UPDATE ai_jobs SET next_retry=clock_timestamp() WHERE id=%s", (s["id"],))
    db.execute("UPDATE ai_jobs SET state='retrying',next_retry=clock_timestamp(),error_code='ValueError:evidence_exceeds_hard_cap' WHERE id=%s", (s["id"],))
    assert grading_recovery.shrink_oversized_requests(db, setup_ai) == 1
    row = db.execute("SELECT attempts,transient_attempts FROM ai_jobs WHERE id=%s", (s["id"],)).fetchone()
    assert row["attempts"] == 2 and row["transient_attempts"] == 2          # the history stays; the budget is renewed
    third = ai_jobs.claim(db)                                               # used to raise UniqueViolation on ai_cny_reservations(job_id, attempt)
    assert third["id"] == s["id"] and third["attempts"] == 3


def test_even_a_task_whose_counter_was_reset_is_numbered_after_its_last_reservation(db, setup_ai):
    s = ai_jobs.enqueue(db, setup_ai, "g-reset", "grading", {"instructions": "x"})
    first = ai_jobs.claim(db)
    ai_jobs.finish(db, first, error=ValueError("evidence_exceeds_hard_cap"))
    db.execute("UPDATE ai_jobs SET state='retrying',next_retry=clock_timestamp(),attempts=0,transient_attempts=0 WHERE id=%s", (s["id"],))   # (what #55 did)
    again = ai_jobs.claim(db)
    assert again["id"] == s["id"] and again["attempts"] == 2


# ---- a database collision is contained and described ----------------------------------------------------------------------------

def test_the_description_names_the_table_the_constraint_and_the_value(db, setup_ai):
    j = ai_jobs.enqueue(db, setup_ai, "dup-res", "grading", {"instructions": "x"})
    db.execute("INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot) VALUES (%s,1,0,'{}')", (j["id"],))
    with pytest.raises(psycopg.errors.UniqueViolation) as raised:
        with db.transaction():
            db.execute("INSERT INTO ai_cny_reservations(job_id,attempt,amount_cny,price_snapshot) VALUES (%s,1,0,'{}')", (j["id"],))
    text = auto_worker.describe_db_error(raised.value)
    assert text.startswith("UniqueViolation:table=ai_cny_reservations:constraint=ai_cny_reservations_job_id_attempt_key:detail=Key_(job_id,_attempt)=(")


def test_one_result_that_collides_with_an_existing_row_blocks_only_that_task_and_the_others_are_dispatched(db, setup_ai, monkeypatch):
    bad = job(db, setup_ai, "bad", "repair", "succeeded", {"question_id": 5}, attempts=1)
    good = job(db, setup_ai, "good", "grading", "succeeded", {"question_id": 6}, attempts=1)
    db.execute("UPDATE ai_jobs SET result=%s WHERE id=ANY(%s)", (Jsonb({"real_api": True, "response": {"question": {}, "reason": "x" * 30}}), [bad["id"], good["id"]]))

    def collide(conn, qid, question, reason):
        conn.execute("INSERT INTO users(username) VALUES ('dup-user')")
        conn.execute("INSERT INTO users(username) VALUES ('dup-user')")          # UniqueViolation: users_username_key

    seen = []
    monkeypatch.setattr(auto_quality, "repair", collide)
    monkeypatch.setattr(auto_worker.auto_grading, "apply", lambda conn, j: seen.append(j["id"]))
    auto_worker.DB_ERRORS.clear()
    auto_worker.dispatch_completed(db)                                              # no exception: the run goes on
    assert seen == [good["id"]]                                                     # the other task was dispatched
    row = db.execute("SELECT state,error_code,result,dispatched_at FROM ai_jobs WHERE id=%s", (bad["id"],)).fetchone()
    assert row["state"] == "blocked" and row["error_code"] == "database_conflict" and row["dispatched_at"] is not None
    assert "users_username_key" in row["result"]["dispatch_failure"]["detail"] and "dup-user" in row["result"]["dispatch_failure"]["detail"]
    assert any(e.startswith(f"dispatch:repair#{bad['id']}:UniqueViolation") for e in auto_worker.DB_ERRORS)
    assert db.execute("SELECT count(*) AS n FROM users WHERE username='dup-user'").fetchone()["n"] == 0      # its own savepoint was rolled back
    auto_worker.DB_ERRORS.clear()


def test_a_unique_violation_in_one_planning_route_is_logged_with_the_route_and_the_other_routes_still_run(db, setup_ai, monkeypatch):
    ran = []

    def collide(conn, uid, *a, **k):
        conn.execute("INSERT INTO users(username) VALUES ('dup-plan')")
        conn.execute("INSERT INTO users(username) VALUES ('dup-plan')")

    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "1")
    monkeypatch.setenv("AI_FULL_CORPUS_ENABLED", "1")
    monkeypatch.setattr(auto_worker.auto_knowledge, "plan_small_batch", collide)
    monkeypatch.setattr(auto_worker.auto_generation, "plan", lambda conn, uid: ran.append("generation"))
    monkeypatch.setattr(auto_worker.auto_exams, "plan", lambda conn, uid: ran.append("exams"))
    monkeypatch.setattr(auto_worker.auto_insights, "plan", lambda conn, uid: ran.append("insights"))
    monkeypatch.setattr(auto_worker, "plan", lambda conn, uid, **k: ran.append("plan"))
    report = {}
    auto_worker._plan_queue(db, report, [{"user_id": setup_ai, "stopped": False}], True, False)
    assert ran == ["plan", "generation", "exams", "insights"]
    assert report["plan_error"].startswith("knowledge=UniqueViolation:table=users:constraint=users_username_key:detail=Key_(username)=(dup-plan)")


def test_an_error_outside_every_route_is_contained_too(db, setup_ai, monkeypatch):
    def boom(conn, report):
        conn.execute("INSERT INTO users(username) VALUES ('dup-outer')")
        conn.execute("INSERT INTO users(username) VALUES ('dup-outer')")

    monkeypatch.setattr(auto_worker, "rescale_scoring", boom)
    report = {}
    auto_worker._plan_queue(db, report, [{"user_id": setup_ai, "stopped": False}], True, False)       # does not raise
    assert report["plan_error"].startswith("queue=UniqueViolation:table=users")
    assert db.execute("SELECT 1").fetchone()                                                           # and the connection is still usable


# ---- keys and repairs ---------------------------------------------------------------------------------------------------------

def test_a_rekeyed_request_whose_new_key_is_already_taken_takes_the_next_suffix(db, setup_ai):
    old = {"max_points": 4, "evidence": [1]}
    new = {"max_points": 2.5, "evidence": [1]}
    ai_jobs.enqueue(db, setup_ai, "repair:75", "repair", old)
    fingerprint = ai_jobs.hashlib.sha256(ai_jobs.json.dumps(new, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
    ai_jobs.enqueue(db, setup_ai, f"repair:75:{fingerprint}", "repair", {"something": "else"})      # the rekeyed key is taken by other content
    got = ai_jobs.enqueue_rekeyed(db, setup_ai, "repair:75", "repair", new)
    assert got["job_key"] == f"repair:75:{fingerprint}:2" and got["payload"] == new
    assert ai_jobs.enqueue_rekeyed(db, setup_ai, "repair:75", "repair", new)["id"] == got["id"]      # asking again finds it


def test_a_duplicate_repair_key_does_not_create_a_second_repair_of_the_same_question(db, setup_ai):
    from stats_helpers import question

    qid = question(db, "biochemistry", "term", 5, "第一章", stem="待修复的题")
    db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256,reason,revision_count) VALUES (%s,'blocked','x','rubric_not_fully_evidenced',0)", (qid,))
    job(db, setup_ai, f"repair:{qid}", "repair", "pending", {"question_id": qid, "schema": {"old": 1}})
    auto_worker.plan(db, setup_ai, target=0)                   # (the repair loop runs inside the planning pass)
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='repair'").fetchone()["n"] == 1


def test_the_stale_check_stops_when_its_time_budget_is_used_and_resumes_next_run(db, monkeypatch):
    from stats_helpers import question

    ids = [question(db, "biochemistry", "term", 5, "第一章", stem=f"题{n}") for n in range(4)]
    for qid in ids:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    seen = []
    monkeypatch.setattr(auto_quality, "evidence", lambda conn, qid: seen.append(qid))
    monkeypatch.setattr(auto_quality, "STALE_BUDGET_SECONDS", -1)          # no time at all: nothing is checked, nothing breaks
    auto_quality.retire_stale(db)
    assert seen == []
    monkeypatch.setattr(auto_quality, "STALE_BUDGET_SECONDS", 60)
    auto_quality.retire_stale(db)
    assert sorted(seen) == sorted(ids)
