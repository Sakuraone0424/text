"""Third-party review follow-ups: submit validation, idempotent submit, fair review scheduling, sign-in hardening,
business readiness, exact question versions."""
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from app import auth
from app import quiz_v2 as q
from app import training_sessions as sessions
from app.selection import pick
from test_api_v2 import LEARNER

CHOICE, BLANK = "single_choice", "fill_blank"


def one(paper, kind):
    return next(x for x in paper["questions"] if x["question_type"] == kind)


def counts(db, pid):
    return (
        db.execute("SELECT count(*) AS n FROM attempts_v2 WHERE paper_id=%s", (pid,)).fetchone()["n"],
        db.execute("SELECT submitted_at FROM training_sessions WHERE paper_id=%s", (pid,)).fetchone()["submitted_at"],
    )


# ---- 1. submit validation ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("answers", [
    {"999999": "A"},                                   # a question that is not on this paper
    {"CHOICE": "E"},                                   # an option that does not exist
    {"CHOICE": "AB"},
    {"CHOICE": 5},                                     # not a string or object
    {"CHOICE": {"text": "A", "extra": 1}},             # unknown keys
    {"BLANK": {"text": "x", "blanks": ["a", "b", "c", "d"]}},
])
def test_invalid_submissions_are_4xx_and_change_nothing(client, running, db, answers):
    uid, paper = running
    pid = paper["paper_id"]
    choice = one(paper, CHOICE)["id"]
    blank = one(paper, BLANK)["id"]
    answers = {k.replace("CHOICE", str(choice)).replace("BLANK", str(blank)): v for k, v in answers.items()}
    before = counts(db, pid)
    r = client.post(f"/v2/papers/{pid}/submit", headers=LEARNER, json={"answers": answers, "expected_version": 0})
    assert 400 <= r.status_code < 500, r.text
    assert counts(db, pid) == before == (0, None)
    assert db.execute("SELECT count(*) AS n FROM answer_drafts WHERE paper_id=%s", (pid,)).fetchone()["n"] == 0
    # the paper is still open and a correct submission goes through
    ok = client.post(f"/v2/papers/{pid}/submit", headers=LEARNER, json={"answers": {str(choice): "A"}, "expected_version": 0})
    assert ok.status_code == 200 and ok.json()["answers"]


def test_a_non_numeric_question_key_is_rejected_before_anything_runs(client, running, db):
    _, paper = running
    r = client.post(f"/v2/papers/{paper['paper_id']}/submit", headers=LEARNER, json={"answers": {"abc": "A"}})
    assert r.status_code == 422 and counts(db, paper["paper_id"]) == (0, None)


# ---- 2. a lost response, drafts survive ----------------------------------------------------------------------------
def test_submit_twice_returns_the_same_result_and_the_result_page_reopens(client, running, db):
    uid, paper = running
    pid = paper["paper_id"]
    choice = one(paper, CHOICE)["id"]
    body = {"answers": {str(choice): "A"}, "expected_version": 0}
    first = client.post(f"/v2/papers/{pid}/submit", headers=LEARNER, json=body).json()
    again = client.post(f"/v2/papers/{pid}/submit", headers=LEARNER, json={"answers": {}, "expected_version": 7}).json()
    assert again["attempt_id"] == first["attempt_id"] and again["score"] == first["score"]
    reopened = client.get(f"/v2/papers/{pid}/result", headers=LEARNER).json()
    assert reopened["attempt_id"] == first["attempt_id"] and reopened["answers"] and reopened["answers"][0]["stem"]
    assert db.execute("SELECT count(*) AS n FROM attempts_v2").fetchone()["n"] == 1
    # grading and the review plan were written once
    wrong = db.execute("SELECT count(*) AS n FROM fsrs_cards").fetchone()["n"]
    client.post(f"/v2/papers/{pid}/submit", headers=LEARNER, json=body)
    assert db.execute("SELECT count(*) AS n FROM fsrs_cards").fetchone()["n"] == wrong


def test_saved_drafts_are_served_again_on_reload(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = one(paper, CHOICE)["id"]
    sessions.save(db, uid, pid, qid, {"text": "B"}, 0)
    again = q.load_paper(db, uid, pid)          # what a refresh / a new tab reads
    assert again["timing"]["drafts"][str(qid)]["text"] == "B" and again["timing"]["status"] == "in_progress"


# ---- 3. fair review scheduling -------------------------------------------------------------------------------------
def _row(i):
    return {"id": i, "content": {"evidence": [{"chapter": "c"}]}}


def test_due_questions_are_served_longest_waiting_first():
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    pool = [_row(i) for i in range(1, 8)]
    due = {1: now - timedelta(days=1), 2: now - timedelta(days=9), 3: now - timedelta(days=5), 4: now - timedelta(days=5)}
    chosen = pick(pool, 3, defaultdict(int), None, None, "seed", "biochemistry", CHOICE, [], due)
    assert [r["id"] for r in chosen] == [2, 3, 4]      # oldest due first, ties by id; not-due rows only after due ones


def test_everything_due_comes_round_within_a_fair_number_of_days():
    """150 questions fall due on day 0, the paper holds 10 of them, and every one is answered wrong every time (so it is
    due again the next day). All 150 must have been asked within ceil(150/10) = 15 days, none starved."""
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    due = {i: start + timedelta(minutes=i) for i in range(1, 151)}
    asked, per_day = {}, 10
    for day in range(1, 16):
        today = start + timedelta(days=day)
        pool = [_row(i) for i in due]
        chosen = pick(pool, per_day, defaultdict(int), None, None, "s", "biochemistry", CHOICE, [], dict(due))
        assert len(chosen) == per_day
        for r in chosen:
            asked.setdefault(r["id"], day)
            due[r["id"]] = today + timedelta(days=1)       # wrong again: due tomorrow, behind everything still waiting
    assert set(asked) == set(due) and max(asked.values()) <= 15


# ---- 5. non-ASCII secrets ------------------------------------------------------------------------------------------
def test_chinese_password_and_malformed_headers_never_500(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "中文密码😀")
    assert client.post("/auth/login", json={"password": "中文密码😀"}).status_code == 200
    assert client.post("/auth/login", json={"password": "中文密码"}).status_code == 401
    assert client.post("/auth/login", json={"password": "pässword"}).status_code == 401
    for headers in ({"x-api-key": "密钥".encode()}, {"authorization": "Bearer ".encode() + "密钥".encode()},
                    {"x-api-key": b"\xff\xfe\xfd"}, {"x-review-api-key": "审核".encode()}):
        r = client.get("/auth/me", headers=headers)
        assert r.status_code == 401, headers
    assert client.post("/v2/questions", headers={"x-review-api-key": "审核".encode()}, json={}).status_code in (403, 422)


def test_unit_compare_handles_non_ascii_and_lone_surrogates():
    assert auth.same_secret("中文", "中文") and not auth.same_secret("中文", "中") and not auth.same_secret("\udcff", "x")


# ---- 6. lock-out after repeated failures --------------------------------------------------------------------------
def test_ten_failures_lock_the_source_for_fifteen_minutes(client, db):
    for n in range(9):
        r = client.post("/auth/login", json={"password": f"wrong{n}"})
        assert r.status_code == 401 and "Invalid" in r.text
    tenth = client.post("/auth/login", json={"password": "wrong9"})
    assert tenth.status_code == 429 and "锁定" in tenth.json()["detail"] and int(tenth.headers["retry-after"]) > 800
    locked = client.post("/auth/login", json={"password": "login-test-password"})   # even the right one waits
    assert locked.status_code == 429
    other = client.post("/auth/login", json={"password": "login-test-password"}, headers={"x-forwarded-for": "203.0.113.9"})
    assert other.status_code == 200                                                   # another source is unaffected
    db.execute("UPDATE login_locks SET locked_until=clock_timestamp()-interval '1 second'")
    assert client.post("/auth/login", json={"password": "login-test-password"}).status_code == 200
    assert db.execute("SELECT count(*) AS n FROM login_failures").fetchone()["n"] == 0


def test_a_success_clears_earlier_failures(client, db):
    for _ in range(5):
        client.post("/auth/login", json={"password": "nope"})
    assert client.post("/auth/login", json={"password": "login-test-password"}).status_code == 200
    for _ in range(9):
        assert client.post("/auth/login", json={"password": "nope"}).status_code == 401


# ---- 7. session revocation ----------------------------------------------------------------------------------------
def _sign_in(client):
    r = client.post("/auth/login", json={"password": "login-test-password"})
    return r.cookies.get(auth.SESSION_COOKIE) or r.headers["set-cookie"].split(";")[0].split("=", 1)[1]


def _me(client, token):
    client.cookies.clear()
    client.cookies.set(auth.SESSION_COOKIE, token)
    return client.get("/auth/me").status_code


def test_logout_ends_this_session_only(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    first, second = _sign_in(client), _sign_in(client)
    assert _me(client, first) == 200 and _me(client, second) == 200
    client.cookies.clear()
    client.cookies.set(auth.SESSION_COOKIE, first)
    assert client.post("/auth/logout").status_code == 200
    assert _me(client, first) == 401 and _me(client, second) == 200


def test_changing_the_password_ends_every_old_session(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    old = _sign_in(client)
    assert _me(client, old) == 200
    monkeypatch.setenv("APP_PASSWORD", "a-new-password")
    assert _me(client, old) == 401
    assert client.post("/auth/login", json={"password": "a-new-password"}).status_code == 200


def test_tokens_in_the_old_format_are_refused(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "s")
    import base64, hashlib, hmac
    payload = f"owner:{int(time.time()) + 100}".encode()
    raw = payload + b"." + hmac.new(b"s", payload, hashlib.sha256).digest()
    assert auth.verify_session_token(base64.urlsafe_b64encode(raw).decode().rstrip("=")) is None


# ---- 8. business readiness ----------------------------------------------------------------------------------------
def test_db_health_says_what_the_bank_lacks_and_health_stays_green(client):
    assert client.get("/health").status_code == 200
    r = client.get("/db/health")
    assert r.status_code == 503
    gaps = r.json()["detail"]["missing"]
    assert r.json()["detail"]["code"] == "not_ready_for_training"
    assert {"kind": "questions", "subject": "cell_biology", "question_type": "essay", "have": 0, "need": 1} in gaps
    assert len(gaps) == 10


def test_db_health_is_green_once_every_bucket_has_a_papers_worth(client, db, bank, monkeypatch):
    from app import training_spec_v4 as spec
    monkeypatch.setattr(spec, "COUNTS", {"single_choice": 3, "fill_blank": 3, "term": 4, "short_answer": 3, "essay": 1})
    for qid in bank:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    r = client.get("/db/health")
    assert r.status_code == 200 and r.json()["database"] == "connected"


# ---- 9. the exact question version --------------------------------------------------------------------------------
def test_a_started_paper_is_graded_by_the_question_as_it_was_when_the_paper_was_made(client, running, db):
    uid, paper = running
    pid = paper["paper_id"]
    item = db.execute(
        "SELECT i.question_id,i.snapshot FROM paper_items_v2 i WHERE i.paper_id=%s AND i.snapshot->>'question_type'='single_choice' LIMIT 1",
        (pid,),
    ).fetchone()
    live = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (item["question_id"],)).fetchone()["content"]
    assert live == item["snapshot"]                           # the paper holds the whole published content
    changed = {**live, "answer": "B" if live["answer"] != "B" else "C"}
    db.execute("SAVEPOINT s")
    try:
        db.execute("UPDATE reviewed_questions SET content=%s WHERE id=%s", (json.dumps(changed), item["question_id"]))
        mutable = True
    except psycopg.Error:
        mutable = False
        db.execute("ROLLBACK TO SAVEPOINT s")
    db.execute("UPDATE reviewed_questions SET status='withdrawn' WHERE id=%s", (item["question_id"],)) if not mutable else None
    right = item["snapshot"]["answer"]
    result = client.post(
        f"/v2/papers/{pid}/submit", headers=LEARNER, json={"answers": {str(item["question_id"]): right}, "expected_version": 0}
    ).json()
    graded = next(a for a in result["answers"] if a["question_id"] == item["question_id"])
    assert graded["points"] == item["snapshot"]["max_points"]    # judged by the snapshot, whatever happened to the live row


def test_the_platform_health_check_never_depends_on_the_bank(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    assert client.get("/health").status_code == 200           # empty bank, schema complete
    assert client.get("/db/health?business=false").status_code == 503   # a query string cannot switch the check off
