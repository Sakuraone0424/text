"""Claude Code scheduled tasks (Routines) as a second executor: the interface, the routing and the blind comparison. No model is called."""
from contextlib import contextmanager
from datetime import timedelta

import pytest

from app import ai_jobs, auto_generation as gen, auto_worker, comparison, external_api, external_executor as ext, progress
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_gap_generation import choice_response, corpus  # noqa: F401  (fixtures)

TOKEN = "external-token-for-tests-only"
HEADERS = {"X-External-Token": TOKEN}


@pytest.fixture
def api(db, client, monkeypatch):
    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    monkeypatch.setattr(external_api, "get_db", test_db)
    monkeypatch.setenv("EXTERNAL_EXECUTOR_TOKEN", TOKEN)
    monkeypatch.setenv("EXECUTOR_GENERATION", "claude_first")       # the default is DeepSeek until the owner has seen a Routine run; these tests are about Claude first
    monkeypatch.delenv("EXECUTOR_REVIEW", raising=False)
    monkeypatch.delenv("EXECUTOR_GRADING", raising=False)
    monkeypatch.setattr(gen, "pinned_science_evidence", lambda content: True)
    ext.reset_rate_limit()
    return client


@pytest.fixture(autouse=True)
def single_choice_requests(monkeypatch):
    """These tests drive the interface with single-choice requests (their answer format is the richest). Daily papers no longer ask for
    single choice, so the planner is told that the single-choice buckets are short."""
    monkeypatch.setattr(gen, "bucket_gaps", lambda conn, uid: [("biochemistry", "single_choice", 5), ("cell_biology", "single_choice", 5)])


def one_job(db, uid):
    job = gen.plan_gap(db, uid)
    assert job is not None
    return job


def lease_one(api, session="session-aaaaaa", kind="generation"):
    r = api.get(f"/v2/external/lease?kind={kind}&max=1&session={session}", headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()["leases"]


# ---- the interface's door ------------------------------------------------------------------------------------------------

def test_a_wrong_or_missing_token_is_401_and_an_unset_token_is_503(api, monkeypatch, caplog):
    url = "/v2/external/lease?kind=generation&max=1&session=session-aaaaaa"
    assert api.get(url).status_code == 401
    assert api.get(url, headers={"X-External-Token": "nope"}).status_code == 401
    assert api.get("/v2/external/status", headers={"X-External-Token": "nope"}).status_code == 401
    assert api.post("/v2/external/release", json={"lease_id": "x" * 10}, headers={"X-External-Token": "nope"}).status_code == 401
    # the login session is not a token
    assert api.get(url, headers={"Authorization": "Bearer learner-test-key", "X-Api-Key": "learner-test-key"}).status_code == 401
    monkeypatch.delenv("EXTERNAL_EXECUTOR_TOKEN")
    assert api.get(url, headers=HEADERS).status_code == 503
    assert TOKEN not in caplog.text and "nope" not in api.get(url, headers={"X-External-Token": "nope"}).text


def test_sixty_requests_a_minute_and_then_429(api):
    for _ in range(60):
        assert api.get("/v2/external/status", headers=HEADERS).status_code == 200
    assert api.get("/v2/external/status", headers=HEADERS).status_code == 429
    ext.reset_rate_limit()
    assert api.get("/v2/external/status", headers=HEADERS).status_code == 200


# ---- leasing -----------------------------------------------------------------------------------------------------------------

def test_a_lease_carries_exactly_what_deepseek_is_sent_and_the_deepseek_worker_leaves_it_alone(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    leases = lease_one(api)
    assert len(leases) == 1
    got = leases[0]
    assert got["payload"] == job["payload"]                                  # rules, paragraphs, style samples, schema, full score
    from app.harness_runner import build_prompt

    assert got["prompt"] == build_prompt(job["payload"])
    assert got["max_points"] == job["payload"]["max_points"] and got["kind"] == "generation"
    assert {"instructions", "schema", "evidence", "style"} <= set(got["payload"])
    # leased: nobody else is given it, and the DeepSeek worker does not claim it (default route: Claude first)
    assert lease_one(api, session="session-bbbbbb") == []
    assert ai_jobs.claim(db) is None


def test_deepseek_gets_it_after_the_fallback_hours_but_not_before(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    assert ai_jobs.claim(db) is None                               # 'claude first': DeepSeek waits
    db.execute("UPDATE ai_jobs SET created_at=clock_timestamp()-interval '5 hours' WHERE id=%s", (job["id"],))
    claimed = ai_jobs.claim(db)
    assert claimed and claimed["id"] == job["id"]


def test_the_route_decides_who_may_take_a_task(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    from test_api_v2 import LEARNER

    saved = api.put("/v2/model/executors", json={"generation": "deepseek", "fallback_hours": 6}, headers=LEARNER)
    assert saved.status_code == 200 and saved.json()["routing"]["generation"] == "deepseek"
    read = api.get("/v2/model/executors", headers=LEARNER).json()
    assert read["routing"]["fallback_hours"] == 6 and read["token_configured"] is True and read["grading_trigger_configured"] is False
    assert api.put("/v2/model/executors", json={"generation": "gpt"}, headers=LEARNER).status_code == 422
    assert api.put("/v2/model/executors", json={"generation": "claude"}).status_code == 401
    assert lease_one(api) == []                                    # Claude may not take DeepSeek's work
    ext.save_policy(db, {"generation": "claude"})
    assert ai_jobs.claim(db) is None
    db.execute("UPDATE ai_jobs SET created_at=clock_timestamp()-interval '30 hours' WHERE id=%s", (job["id"],))
    assert ai_jobs.claim(db) is None                               # 'claude': no fallback, ever
    assert len(lease_one(api)) == 1


def test_defaults_and_the_environment_fallback(db, monkeypatch):
    monkeypatch.delenv("EXECUTOR_GENERATION", raising=False)
    assert ext.policy(db) == dict(generation="deepseek", review="deepseek", grading="deepseek", fallback_hours=4)    # everything starts with DeepSeek
    monkeypatch.setenv("EXECUTOR_REVIEW", "claude")
    monkeypatch.setenv("EXECUTOR_FALLBACK_HOURS", "6")
    pol = ext.policy(db)
    assert pol["review"] == "claude" and pol["fallback_hours"] == 6
    ext.save_policy(db, {"review": "claude_first", "fallback_hours": 2})
    assert ext.policy(db)["review"] == "claude_first" and ext.policy(db)["fallback_hours"] == 2
    with pytest.raises(ValueError):
        ext.save_policy(db, {"generation": "gpt"})


def test_an_expired_lease_puts_the_task_back_and_a_late_submit_is_refused(db, corpus, setup_ai, api):
    one_job(db, setup_ai)
    first = lease_one(api)[0]
    assert lease_one(api, session="session-bbbbbb") == []
    db.execute("UPDATE external_leases SET expires_at=clock_timestamp()-interval '1 minute' WHERE lease_id=%s", (first["lease_id"],))
    again = lease_one(api, session="session-bbbbbb")              # the task is back in the queue
    assert len(again) == 1 and again[0]["lease_id"] != first["lease_id"]
    late = api.post("/v2/external/submit", headers=HEADERS,
                    json=dict(lease_id=first["lease_id"], session="session-aaaaaa", model_label="claude-test", result={"question": {}}))
    assert late.status_code == 410 and late.json()["detail"] == "lease_expired"
    assert db.execute("SELECT state FROM external_leases WHERE lease_id=%s", (first["lease_id"],)).fetchone()["state"] == "expired"


def test_release_gives_the_task_back_at_once(db, corpus, setup_ai, api):
    one_job(db, setup_ai)
    got = lease_one(api)[0]
    assert api.post("/v2/external/release", json={"lease_id": got["lease_id"]}, headers=HEADERS).json() == {"released": True}
    assert len(lease_one(api, session="session-bbbbbb")) == 1
    assert api.post("/v2/external/release", json={"lease_id": got["lease_id"]}, headers=HEADERS).status_code == 409


def test_a_task_that_keeps_timing_out_is_left_to_the_fallback(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    for n in range(ext.MAX_EXPIRED_LEASES):
        lease = lease_one(api, session=f"session-{n}-xxxx")[0]
        db.execute("UPDATE external_leases SET expires_at=clock_timestamp()-interval '1 minute' WHERE lease_id=%s", (lease["lease_id"],))
    assert lease_one(api, session="session-final-x") == []
    assert db.execute("SELECT state FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()["state"] == "pending"


def test_a_session_id_must_look_like_one(api):
    assert api.get("/v2/external/lease?kind=generation&max=1&session=short", headers=HEADERS).status_code == 422
    assert api.get("/v2/external/lease?kind=poetry&max=1&session=session-aaaaaa", headers=HEADERS).status_code == 422


# ---- submitting: the same checks, cost 0 ------------------------------------------------------------------------------------------

def ledger_rows(db, job_id):
    return sum(db.execute(f"SELECT count(*) AS n FROM {t} WHERE job_id=%s", (job_id,)).fetchone()["n"]
               for t in ("ai_call_events", "ai_cny_reservations", "ai_cny_events"))


def submit(api, lease, session="session-aaaaaa", result=None, label="claude-opus-test-label"):
    return api.post("/v2/external/submit", headers=HEADERS, json=dict(lease_id=lease["lease_id"], session=session, model_label=label, result=result))


def test_a_good_result_passes_the_same_checks_costs_nothing_and_is_recorded(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    lease = lease_one(api)[0]
    r = submit(api, lease, result=choice_response(job)["response"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["passed"] is True and body["outcome"] == "passed" and body["reasons"] == []
    row = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    assert row["state"] == "succeeded" and row["dispatched_at"] is not None
    assert (row["executor"], row["ext_model_label"], row["ext_session"]) == ("claude-subscription", "claude-opus-test-label", "session-aaaaaa")
    assert row["result"]["real_api"] is True and row["result"]["response"] == choice_response(job)["response"]
    assert ledger_rows(db, job["id"]) == 0 and float(row["reserved_cny"]) == 0              # cost 0, nothing in the DeepSeek ledger
    # exactly what a DeepSeek result becomes: a candidate, checked for free, one review queued
    qid = body["detail"].get("question_id") or db.execute("SELECT question_id FROM candidate_review_packets").fetchone()["question_id"]
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["status"] == "checking"
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='quality_review'").fetchone()["n"] == 1
    assert db.execute("SELECT session FROM external_question_sessions WHERE question_id=%s", (qid,)).fetchone()["session"] == "session-aaaaaa"
    assert db.execute("SELECT state,outcome FROM external_leases WHERE lease_id=%s", (lease["lease_id"],)).fetchone() == dict(state="submitted", outcome="passed")
    # submitting twice is refused
    assert submit(api, lease, result=choice_response(job)["response"]).status_code == 409


def test_a_bad_result_is_stopped_by_the_same_checks_with_a_reason(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    lease = lease_one(api)[0]
    bad = choice_response(job)["response"]
    bad["question"]["rubric"][0]["points"] = 2                      # the points no longer add up to the full score
    r = submit(api, lease, result=bad)
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is False and body["outcome"] == "repair_requested"       # the one repair, as for DeepSeek
    assert "rubric_points_mismatch" in body["reasons"]
    assert db.execute("SELECT count(*) AS n FROM candidate_review_packets").fetchone()["n"] == 0
    assert ledger_rows(db, job["id"]) == 0
    # the repair task can be leased and answered the same way
    repair = lease_one(api, session="session-aaaaaa")
    assert len(repair) == 1 and repair[0]["payload"]["repair_of_job"] == job["id"]
    unreadable = submit(api, repair[0], result={"nothing": "useful"})
    assert unreadable.status_code == 200 and unreadable.json()["passed"] is False


def test_a_quote_that_is_not_in_the_paragraph_is_stopped_like_for_deepseek(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    lease = lease_one(api)[0]
    bad = choice_response(job, invent="B")["response"]
    r = submit(api, lease, result=bad)
    assert r.json()["passed"] is False


def test_a_lease_belongs_to_its_session_and_results_must_be_objects(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    lease = lease_one(api)[0]
    assert submit(api, lease, session="session-other-x", result=choice_response(job)["response"]).status_code == 403
    assert submit(api, lease, result={}).status_code in (200, 422)
    assert api.post("/v2/external/submit", headers=HEADERS, json=dict(lease_id=lease["lease_id"], session="session-aaaaaa", model_label="x", result="text")).status_code == 422
    assert submit(api, dict(lease_id="doesnotexist1"), result={"a": 1}).status_code == 404


# ---- review independence ----------------------------------------------------------------------------------------------------------

def test_a_session_never_reviews_a_question_it_wrote(db, corpus, setup_ai, api):
    ext.save_policy(db, {"review": "claude"})
    job = one_job(db, setup_ai)
    lease = lease_one(api, session="session-writer")[0]
    assert submit(api, lease, session="session-writer", result=choice_response(job)["response"]).json()["passed"] is True
    assert lease_one(api, session="session-writer", kind="review") == []                  # not its own
    other = lease_one(api, session="session-reviewer", kind="review")
    assert len(other) == 1 and other[0]["kind"] == "review"
    assert other[0]["payload"]["question"]["question_type"] == "single_choice"


def test_the_deepseek_worker_still_reviews_by_default(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    lease = lease_one(api)[0]
    assert submit(api, lease, result=choice_response(job)["response"]).json()["passed"] is True
    assert lease_one(api, session="session-reviewer", kind="review") == []                 # review = DeepSeek by default
    review = ai_jobs.claim(db)
    assert review and review["kind"] == "quality_review"


# ---- status and the per-executor figures ------------------------------------------------------------------------------------------

def test_status_counts_claimable_work_and_today_per_executor(db, corpus, setup_ai, api):
    job = one_job(db, setup_ai)
    s = api.get("/v2/external/status", headers=HEADERS).json()
    assert s["claimable"]["generation"] == 1 and s["claimable"]["review"] == 0 and s["routing"]["generation"] == "claude_first"
    submit(api, lease_one(api)[0], result=choice_response(job)["response"])
    s = api.get("/v2/external/status", headers=HEADERS).json()
    today = s["today"]["claude-subscription"]
    assert today["done"] == 1 and today["passed"] == 1 and today["pass_rate"] == 1.0
    assert s["today"]["deepseek"]["done"] == 0
    rep = progress.report(db, setup_ai)
    assert rep["executors"]["today"]["claude-subscription"]["done"] == 1
    labelled = [j for j in rep["recent_jobs"] if j["id"] == job["id"]][0]
    assert labelled["executor"] == "claude"
    assert all(j["executor"] in ("claude", "deepseek") for j in rep["recent_jobs"])


# ---- phase two: the grading trigger is off unless configured ------------------------------------------------------------------------

def test_the_grading_trigger_is_off_by_default_and_needs_both_variables_and_the_route(db, monkeypatch):
    from app import routine_trigger

    started = []
    monkeypatch.setattr(routine_trigger.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())
    monkeypatch.delenv("CLAUDE_ROUTINE_GRADING_URL", raising=False)
    monkeypatch.delenv("CLAUDE_ROUTINE_GRADING_TOKEN", raising=False)
    assert routine_trigger.fire_for_grading(db, 3) is False
    monkeypatch.setenv("CLAUDE_ROUTINE_GRADING_URL", "https://example.invalid/fire")
    assert routine_trigger.fire_for_grading(db, 3) is False                        # token missing
    monkeypatch.setenv("CLAUDE_ROUTINE_GRADING_TOKEN", "secret-token")
    assert routine_trigger.fire_for_grading(db, 3) is False                        # grading is routed to DeepSeek by default
    ext.save_policy(db, {"grading": "claude_first"})
    assert routine_trigger.fire_for_grading(db, 3) is True and len(started) == 1
    assert routine_trigger.fire_for_grading(db, 0) is False


# ---- the blind comparison -----------------------------------------------------------------------------------------------------------

def test_the_comparison_sends_identical_tasks_to_both_and_the_page_never_says_who(db, corpus, setup_ai, api, monkeypatch):
    monkeypatch.setattr(comparison, "SLOTS", ("single_choice", "single_choice"))
    started = comparison.start(db, setup_ai)
    assert started["pairs"] >= 1
    pairs = db.execute("SELECT * FROM comparison_pairs WHERE set_id=%s ORDER BY position", (started["set_id"],)).fetchall()
    for pair in pairs:
        mine = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (pair["deepseek_job"],)).fetchone()
        theirs = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (pair["claude_job"],)).fetchone()
        assert mine["payload"] == theirs["payload"]                                  # the very same task content
        assert (mine["executor"], theirs["executor"]) == ("deepseek", "claude-subscription")
    # the DeepSeek copy is the DeepSeek worker's even though generation is routed 'Claude first'
    claimed = ai_jobs.claim(db)
    assert claimed and claimed["executor"] == "deepseek"
    # the Claude copy is what a Routine is given
    leased = lease_one(api)
    assert len(leased) == 1 and leased[0]["payload"] == claimed["payload"]


def run_pair(db, api, set_id, who_first="claude"):
    """Answer both jobs of the first pair with the same good draft (so both pass the checks) and return the pair."""
    pair = db.execute("SELECT * FROM comparison_pairs WHERE set_id=%s ORDER BY position LIMIT 1", (set_id,)).fetchone()
    ds = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (pair["deepseek_job"],)).fetchone()
    claimed = ai_jobs.claim(db)
    assert claimed["id"] == ds["id"]
    from test_past_exam_import import finish_job

    finish_job(db, claimed, choice_response(claimed))
    auto_worker.dispatch_completed(db)
    lease = lease_one(api)[0]
    cl = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (pair["claude_job"],)).fetchone()
    response = choice_response(cl)["response"]
    response["question"]["stem"] = "下列哪种酶最终封闭相邻冈崎片段之间留下的缺口并使两段DNA连成一条链？"      # a different wording, so the two are distinct questions
    assert submit(api, lease, result=response).json()["passed"] is True
    return pair


def test_rating_both_sides_reveals_the_sources_and_the_pass_rates(db, corpus, setup_ai, api, monkeypatch):
    monkeypatch.setattr(comparison, "SLOTS", ("single_choice",))
    set_id = comparison.start(db, setup_ai)["set_id"]
    pair = run_pair(db, api, set_id)
    before = comparison.view(db, setup_ai, set_id)
    assert before["complete"] is False and "reveal" not in before
    text = str(before).lower()
    for leak in ("claude", "deepseek", "a_is", "job", "executor", "model_label", "session"):
        assert leak not in text, leak
    assert [s["label"] for s in before["pairs"][0]["sides"]] == ["甲", "乙"]
    comparison.rate(db, setup_ai, set_id, pair["position"], "A", 4, 5, 3, "还行")
    assert comparison.view(db, setup_ai, set_id)["complete"] is False                    # one side still unrated
    comparison.rate(db, setup_ai, set_id, pair["position"], "B", 2, 5, 4, None)
    done = comparison.view(db, setup_ai, set_id)
    assert done["complete"] is True
    ex = done["reveal"]["executors"]
    assert ex["claude"]["program_check_rate"] == 1.0 and ex["deepseek"]["program_check_rate"] == 1.0
    assert {ex["claude"]["realism"], ex["deepseek"]["realism"]} == {4, 2}
    assert done["reveal"]["pairs"][0]["A"] in ("DeepSeek", "Claude（订阅）")
    with pytest.raises(ValueError):
        comparison.rate(db, setup_ai, set_id, pair["position"], "C", 3, 3, 3, None)
    with pytest.raises(ValueError):
        comparison.rate(db, setup_ai, set_id, pair["position"], "A", 6, 3, 3, None)


def test_the_comparisons_questions_are_reviewed_by_deepseek_even_when_review_is_routed_to_claude(db, corpus, setup_ai, api, monkeypatch):
    ext.save_policy(db, {"review": "claude"})
    monkeypatch.setattr(comparison, "SLOTS", ("single_choice",))
    set_id = comparison.start(db, setup_ai)["set_id"]
    pair = db.execute("SELECT * FROM comparison_pairs WHERE set_id=%s", (set_id,)).fetchone()
    lease = lease_one(api)[0]
    cl = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (pair["claude_job"],)).fetchone()
    assert submit(api, lease, result=choice_response(cl)["response"]).json()["passed"] is True
    assert lease_one(api, session="session-reviewer", kind="review") == []                   # not for a Routine
    review = ai_jobs.claim(db)                                                              # DeepSeek's worker takes it
    assert review and review["kind"] == "quality_review"
    # an ordinary question's review would have gone to Claude under the same route
    other = db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='quality_review'").fetchone()["n"]
    assert other == 1


def test_a_repair_of_a_comparison_draft_stays_with_the_executor_that_wrote_it(db, corpus, setup_ai, api, monkeypatch):
    monkeypatch.setattr(comparison, "SLOTS", ("single_choice",))
    set_id = comparison.start(db, setup_ai)["set_id"]
    pair = db.execute("SELECT * FROM comparison_pairs WHERE set_id=%s", (set_id,)).fetchone()
    lease = lease_one(api)[0]
    cl = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (pair["claude_job"],)).fetchone()
    bad = choice_response(cl)["response"]
    bad["question"]["rubric"][0]["points"] = 2
    assert submit(api, lease, result=bad).json()["outcome"] == "repair_requested"
    repair = db.execute("SELECT executor FROM ai_jobs WHERE kind='repair'").fetchone()
    assert repair["executor"] == "claude-subscription"
