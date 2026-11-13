"""Progress page task list: readable names, review outcomes, specific failure reasons; review-before-writing claim order."""
import pytest
from psycopg.types.json import Jsonb

from app import ai_jobs, auto_worker, cost_control, progress
from stats_helpers import question
from test_ai_automation import setup_ai  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def off_peak(monkeypatch):
    monkeypatch.setattr(cost_control, "held_back_kinds", lambda at=None: [])


def add_job(db, uid, key, kind, state="pending", payload=None, result=None, error=None, dispatched=False):
    return db.execute(
        "INSERT INTO ai_jobs(job_key,user_id,kind,state,settings_snapshot,payload,result,error_code,dispatched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (key, uid, kind, state, Jsonb({"effort": "low", "model_id": "deepseek-flash"}), Jsonb(payload or {}), Jsonb(result) if result else None, error,
         __import__("datetime").datetime.now(__import__("datetime").timezone.utc) if dispatched else None),
    ).fetchone()["id"]


def state(db, qid, status, reason=None, revisions=0):
    db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256,reason,revision_count) VALUES (%s,%s,'x',%s,%s)", (qid, status, reason, revisions))


def origin(db, qid, kind="generated", item=None, source=None):
    db.execute("INSERT INTO question_origins(question_id,origin,past_exam_item_id,source_ref) VALUES (%s,%s,%s,%s)", (qid, kind, item, Jsonb(source or {})))


def by_label(db, uid):
    return {j["label"]: j for j in progress.recent_jobs(db, 50)}


def test_every_task_is_named_by_kind_and_the_question_or_source(db, setup_ai):
    new = question(db, "biochemistry", "fill_blank", 4, "章")
    state(db, new, "ready"); origin(db, new)
    exam = question(db, "biochemistry", "term", 6, "章")
    state(db, exam, "blocked", "unresolved_quality_findings")
    legacy = question(db, "cell_biology", "single_choice", 4, "章")
    state(db, legacy, "checking"); origin(db, legacy, source={"legacy_draft_id": "d-1"})
    repaired = question(db, "biochemistry", "short_answer", 12, "章")
    state(db, repaired, "checking", revisions=1); origin(db, repaired)
    add_job(db, setup_ai, "r1", "quality_review", "succeeded", {"question_id": new}, {"real_api": True}, dispatched=True)
    add_job(db, setup_ai, "r2", "quality_review", "pending", {"question_id": legacy})
    add_job(db, setup_ai, "r3", "quality_review", "pending", {"question_id": repaired})
    add_job(db, setup_ai, "p1", "repair", "pending", {"question_id": exam})
    add_job(db, setup_ai, "g1", "generation", "succeeded", {"subject": "biochemistry", "question_type": "fill_blank"}, {"real_api": True}, dispatched=True)
    add_job(db, setup_ai, "g2", "generation", "pending", {"subject": "cell_biology", "question_type": "essay", "origin": "past_exam", "item_id": 37})
    add_job(db, setup_ai, "c1", "compatibility", "pending", {})
    labels = by_label(db, setup_ai)
    assert set(labels) == {
        f"审核 · 生化填空 #{new}", f"审核 · 旧候选复用 细胞单选 #{legacy}", f"审核 · 生化简答 #{repaired}（修复后）",
        f"修复 · 生化名词解释 #{exam}", "出题 · 生化填空（新题）", "出题 · 真题问答答案（真题条目 #37）", "兼容性探测 · deepseek-flash / low",
    }
    assert labels[f"审核 · 旧候选复用 细胞单选 #{legacy}"]["source"] == "legacy"
    assert labels[f"审核 · 生化简答 #{repaired}（修复后）"]["source"] == "generated+repaired"
    assert labels["出题 · 真题问答答案（真题条目 #37）"]["source"] == "past_exam"
    assert labels[f"审核 · 生化填空 #{new}"]["question_id"] == new


def test_a_past_exam_review_says_so(db, setup_ai):
    qid = question(db, "biochemistry", "term", 6, "章")
    state(db, qid, "checking")
    # origin is what the label reads; the FK to the past-exam item is dropped inside this rolled-back test only
    db.execute("ALTER TABLE question_origins DROP CONSTRAINT IF EXISTS question_origins_past_exam_item_id_fkey")
    origin(db, qid, "past_exam", item=99)
    add_job(db, setup_ai, "r9", "quality_review", "pending", {"question_id": qid})
    assert f"审核 · 真题名词解释 #{qid}" in by_label(db, setup_ai)


def test_a_finished_review_shows_whether_the_question_passed_and_why_not(db, setup_ai):
    good = question(db, "biochemistry", "fill_blank", 4, "章")
    bad = question(db, "biochemistry", "fill_blank", 4, "章")
    state(db, good, "ready"); state(db, bad, "blocked", "rubric_not_fully_evidenced")
    for qid, key in ((good, "a"), (bad, "b")):
        add_job(db, setup_ai, key, "quality_review", "succeeded", {"question_id": qid}, {"real_api": True}, dispatched=True)
    jobs = {j["question_id"]: j for j in progress.recent_jobs(db, 20)}
    assert jobs[good]["outcome"] == {"status": "ready", "reason": None}
    assert jobs[bad]["outcome"] == {"status": "blocked", "reason": "rubric_not_fully_evidenced"}


def test_a_failed_result_keeps_the_specific_reason_for_every_job_not_only_qa_jobs(db, setup_ai):
    jid = add_job(db, setup_ai, "bad-gen", "generation", "succeeded", {"subject": "biochemistry", "question_type": "fill_blank", "max_points": 2.5, "evidence": []},
                  {"real_api": True, "response": {"question": {"subject": "biochemistry"}}})
    auto_worker.dispatch_completed(db)
    row = db.execute("SELECT state,error_code,result FROM ai_jobs WHERE id=%s", (jid,)).fetchone()
    assert row["state"] == "blocked" and row["error_code"] == "invalid_or_uncertain_result"
    assert row["result"]["dispatch_failure"]["code"]  # a code, never the model's text
    listed = next(j for j in progress.recent_jobs(db, 5) if j["id"] == jid)
    assert listed["reason"] == row["result"]["dispatch_failure"]["code"] != "invalid_or_uncertain_result"
    bucket = next(b for b in progress.buckets(db, setup_ai) if (b["subject"], b["question_type"]) == ("biochemistry", "fill_blank"))
    assert bucket["blocked_reasons"] == {row["result"]["dispatch_failure"]["code"]: 1}


def test_schema_violations_name_the_field_and_the_rule():
    from pydantic import ValidationError
    from app.question_quality import Question

    with pytest.raises(ValidationError) as caught:
        Question.model_validate(dict(
            subject="biochemistry", question_type="term", stem="冈崎片段", options={}, answer="答案" * 10, accepted_answers=[], blanks=[],
            knowledge_tags=[], max_points=6, rubric=[dict(description="一点", points=5)],
            evidence=[dict(textbook="t", authors="a", edition="e", chapter="c", locator="l", excerpt="x")],
        ))
    failure = auto_worker.safe_dispatch_failure(caught.value)
    assert failure["code"] == "output_schema_violation" and failure["errors"][0]["reason"] == "rubric_points_mismatch"
    shown = progress.failure_detail({"dispatch_failure": failure})
    assert shown["code"] == "output_schema_violation" and "rubric_points_mismatch" in shown["text"]


def test_reviews_and_repairs_are_claimed_before_new_questions_are_written(db, setup_ai):
    gen_old = ai_jobs.enqueue(db, setup_ai, "gen-old", "generation", {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "y"})
    review = ai_jobs.enqueue(db, setup_ai, "rev-new", "quality_review", {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "y"})
    gen_new = ai_jobs.enqueue(db, setup_ai, "gen-new", "generation", {"evidence": [{"unit_id": 2, "text": "x"}], "instructions": "z"})
    assert gen_old["id"] < review["id"] < gen_new["id"]
    order = []
    for _ in range(3):
        job = ai_jobs.claim(db)
        order.append(job["kind"] + ":" + job["job_key"])
        db.execute("UPDATE ai_jobs SET state='succeeded' WHERE id=%s", (job["id"],))
    assert order == ["quality_review:rev-new", "generation:gen-old", "generation:gen-new"]  # review first, then oldest writing job


def test_probes_and_grading_still_come_first_and_each_class_is_oldest_first(db, setup_ai):
    a = ai_jobs.enqueue(db, setup_ai, "gen-a", "generation", {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "1"})
    b = ai_jobs.enqueue(db, setup_ai, "rev-b", "quality_review", {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "2"})
    c = ai_jobs.enqueue(db, setup_ai, "rep-c", "repair", {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "3"})
    d = ai_jobs.enqueue(db, setup_ai, "grade-d", "grading", {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "4"})
    order = []
    for _ in range(4):
        job = ai_jobs.claim(db)
        order.append(job["job_key"])
        db.execute("UPDATE ai_jobs SET state='succeeded' WHERE id=%s", (job["id"],))
    assert order == ["grade-d", "rev-b", "rep-c", "gen-a"] and a["id"] < b["id"] < c["id"] < d["id"]


def test_the_worker_log_head_follows_the_same_claim_order(db, setup_ai):
    ai_jobs.enqueue(db, setup_ai, "gen-x", "generation", {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "1"})
    ai_jobs.enqueue(db, setup_ai, "rev-y", "quality_review", {"evidence": [{"unit_id": 1, "text": "x"}], "instructions": "2"})
    states, held, head = auto_worker.queue_snapshot(db)
    assert head["kind"] == "quality_review"


def test_a_waiting_job_shows_its_attempts_and_when_it_retries(db, setup_ai):
    jid = add_job(db, setup_ai, "slow", "quality_review", "retrying", {})
    db.execute("UPDATE ai_jobs SET attempts=2,next_retry=clock_timestamp()+interval '2 minutes' WHERE id=%s", (jid,))
    job = next(j for j in progress.recent_jobs(db, 5) if j["id"] == jid)
    assert job["attempts"] == 2 and job["next_retry"] is not None and job["state"] == "retrying"
