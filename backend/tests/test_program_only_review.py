"""The owner's choice "no review": a question a Claude Routine wrote is published on the program checks alone; DeepSeek's questions and
grading are unaffected; the learner's reports are counted per writer; planning gives a Routine enough writing tasks."""
import pytest
from psycopg.types.json import Jsonb

from app import auto_generation as gen, auto_quality, auto_worker, external_executor as ext
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_gap_generation import choice_response, corpus, finish_job, plan_single  # noqa: F401  (fixtures/helpers)


def write(db, uid, executor, mode):
    """A single-choice question written by `executor`, dispatched under review mode `mode`; returns (question id, writing job)."""
    ext.save_policy(db, {"review": mode})
    job = plan_single(db, uid)
    db.execute("UPDATE ai_jobs SET executor=%s WHERE id=%s", (executor, job["id"]))
    finish_job(db, job, choice_response(job))
    auto_worker.dispatch_completed(db)
    qid = db.execute("SELECT question_id FROM candidate_review_packets WHERE candidate_key LIKE %s", (f"auto-generated-{job['id']}-%",)).fetchone()["question_id"]
    return qid, db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()


def review_jobs_of(db, qid):
    return db.execute("SELECT * FROM ai_jobs WHERE kind='quality_review' AND payload->>'question_id'=%s", (str(qid),)).fetchall()


def test_no_review_is_the_default_and_only_the_review_task_may_have_it(db):
    assert ext.policy(db) == dict(generation="deepseek", review="none", grading="deepseek", fallback_hours=4)
    with pytest.raises(ValueError, match="unknown_executor_mode"):
        ext.save_policy(db, {"generation": "none"})
    with pytest.raises(ValueError, match="unknown_executor_mode"):
        ext.save_policy(db, {"grading": "none"})
    assert ext.save_policy(db, {"review": "claude"})["review"] == "claude" and ext.save_policy(db, {"review": "none"})["review"] == "none"


def test_a_claude_question_is_published_on_the_program_checks_with_no_review_task(db, corpus, setup_ai):
    qid, job = write(db, setup_ai, ext.CLAUDE, "none")
    assert review_jobs_of(db, qid) == []                                              # no quality_review task is created
    state = db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["status"]
    assert state == "ready" and db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] == "published"
    assert auto_quality.ready(db, qid)                                                # the same readiness rule every count uses
    event = db.execute("SELECT result,passed FROM auto_quality_events WHERE question_id=%s AND phase='review'", (qid,)).fetchone()
    assert event["passed"] and event["result"]["review_mode"] == "program_only" and event["result"]["executor"] == ext.CLAUDE and event["result"]["writer_job"] == job["id"]
    source = db.execute("SELECT source_ref FROM question_origins WHERE question_id=%s", (qid,)).fetchone()["source_ref"]
    assert source["executor"] == ext.CLAUDE and source["review"] == "program_only" and source["review_label"] == "仅程序检查"


def test_a_deepseek_question_is_still_reviewed_by_deepseek_and_so_is_everything_when_review_is_on(db, corpus, setup_ai):
    qid, _ = write(db, setup_ai, None, "none")                                        # written by DeepSeek: the old path
    assert len(review_jobs_of(db, qid)) == 1
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] == "draft"
    db.execute("UPDATE ai_jobs SET state='cancelled' WHERE kind='quality_review'")
    other, _ = write(db, setup_ai, ext.CLAUDE, "deepseek")                            # "DeepSeek" review: kept as it was, for Claude's questions too
    assert len(review_jobs_of(db, other)) == 1 and db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (other,)).fetchone()["status"] == "draft"


def test_the_program_checks_are_the_only_gate_and_they_stay_strict(db, corpus, setup_ai, monkeypatch):
    # a stem too like a published question of the same subject and type
    first, _ = write(db, setup_ai, ext.CLAUDE, "none")
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (first,)).fetchone()["status"] == "published"
    db.execute("UPDATE ai_jobs SET state='cancelled'")
    second, _ = write(db, setup_ai, ext.CLAUDE, "none")
    state = db.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (second,)).fetchone()
    assert state["status"] == "blocked" and "duplicate_ready_question" in state["reason"]                       # same cell, same stem: not published
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (second,)).fetchone()["status"] == "draft"
    assert review_jobs_of(db, second) == []
    # the writing rules are checked again at publication (forbidden wording, answer given away ...)
    monkeypatch.setattr("app.exam_authoring.issues", lambda q: ["textbook_wording_in_stem"])
    db.execute("UPDATE auto_question_states SET status='candidate',reason=NULL WHERE question_id=%s", (second,))
    db.execute("UPDATE reviewed_questions SET status='draft' WHERE id=%s", (second,))
    monkeypatch.setattr(auto_quality, "hard_issues", lambda conn, qid, q: [])
    writer = {"id": 1, "executor": ext.CLAUDE}
    assert auto_quality.publish_on_program_checks(db, setup_ai, second, writer) is False
    assert db.execute("SELECT reason FROM auto_question_states WHERE question_id=%s", (second,)).fetchone()["reason"] == "textbook_wording_in_stem"


def test_questions_already_waiting_for_a_review_are_published_and_their_review_tasks_cancelled(db, corpus, setup_ai):
    qid, job = write(db, setup_ai, ext.CLAUDE, "claude")                              # written while review was on: a review task is waiting
    assert len(review_jobs_of(db, qid)) == 1
    deepseek_q, _ = write(db, setup_ai, None, "claude")
    ext.save_policy(db, {"review": "none"})                                           # now the owner chooses "no review"
    db.execute("UPDATE auto_question_states SET status='checking' WHERE question_id=ANY(%s)", ([qid, deepseek_q],))
    report = {}
    assert auto_worker.publish_waiting_claude_questions(db, report) == 1
    assert report["program_only"].startswith("considered=2,published=1,blocked=0,review_setting=none,reason=ok")
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] == "published"
    assert [j["state"] for j in review_jobs_of(db, qid)] == ["cancelled"]            # the DeepSeek review task is cancelled
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (deepseek_q,)).fetchone()["status"] == "draft"      # DeepSeek's question waits for its review
    assert len([j for j in review_jobs_of(db, deepseek_q) if j["state"] != "cancelled"]) == 1
    assert auto_worker.publish_waiting_claude_questions(db) == 0                      # nothing left to do: idempotent


def test_grading_is_still_deepseeks_and_untouched(db, corpus, setup_ai):
    from app import ai_jobs

    write(db, setup_ai, ext.CLAUDE, "none")
    assert ext.policy(db)["grading"] == "deepseek"
    job = ai_jobs.enqueue(db, setup_ai, "grade-x", "grading", {"instructions": "x"})
    ext.save_policy(db, {"review": "none"})
    assert ai_jobs.claim(db)["id"] == job["id"]                                       # claimed by the DeepSeek worker as always
    assert ext.lease(db, "grading", 3, "session-aaaaaa") == []                        # and no Routine is handed it


# ---- the learner's reports, per writer ---------------------------------------------------------------------------------------------------

def test_the_share_of_reported_claude_questions_is_counted_and_above_ten_percent_it_advises_review(db, corpus, setup_ai):
    from stats_helpers import question

    uid = setup_ai
    made = []
    for n in range(12):
        qid = question(db, "biochemistry", "term", 5, "第一章", stem=f"出过卷的题{n}")
        db.execute("INSERT INTO question_origins(question_id,origin,source_ref) VALUES (%s,'generated',%s)", (qid, Jsonb({"executor": ext.CLAUDE})))
        pid = db.execute("INSERT INTO papers_v2(user_id,paper_date,blueprint) VALUES (%s,current_date - %s,%s) RETURNING id", (uid, n, Jsonb({"specification": "x"}))).fetchone()["id"]
        content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
        db.execute("INSERT INTO paper_items_v2(paper_id,question_id,position,snapshot) VALUES (%s,%s,1,%s)", (pid, qid, Jsonb(content)))
        made.append((pid, qid))
    rates = ext.report_rates(db)[ext.CLAUDE]
    assert rates["used"] == 12 and rates["reported"] == 0 and rates["rate"] == 0.0 and rates["enough"] and not rates["advise"]
    db.execute("INSERT INTO question_reports(paper_id,question_id,user_id,note) VALUES (%s,%s,%s,'x')", (*made[0], uid))
    assert ext.report_rates(db)[ext.CLAUDE]["advise"] is False                         # 1 of 12 = 8%
    db.execute("INSERT INTO question_reports(paper_id,question_id,user_id,note) VALUES (%s,%s,%s,'x')", (*made[1], uid))
    got = ext.report_rates(db)[ext.CLAUDE]
    assert got["reported"] == 2 and got["rate"] == round(2 / 12, 3) and got["advise"] is True                # 17%: advise review
    assert ext.report_rates(db)[ext.DEEPSEEK]["used"] == 0                              # DeepSeek's are counted apart
    from app import progress

    assert progress.report(db, uid)["executors"]["report_rates"][ext.CLAUDE]["advise"] is True


def test_a_small_sample_says_nothing_yet(db, setup_ai):
    assert ext.report_rates(db)[ext.CLAUDE] == dict(used=0, reported=0, rate=None, enough=False, advise=False)


# ---- planning for a Routine ---------------------------------------------------------------------------------------------------------------

def test_when_claude_writes_planning_queues_enough_writing_tasks_for_one_run_whatever_else_waits(db, corpus, setup_ai, monkeypatch):
    ext.save_policy(db, {"generation": "claude"})
    monkeypatch.setattr(gen, "bucket_gaps", lambda conn, uid: [("biochemistry", "fill_blank", 60), ("cell_biology", "fill_blank", 60)])
    for n in range(40):                                                               # lots of other waiting work: reviews of DeepSeek questions
        db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload) VALUES (%s,%s,'quality_review','pending','{}',%s)",
                   (f"rv{n}", setup_ai, Jsonb({"question_id": n + 1000, "subject": "biochemistry", "question_type": "term"})))
    for n in range(2):                                                                # and the two writing tasks that used to be the cap
        db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload) VALUES (%s,%s,'generation','pending','{}',%s)",
                   (f"wr{n}", setup_ai, Jsonb({"subject": "biochemistry", "question_type": "fill_blank", "max_points": 2.5, "evidence": []})))
    queued = gen.fill_queue(db, setup_ai, gen.CLAUDE_QUEUE_TARGET, writing_only=True)
    assert queued >= 3 and gen.FILL_DETAIL["want"] == 30 and gen.FILL_DETAIL["writing_only"] is True
    assert gen.generation_claimable_count(db, setup_ai) <= 30


def test_the_log_lists_what_the_waiting_tasks_are_made_of(db, setup_ai):
    for n in range(3):
        db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload) VALUES (%s,%s,'quality_review','pending','{}',%s)", (f"r{n}", setup_ai, Jsonb({})))
    db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,executor) VALUES ('g1',%s,'generation','retrying','{}',%s,'claude-subscription')",
               (setup_ai, Jsonb({"origin": "past_exam"})))
    line = auto_worker.pending_mix(db)
    assert "quality_review/-/-:pending=3" in line and "generation/past_exam/claude-subscription:retrying=1" in line


# ---- paragraph_id -------------------------------------------------------------------------------------------------------------------------

def test_every_paragraph_a_routine_is_given_carries_its_paragraph_id():
    task = ext.slim_task(dict(instructions="x", schema={}, evidence=[dict(unit_id=41, text="a"), dict(unit_id=7, text="b")], style=[]))
    assert [(u["unit_id"], u["paragraph_id"]) for u in task["evidence"]] == [(41, 41), (7, 7)]
