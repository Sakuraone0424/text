"""A bucket whose usable stock covers the two-day reserve is neither written nor reviewed; its jobs wait in the queue."""
import pytest

from app import ai_jobs, auto_generation as gen, past_exam_import as pei
from test_ai_automation import setup_ai  # noqa: F401
from test_gap_generation import corpus  # noqa: F401


@pytest.fixture
def stock(monkeypatch):
    """eligible_reserve_count with a settable stock per (subject, type)."""
    have = {}
    monkeypatch.setattr(gen, "SATISFIED_TTL_SECONDS", 0)
    monkeypatch.setattr(gen, "eligible_reserve_count", lambda conn, uid, subject, kind, cached=False: have.get((subject, kind), 0))
    return have


def job(db, uid, key, kind, subject, qtype):
    payload = {"subject": subject, "question_type": qtype} if kind == "generation" else {"question": {"subject": subject, "question_type": qtype}}
    return ai_jobs.enqueue(db, uid, key, kind, payload)


def done(db, j):
    db.execute("UPDATE ai_jobs SET state='succeeded',dispatched_at=clock_timestamp(),lease_token=NULL WHERE id=%s", (j["id"],))


def state(db, key):
    return db.execute("SELECT state FROM ai_jobs WHERE job_key=%s", (key,)).fetchone()["state"]


def test_the_threshold_is_the_two_day_reserve(db, setup_ai, stock):
    stock[("biochemistry", "term")] = 8       # target 4 x 2
    stock[("biochemistry", "short_answer")] = 3
    keys = gen.satisfied_keys(db, setup_ai)
    assert "biochemistry|term" in keys and "biochemistry|short_answer" not in keys
    stock[("biochemistry", "short_answer")] = 4
    assert "biochemistry|short_answer" in gen.satisfied_keys(db, setup_ai)


def test_writing_review_and_repair_of_a_stocked_bucket_wait_in_the_queue(db, setup_ai, stock):
    stock[("biochemistry", "term")] = 60
    job(db, setup_ai, "w-term", "generation", "biochemistry", "term")
    job(db, setup_ai, "r-term", "quality_review", "biochemistry", "term")
    job(db, setup_ai, "x-term", "repair", "biochemistry", "term")
    job(db, setup_ai, "w-single", "generation", "cell_biology", "single_choice")
    first = ai_jobs.claim(db)
    assert first["job_key"] == "w-single"                     # the bucket with a gap is served
    done(db, first)
    assert ai_jobs.claim(db) is None                          # the stocked bucket's three jobs are not claimed
    assert [state(db, k) for k in ("w-term", "r-term", "x-term")] == ["pending"] * 3     # waiting, not dropped or blocked
    stock[("biochemistry", "term")] = 3                       # a gap appears
    got = set()
    for _ in range(3):
        j = ai_jobs.claim(db)
        got.add(j["job_key"])
        done(db, j)
    assert got == {"w-term", "r-term", "x-term"}


def test_grading_is_never_held_back_by_a_full_bucket(db, setup_ai, stock):
    stock[("biochemistry", "term")] = 60
    ai_jobs.enqueue(db, setup_ai, "g", "grading", {"subject": "biochemistry", "question_type": "term"})
    assert ai_jobs.claim(db)["job_key"] == "g"


def test_the_switch_turns_it_off(db, setup_ai, stock, monkeypatch):
    stock[("biochemistry", "term")] = 60
    job(db, setup_ai, "w-term", "generation", "biochemistry", "term")
    monkeypatch.setenv("AI_SKIP_SATISFIED_BUCKETS", "0")
    assert ai_jobs.claim(db)["job_key"] == "w-term"


def test_past_exam_items_of_a_stocked_bucket_are_not_queued(db, corpus, setup_ai, monkeypatch):
    pei.import_items(db)
    subjects_before = {(r["subject"], r["question_type"]) for r in db.execute("SELECT subject,question_type FROM past_exam_items WHERE status='imported'")}
    assert subjects_before
    monkeypatch.setattr(gen, "satisfied_keys", lambda conn, uid, cached=True: [f"{s}|{t}" for s, t in subjects_before])
    assert pei.plan(db, setup_ai) == 0
    assert db.execute("SELECT count(*) AS n FROM past_exam_items WHERE status='imported'").fetchone()["n"] > 0   # they wait
    monkeypatch.setattr(gen, "satisfied_keys", lambda conn, uid, cached=True: [])
    assert pei.plan(db, setup_ai) > 0


def test_the_log_counts_the_jobs_on_their_way_per_bucket(db, setup_ai, stock):
    job(db, setup_ai, "a", "generation", "biochemistry", "term")
    job(db, setup_ai, "b", "quality_review", "biochemistry", "term")
    job(db, setup_ai, "c", "generation", "cell_biology", "essay")
    d = gen.gap_diagnosis(db, setup_ai)
    assert "bioc:term=2" in d["on_way"] and "cell:essay=1" in d["on_way"] and "bioc:essay=0" in d["on_way"]
