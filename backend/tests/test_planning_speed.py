"""The planning stage before the first call must not re-verify every stocked question on every 5-minute run."""
import pytest

from app import auto_generation as gen, auto_quality, auto_worker, source_policy
from test_ai_automation import setup_ai  # noqa: F401


@pytest.fixture
def stocked(db, bank, monkeypatch):
    calls = []
    for qid in bank:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready',%s)", (qid, f"sha{qid}"))

    def fake_ready(conn, qid):
        calls.append(qid)
        return True

    points = {r["t"]: int(r["p"]) for r in db.execute(
        "SELECT content->>'question_type' AS t, max(content->>'max_points') AS p FROM reviewed_questions GROUP BY 1")}
    monkeypatch.setattr(gen, "POINTS", points)                       # the fixture bank uses its own point values
    monkeypatch.setattr(auto_quality, "ready", fake_ready)
    monkeypatch.setattr(gen, "pinned_science_evidence", lambda content: True)
    return calls


def test_a_stocked_question_is_verified_once_and_then_remembered_across_runs(db, setup_ai, stocked):
    first = gen.bucket_detail(db, setup_ai)
    assert len(stocked) == len(set(stocked)) > 0                     # one verification per question, however many buckets or passes
    done = len(stocked)
    gen.bucket_detail(db, setup_ai)
    gen.satisfied_keys(db, setup_ai, cached=False)
    gen.gap_diagnosis(db, setup_ai)
    assert len(stocked) == done                                      # a second pass, even in a new process, verifies nothing
    assert sum(d["avail"] for d in first) > 0


def test_the_memory_follows_the_content_and_the_state(db, setup_ai, stocked):
    gen.bucket_detail(db, setup_ai)
    done = len(stocked)
    victim = db.execute("SELECT question_id FROM question_ready_cache LIMIT 1").fetchone()["question_id"]
    db.execute("UPDATE auto_question_states SET content_sha256='changed' WHERE question_id=%s", (victim,))   # content changed: verify again
    gen.bucket_detail(db, setup_ai)
    assert stocked[done:] == [victim]
    done = len(stocked)
    db.execute("UPDATE auto_question_states SET status='blocked' WHERE question_id=%s", (victim,))          # no longer ready: no call, not counted
    before = sum(d["avail"] for d in gen.bucket_detail(db, setup_ai))
    assert len(stocked) == done
    db.execute("UPDATE auto_question_states SET status='ready' WHERE question_id=%s", (victim,))
    assert sum(d["avail"] for d in gen.bucket_detail(db, setup_ai)) == before + 1


def test_a_not_ready_verdict_is_looked_at_again_after_ten_minutes_but_a_ready_one_much_later(db, setup_ai, monkeypatch, stocked):
    verdict = {"v": False}
    monkeypatch.setattr(auto_quality, "ready", lambda conn, qid: (stocked.append(qid), verdict["v"])[1])
    gen.bucket_detail(db, setup_ai)
    assert sum(d["avail"] for d in gen.bucket_detail(db, setup_ai)) == 0
    done = len(stocked)
    db.execute("UPDATE question_ready_cache SET checked_at=clock_timestamp()-interval '11 minutes'")
    verdict["v"] = True
    assert sum(d["avail"] for d in gen.bucket_detail(db, setup_ai)) > 0             # a review that passed in the meantime is seen
    assert len(stocked) > done
    done = len(stocked)
    db.execute("UPDATE question_ready_cache SET checked_at=clock_timestamp()-interval '2 hours'")
    gen.bucket_detail(db, setup_ai)
    assert len(stocked) == done                                                      # ready answers are trusted for hours
    db.execute("UPDATE question_ready_cache SET checked_at=clock_timestamp()-interval '7 hours'")
    gen.bucket_detail(db, setup_ai)
    assert len(stocked) > done


def test_the_run_log_breaks_the_planning_stage_down(db, setup_ai, stocked, monkeypatch):
    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "1")
    report = {}
    auto_worker._plan_queue(db, report, [{"user_id": setup_ai, "stopped": False}], True, True)
    assert {"plan", "gap"} <= {p.rstrip("0123456789.") for p in report["plan_detail"].split(",")}
    assert {"retire", "checks", "legacy", "fill", "past"} <= {p.rstrip("0123456789.") for p in report["plan_steps"].split(",")}
