"""The "no review" choice really reaches the Claude questions: an old saved review setting does not stand in its way, the sweep says what it did,
and a fill-in submitted through the Routine interface is ready and counted with no review task."""
import pytest

from app import auto_generation as gen, auto_quality, auto_worker, external_executor as ext, legacy_reuse, past_exam_import as pei
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_past_exam_import import corpus, items, model_result  # noqa: F401  (fixtures/helpers)
from test_program_only_review import review_jobs_of, write
from test_gap_generation import corpus as gap_corpus  # noqa: F401  (fixture)


def test_saving_one_setting_does_not_freeze_the_others(db):
    ext.save_policy(db, {"generation": "claude"})
    assert legacy_reuse._state(db, ext.STATE_KEY) == {"generation": "claude"}          # only what was chosen is kept; the rest follows the default
    ext.save_policy(db, {"grading": "deepseek"})
    assert legacy_reuse._state(db, ext.STATE_KEY) == {"generation": "claude", "grading": "deepseek"}
    ext.save_policy(db, {"review": "claude"})                                           # a review choice made now is the owner's
    assert ext.apply_owner_review_choice(db) is None and ext.policy(db)["review"] == "claude"


@pytest.mark.parametrize("old", ["deepseek", "claude", "claude_first"])
def test_a_review_setting_saved_before_the_choice_is_replaced_once(db, old):
    legacy_reuse._set_state(db, ext.STATE_KEY, {"generation": "claude", "review": old, "grading": "deepseek", "fallback_hours": 4})   # as the old save wrote it
    assert ext.policy(db)["review"] == old                                              # this is what blocked the sweep and held DeepSeek's reviews
    assert ext.apply_owner_review_choice(db) == old
    assert ext.policy(db)["review"] == "none" and ext.policy(db)["generation"] == "claude"
    assert ext.apply_owner_review_choice(db) is None                                    # once only
    ext.save_policy(db, {"review": "deepseek"})                                         # a later choice on the page is kept
    assert ext.apply_owner_review_choice(db) is None and ext.policy(db)["review"] == "deepseek"


def test_nothing_saved_means_the_default_and_the_environment_stand(db, monkeypatch):
    monkeypatch.setenv("EXECUTOR_REVIEW", "deepseek")
    assert ext.apply_owner_review_choice(db) is None and ext.policy(db)["review"] == "deepseek"      # the deployer's setting is not overwritten


def test_the_worker_applies_it_and_logs_it(db, setup_ai, monkeypatch):
    legacy_reuse._set_state(db, ext.STATE_KEY, {"review": "claude_first"})
    monkeypatch.setenv("AI_QUEUE_AUTOPLAN", "0")
    report = {}
    auto_worker._plan_queue(db, report, [], True, False)
    assert report["review_setting"] == "none(was:claude_first)" and ext.policy(db)["review"] == "none"


def test_the_log_always_says_what_the_program_only_step_did_and_why_not(db, gap_corpus, setup_ai):
    report = {}
    auto_worker.publish_waiting_claude_questions(db, report)
    assert report["program_only"] == "considered=0,published=0,blocked=0,review_setting=none,reason=nothing_is_waiting_for_review"
    ext.save_policy(db, {"review": "claude"})
    report = {}
    auto_worker.publish_waiting_claude_questions(db, report)
    assert report["program_only"] == "considered=0,published=0,blocked=0,review_setting=claude,reason=review_setting_is_claude"
    ext.save_policy(db, {"review": "deepseek"})
    qid, _ = write(db, setup_ai, None, "deepseek")                                      # waiting, but DeepSeek wrote it
    ext.save_policy(db, {"review": "none"})
    report = {}
    assert auto_worker.publish_waiting_claude_questions(db, report) == 0
    assert report["program_only"].startswith("considered=1,published=0,blocked=0,review_setting=none,reason=none_written_by_claude(") and "not_claude=1" in report["program_only"]


def test_a_fill_in_submitted_through_the_routine_interface_is_ready_and_counted_with_no_review_task(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setenv("EXECUTOR_GENERATION", "claude")
    monkeypatch.setattr(gen, "pinned_science_evidence", lambda content: True)
    legacy_reuse._set_state(db, ext.STATE_KEY, {"generation": "claude", "review": "deepseek"})        # the state production was in
    pei.import_items(db)
    db.execute("UPDATE past_exam_items SET status='blocked',blocked_reason='x' WHERE question_type<>'fill_blank'")
    pei.plan(db, setup_ai, batch=5)
    job = next(j for j in db.execute("SELECT * FROM ai_jobs WHERE payload->>'origin'='past_exam' ORDER BY id").fetchall() if j["payload"]["question_type"] == "fill_blank")
    before = gen.gap_diagnosis(db, setup_ai)["avail"]
    assert "bioc:fill_blank=0" in before
    lease = next(l for l in ext.lease(db, "generation", 5, "session-aaaaaa") if l["task"]["item_id"] == job["payload"]["item_id"])
    with monkeypatch.context() as old:
        old.setattr(ext, "apply_owner_review_choice", lambda conn: None)                           # (the state production was in when it was submitted)
        out = ext.submit(db, lease["lease_id"], "session-aaaaaa", "claude-test", model_result(job)["response"])
    assert out["passed"] is True
    qid = db.execute("SELECT question_id FROM past_exam_items WHERE id=%s", (job["payload"]["item_id"],)).fetchone()["question_id"]
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] == "draft"          # waiting for a DeepSeek review
    assert len(review_jobs_of(db, qid)) == 1
    # the question is waiting for a review the owner does not want: the next planning pass settles it
    report = {}
    auto_worker.plan(db, setup_ai, target=2, report=report)
    assert report["program_only"].startswith("considered=1,published=1,blocked=0,review_setting=none,reason=ok") and report["review_setting"] == "none(was:deepseek)"
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] == "published"
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["status"] == "ready"
    assert [j for j in review_jobs_of(db, qid) if j["state"] in ("pending", "retrying", "running")] == []      # no review task is waiting
    assert auto_quality.ready(db, qid)
    assert "bioc:fill_blank=1" in gen.gap_diagnosis(db, setup_ai)["avail"]                       # counted in what a paper can draw
