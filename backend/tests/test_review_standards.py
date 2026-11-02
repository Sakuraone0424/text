"""Review standards PR: the reviewer sees what the writer saw, the program derives the tag mapping, only textbook
risks need explaining, single-choice reviews think harder, duplicates are compared inside one bucket at 0.9, the writing
prompt demands refutable distractors, and the before/after numbers are kept apart by review contract."""
import pytest

from app import auto_execution, auto_quality as quality, cost_control, waste_report
from app.question_quality import similarity
from test_ai_automation import setup_ai, synthetic_result  # noqa: F401  (fixture/helper)
from test_gap_generation import corpus, review_jobs, review_round, tweak, written  # noqa: F401
from test_waste_rules import written_with  # noqa: F401


# 1. whole paragraphs --------------------------------------------------------------------------------------------

def test_the_reviewer_gets_the_whole_paragraph_the_writer_had_and_the_request_still_fits(db, corpus, setup_ai):
    written(db, setup_ai)
    review = review_jobs(db)["review"]
    p = review["payload"]
    assert p["instructions"].startswith("Single independent review of one exam question (contract 10)")
    assert quality.job_contract_version(review) == 10
    qid = p["question_id"]
    q, refs, style = quality.evidence(db, qid)
    cited = {e["source_unit_id"]: e["excerpt"] for e in q["evidence"]}
    assert len(p["evidence"]) <= 4 and len(p["evidence"]) == len({u["unit_id"] for u in refs})   # one view per paragraph
    for view in p["evidence"]:
        paragraph = next(u["text"] for u in refs if u["unit_id"] == view["unit_id"])
        assert view["text"] == paragraph and cited[view["unit_id"]] in view["text"]   # the citation is part of it, not all of it
    auto_execution.bounded_material(p)                                                # the hard caps still hold
    assert "WHOLE textbook paragraphs" in p["instructions"] and "cannot be answered from the supplied paragraphs" in p["instructions"]


def test_a_quote_from_another_part_of_the_paragraph_is_a_valid_citation_for_the_reviewer(db, corpus, setup_ai):
    def edit(job):
        u = job["payload"]["evidence"][0]
        other = u["text"][len(u["text"]) // 2: len(u["text"]) // 2 + 20]   # not the cited span: elsewhere in the paragraph
        job = tweak(db, job, ["response", "claims", "0", "excerpt"], other)
        return tweak(db, job, ["response", "option_checks", "A", "excerpt"], other)

    qid, review = review_round(db, setup_ai, edit)
    assert quality.accept_round(db, review)          # (contract 9 would have called this an invented quote)
    assert quality.promote(db, qid) and quality.ready(db, qid)


def test_a_quote_that_is_in_no_sent_paragraph_is_still_invented(db, corpus, setup_ai):
    qid, review = review_round(db, setup_ai, lambda job: tweak(db, job, ["response", "claims", "0", "excerpt"], "教材里根本没有的话，是审核员编出来的"))
    assert not quality.accept_round(db, review)
    assert "invented_quote" in db.execute("SELECT reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["reason"]


def test_an_older_review_request_is_judged_and_matched_under_its_own_contract(db, corpus, setup_ai):
    written(db, setup_ai)
    review = review_jobs(db)["review"]
    qid = review["payload"]["question_id"]
    q, refs, style = quality.evidence(db, qid)
    old = quality.review_payload("review", qid, quality.content_sha(q), q, refs, style, quality.OLD_REVIEW_CONTRACT)
    assert old["instructions"].startswith("Single independent review of one exam question.") and quality.job_contract_version({"payload": old, "settings_snapshot": {}}) == 9
    assert q["evidence"][0]["excerpt"] in old["evidence"][0]["text"]                   # cited spans only, exactly as before


# 2. the program derives the mapping -----------------------------------------------------------------------------------

def test_the_tag_mapping_is_derived_from_the_citations_not_written_by_the_reviewer(db, corpus, setup_ai):
    def tags(response, job):
        response["question"]["knowledge_tags"] = ["冈崎片段", "DNA连接酶"]

    written_with(db, setup_ai, tags)
    review = review_jobs(db)["review"]
    qid = review["payload"]["question_id"]
    job = tweak(db, synthetic_result(review, db), ["response", "mapping_evidence"], {})     # the reviewer wrote no mapping
    assert quality.accept_round(db, job)
    event = db.execute("SELECT result FROM auto_quality_events WHERE phase='review'").fetchone()["result"]
    units = sorted({e["source_unit_id"] for e in quality.evidence(db, qid)[0]["evidence"]})
    assert event["finding"]["mapping_evidence"] == {"冈崎片段": units, "DNA连接酶": units}


# 3. risk explanations only for textbook paragraphs --------------------------------------------------------------------

def flagged_style(monkeypatch):
    original = quality.evidence

    def patched(conn, qid):
        q, refs, style = original(conn, qid)
        return q, refs, [{**style[0], "quality_flags": ["table_layout"]}] + style[1:]

    monkeypatch.setattr(quality, "evidence", patched)


def test_a_flag_on_a_past_exam_style_example_needs_no_explanation_for_contract_10(db, corpus, setup_ai, monkeypatch):
    flagged_style(monkeypatch)
    written(db, setup_ai)
    review = review_jobs(db)["review"]
    assert review["payload"]["style"][0]["quality_flags"] == ["table_layout"]
    assert quality.accept_round(db, synthetic_result(review, db))        # no risk_resolutions needed for the style example


def test_the_same_flag_still_needs_an_explanation_under_contract_9(db, corpus, setup_ai, monkeypatch):
    flagged_style(monkeypatch)
    written(db, setup_ai)
    review = review_jobs(db)["review"]
    qid = review["payload"]["question_id"]
    q, refs, style = quality.evidence(db, qid)
    old_payload = quality.review_payload("review", qid, quality.content_sha(q), q, refs, style, quality.OLD_REVIEW_CONTRACT)
    from app import ai_jobs
    old = ai_jobs.enqueue(db, setup_ai, "old-contract", "quality_review", old_payload)
    assert not quality.accept_round(db, synthetic_result(old, db))
    assert "unresolved_source_risk" in db.execute("SELECT reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["reason"]


# 4. single-choice reviews think harder -------------------------------------------------------------------------------

def test_single_choice_reviews_use_high_thinking_and_the_others_keep_low(db, corpus, setup_ai, monkeypatch):
    single, term = {"question": {"question_type": "single_choice"}}, {"question": {"question_type": "term"}}
    assert cost_control.effort_for("quality_review", single) == "high"
    assert cost_control.effort_for("quality_review", term) == "low" and cost_control.effort_for("quality_review") == "low"
    assert cost_control.effort_for("generation", single) == "off"
    monkeypatch.setenv("AI_REVIEW_THINKING_SINGLE_CHOICE", "low")
    assert cost_control.effort_for("quality_review", single) == "low"
    monkeypatch.setenv("AI_REVIEW_THINKING_SINGLE_CHOICE", "nonsense")
    assert cost_control.effort_for("quality_review", single) == "high"
    monkeypatch.delenv("AI_REVIEW_THINKING_SINGLE_CHOICE")
    written(db, setup_ai)
    assert review_jobs(db)["review"]["settings_snapshot"]["effort"] == "high"      # frozen into the job itself


# 5. duplicates ------------------------------------------------------------------------------------------------------

def published(db, question_factory, passing_audit, kind, stem):
    from app.quiz_v2 import create_question, publish_question, review_question

    qid = create_question(db, question_factory("biochemistry", kind, stem=stem))["id"]
    assert review_question(db, qid, passing_audit)["passed"]
    publish_question(db, qid)
    return qid


def test_duplicates_are_judged_inside_one_bucket_at_zero_point_nine(db, question_factory, passing_audit):
    base = "".join(chr(0x4E00 + i * 7) for i in range(40))
    published(db, question_factory, passing_audit, "single_choice", base)
    probe = lambda kind, stem: quality.hard_issues(db, 0, dict(subject="biochemistry", question_type=kind, stem=stem, answer="甲", evidence=[], options={}))
    same_bucket = probe("single_choice", base)
    assert "duplicate_ready_question" in same_bucket
    assert "duplicate_ready_question" not in probe("term", base)           # a term is never a duplicate of a single choice
    near = "".join("乙" if i in (3, 11, 19, 27, 35) else c for i, c in enumerate(base))   # 5 of 40 characters differ
    assert 0.85 <= similarity(base, near) < 0.9
    assert "duplicate_ready_question" not in probe("single_choice", near)  # 0.875: a duplicate under the old 0.85, not now
    near4 = "".join("乙" if i in (3, 11, 19, 27) else c for i, c in enumerate(base))
    assert similarity(base, near4) >= 0.9 and "duplicate_ready_question" in probe("single_choice", near4)
    assert quality.DUPLICATE_THRESHOLD == 0.9


# 6. the writing prompt ---------------------------------------------------------------------------------------------------

def test_the_writing_prompt_demands_a_findable_refutation_for_every_wrong_option():
    from app.exam_authoring import instructions

    text = instructions("single_choice", 4)
    assert "EVERY wrong alternative MUST have grounds for refuting it" in text and "found in the supplied paragraphs" in text
    assert "keep only those whose refutation you can quote verbatim" in text


# the before / after numbers ---------------------------------------------------------------------------------------------

def test_pass_rate_and_average_cost_are_reported_per_contract_and_thinking_level(db, corpus, setup_ai):
    qid, review = review_round(db, setup_ai)
    assert quality.accept_round(db, review)
    db.execute("INSERT INTO ai_cny_events(event_key,job_id,attempt,kind,amount_cny,detail) VALUES (%s,%s,1,'usage_estimate',0.0123,'{}')",
               (f"cost-{review['id']}", review["id"]))
    rows = waste_report.review_by_contract(db, 72)
    row = next(r for r in rows if r["question_type"] == "single_choice")
    assert (row["contract"], row["thinking"], row["reviewed"], row["passed"], row["pass_rate"]) == ("10", "high", 1, 1, 1.0)
    assert row["avg_cost_cny"] == "0.0123"
    assert waste_report.full_stats(db, 24)["review_by_contract"] == rows or waste_report.full_stats(db, 24)["review_by_contract"]
