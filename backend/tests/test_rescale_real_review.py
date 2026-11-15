"""A question that went through the REAL review chain at the old points stays published, ready and counted after it is rescaled."""
import pytest

from app import auto_generation as gen, auto_quality as quality, auto_worker, scoring_rescale
from app.training_spec_v5 import V4_POINTS
from spec_helpers import rubric  # noqa: F401
from test_ai_automation import setup_ai, synthetic_result  # noqa: F401
from test_gap_generation import choice_response, corpus, finish_job  # noqa: F401
from test_gap_generation import plan_single  # noqa: F401
from test_review_gate import run


def old_scale_choice(db, uid, monkeypatch):
    monkeypatch.setattr(gen, "POINTS", dict(V4_POINTS))
    job = plan_single(db, uid)
    response = choice_response(job)
    q = response["response"]["question"]
    q["max_points"] = 4
    q["rubric"] = [dict(description="选出DNA连接酶得4分", points=4, knowledge_tags=[])]
    finish_job(db, job, response)
    auto_worker.dispatch_completed(db)
    review = db.execute("SELECT * FROM ai_jobs WHERE kind='quality_review' ORDER BY id").fetchall()[-1]
    qid = review["payload"]["question_id"]
    quote = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]["evidence"][0]["excerpt"]
    return qid, review, quote


def test_a_really_reviewed_old_scale_question_stays_ready_and_counted_after_rescaling(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setattr(gen, "pinned_science_evidence", lambda content: True)      # the synthetic corpus is not one of the pinned books
    qid, review, quote = old_scale_choice(db, setup_ai, monkeypatch)
    assert run(db, review, quote)
    assert quality.promote(db, qid) and quality.ready(db, qid)
    assert db.execute("SELECT content->>'max_points' AS p FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["p"] == "4"
    assert gen.eligible_reserve_count(db, setup_ai, "biochemistry", "single_choice", points=V4_POINTS) == 1

    monkeypatch.setattr(gen, "POINTS", {**V4_POINTS, "single_choice": 3, "fill_blank": 2, "term": 5, "short_answer": 15, "essay": 25})
    report = scoring_rescale.run(db)
    assert report["rescaled"] == 1 and report["invalidated"] == 0
    assert db.execute("SELECT content->>'max_points' AS p FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["p"] == "3"
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] == "published"
    assert quality.ready(db, qid), "the review is bound to the content as it was reviewed"
    assert quality.retire_old_scoring(db) == 0
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["status"] == "ready"
    assert gen.eligible_reserve_count(db, setup_ai, "biochemistry", "single_choice") == 1


def test_the_worker_planning_pass_survives_a_pile_of_old_scale_work(db, corpus, setup_ai, monkeypatch):
    """plan() runs in ONE transaction whose ValueError is swallowed by the worker: anything that raises there silently undoes the
    rescaling on every run."""
    monkeypatch.setattr(gen, "pinned_science_evidence", lambda content: True)
    qid, review, quote = old_scale_choice(db, setup_ai, monkeypatch)
    assert run(db, review, quote) and quality.promote(db, qid)
    monkeypatch.setattr(gen, "POINTS", {"single_choice": 3, "fill_blank": 2, "term": 5, "short_answer": 15, "essay": 25})
    report = {}
    auto_worker.rescale_scoring(db, report)
    auto_worker.plan(db, setup_ai, target=4, report=report)
    assert db.execute("SELECT content->>'max_points' AS p FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["p"] == "3"
    assert "fill" in report.get("plan_steps", "")


def test_planning_with_old_scale_candidates_still_waiting_for_their_review(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setattr(gen, "pinned_science_evidence", lambda content: True)
    qid, review, quote = old_scale_choice(db, setup_ai, monkeypatch)          # written and checked for free, review still pending
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["status"] == "checking"
    monkeypatch.setattr(gen, "POINTS", {"single_choice": 3, "fill_blank": 2, "term": 5, "short_answer": 15, "essay": 25})
    report = {}
    auto_worker.rescale_scoring(db, report)
    auto_worker.plan(db, setup_ai, target=4, report=report)
    assert db.execute("SELECT content->>'max_points' AS p FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["p"] == "3"
    assert "fill" in report.get("plan_steps", "")
    # its review, written before the change, is still accepted and publishes it
    assert run(db, review, quote) and quality.promote(db, qid) and quality.ready(db, qid)


def odd_fill(db, question_factory, passing_audit, rubric_points, blanks=None):
    from psycopg.types.json import Jsonb
    from two_paper_helpers import add_question

    qid = add_question(db, question_factory, passing_audit, "biochemistry", "fill_blank", points=4)
    db.execute("ALTER TABLE reviewed_questions DISABLE TRIGGER USER")
    content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
    content["rubric"] = [dict(description=f"要点{i}", points=p, knowledge_tags=[]) for i, p in enumerate(rubric_points)]
    if blanks:
        content["blanks"] = [dict(accepted_answers=[a], points=4 / len(blanks)) for a in blanks]
        content["accepted_answers"] = []
        content["stem"] = "、".join(["____"] * len(blanks)) + "是糖酵解的关键物质"
    db.execute("UPDATE reviewed_questions SET content=%s WHERE id=%s", (Jsonb(content), qid))
    db.execute("ALTER TABLE reviewed_questions ENABLE TRIGGER USER")
    return qid


def content_of(db, qid):
    return db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]


def test_no_fill_in_is_left_impossible_the_scoring_points_share_the_two_and_a_half_points(db, question_factory, passing_audit, monkeypatch):
    from app import question_quality as qq
    from app.scoring_rescale import read_in_original_scoring

    five = odd_fill(db, question_factory, passing_audit, [1, 1, 1, 0.5, 0.5])       # N=5: 2.5/5 = 0.5 each
    seven = odd_fill(db, question_factory, passing_audit, [1, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])   # N=7: not an exact share: one point of 2.5
    two_blanks = odd_fill(db, question_factory, passing_audit, [2, 1, 1], blanks=["丙酮酸", "ATP"])   # three points for two blanks
    report = scoring_rescale.run(db)
    assert report["rescaled"] == 3 and "failed" not in report and report["invalidated"] == 0
    assert [c["points"] for c in content_of(db, five)["rubric"]] == [0.5] * 5
    merged = content_of(db, seven)
    assert [c["points"] for c in merged["rubric"]] == [2.5] and merged["max_points"] == 2.5 and merged["rubric"][0]["description"].startswith("要点0；要点1")
    cut = content_of(db, two_blanks)
    assert [c["points"] for c in cut["rubric"]] == [1.25, 1.25] and [b["points"] for b in cut["blanks"]] == [1.25, 1.25]
    for qid in (five, seven, two_blanks):
        qq.Question.model_validate(content_of(db, qid))                    # every converted question is a valid question
    # the review still matches: the content as it was reviewed is read back from the first record
    original = read_in_original_scoring(db, seven, merged)
    assert len(original["rubric"]) == 7 and original["max_points"] == 4 and [c["points"] for c in original["rubric"]][:2] == [1, 0.5]
    assert db.execute("SELECT count(*) AS n FROM auto_question_states WHERE reason='rescale_impossible'").fetchone()["n"] == 0


def test_fill_ins_an_earlier_pass_set_aside_are_taken_back_and_converted(db, question_factory, passing_audit):
    qid = odd_fill(db, question_factory, passing_audit, [1, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])
    db.execute("UPDATE auto_question_states SET status='blocked',reason='rescale_impossible' WHERE question_id=%s", (qid,))
    report = scoring_rescale.run(db)
    assert report["rescaled"] == 1 and "failed" not in report
    state = db.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()
    assert state["status"] == "ready" and state["reason"] is None and content_of(db, qid)["max_points"] == 2.5


def test_one_term_that_cannot_be_converted_does_not_stop_the_others_or_get_retired(db, question_factory, passing_audit, monkeypatch):
    from psycopg.types.json import Jsonb
    from two_paper_helpers import add_question

    good = add_question(db, question_factory, passing_audit, "biochemistry", "term", points=6)
    odd = add_question(db, question_factory, passing_audit, "biochemistry", "term", points=6)
    # a term whose scoring points are worth less than half a point each: nothing in half points can carry them
    db.execute("ALTER TABLE reviewed_questions DISABLE TRIGGER USER")
    content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (odd,)).fetchone()["content"]
    content["rubric"] = [dict(description=f"要点{i}", points=0.5, knowledge_tags=[]) for i in range(12)]
    content["max_points"] = 6
    db.execute("UPDATE reviewed_questions SET content=%s WHERE id=%s", (Jsonb(content), odd))
    db.execute("ALTER TABLE reviewed_questions ENABLE TRIGGER USER")
    report = scoring_rescale.run(db)
    assert report["rescaled"] == 1 and report["failed"] == {"rescale_impossible": 1} and report["invalidated"] == 0
    assert db.execute("SELECT content->>'max_points' AS p FROM reviewed_questions WHERE id=%s", (good,)).fetchone()["p"] == "5"
    state = db.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (odd,)).fetchone()
    assert (state["status"], state["reason"]) == ("blocked", "rescale_impossible")
    assert quality.retire_old_scoring(db) == 0                       # waiting for a person, not voided
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (odd,)).fetchone()["status"] == "blocked"


def test_a_planning_pass_that_raises_no_longer_undoes_the_rescaling(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setattr(gen, "pinned_science_evidence", lambda content: True)
    qid, review, quote = old_scale_choice(db, setup_ai, monkeypatch)
    monkeypatch.setattr(gen, "POINTS", {"single_choice": 3, "fill_blank": 2, "term": 5, "short_answer": 15, "essay": 25})
    report = {}
    auto_worker.rescale_scoring(db, report)                          # its own savepoint, ahead of planning
    assert "rescale" in report and "rescale_error" not in report
    with pytest.raises(ValueError):
        with db.transaction():
            raise ValueError("a planning step failed")               # the worker swallows this and rolls the pass back
    assert db.execute("SELECT content->>'max_points' AS p FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["p"] == "3"


def test_the_stuck_line_says_why_ready_questions_are_not_stock(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setattr(gen, "pinned_science_evidence", lambda content: True)
    qid, review, quote = old_scale_choice(db, setup_ai, monkeypatch)
    assert run(db, review, quote) and quality.promote(db, qid)
    monkeypatch.setattr(gen, "POINTS", {"single_choice": 3, "fill_blank": 2, "term": 5, "short_answer": 15, "essay": 25})
    before = gen.ready_breakdown(db, setup_ai, "biochemistry", "single_choice")
    assert before["points"] == 1 and before["counted"] == 0           # ready, published, but still on the old scale
    scoring_rescale.run(db)
    after = gen.ready_breakdown(db, setup_ai, "biochemistry", "single_choice")
    assert after == dict(points=0, used=0, unpinned=0, unverified=0, counted=1)
    assert "single_choice" not in gen.short_bucket_report(db, setup_ai)       # single choice is no longer a bucket of the daily papers
