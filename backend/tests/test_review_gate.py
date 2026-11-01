"""The release gate: two independent AI rounds, nothing else (no human sign-off).

Fake model results only. Each test states the property it protects.
"""
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from app import auto_quality as quality, auto_worker, quiz_v2, source_policy, training_sessions
from app.candidate_review import create_packet, snapshot_unit
from app.training_spec_v2 import COUNTS, POINTS, SPECIFICATION, SUBJECTS, default_blueprint
from test_ai_automation import setup_ai, synthetic_result  # noqa: F401  (fixture/helper)
from test_candidate_review import packet as packet_fixture
from test_gap_generation import corpus, review_jobs, tweak, written  # noqa: F401  (fixtures/helpers)


def fresh_choice(db, uid):
    """A single-choice candidate with its two pending review jobs, plus a citable quote."""
    written(db, uid)
    jobs = review_jobs(db)
    qid = jobs["answer"]["payload"]["question_id"]
    quote = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]["evidence"][0]["excerpt"]
    return qid, jobs, quote


def run(db, job, quote, **kwargs):
    return quality.accept_round(db, synthetic_result(job, db, quote=quote, **kwargs))


def state(db, qid):
    return db.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()


# ---- release condition -------------------------------------------------------------------


def test_only_two_independent_passing_rounds_publish_a_question(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    assert jobs["science"]["id"] != jobs["answer"]["id"]
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] != "published"
    assert not quality.ready(db, qid)
    assert run(db, jobs["science"], quote)
    assert not quality.ready(db, qid)  # one round is not enough, however good
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] != "published"
    assert run(db, jobs["answer"], quote)
    assert quality.promote(db, qid) and quality.ready(db, qid)
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] == "published"


def test_each_round_needs_a_real_completed_model_call(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    science = synthetic_result(jobs["science"], db, quote=quote)
    db.execute("UPDATE ai_jobs SET result=jsonb_set(result,'{real_api}','false') WHERE id=%s", (science["id"],))
    stored = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (science["id"],)).fetchone()
    with pytest.raises(ValueError, match="completed_actual_model_call_required"):
        quality.accept_round(db, stored)
    assert not quality.ready(db, qid)


def test_the_blind_science_round_never_receives_the_answer_or_the_scoring(db, corpus, setup_ai):
    qid, jobs, _ = fresh_choice(db, setup_ai)
    q, refs, style = quality.evidence(db, qid)
    sentinel = dict(answer="唯一答案标记XQ1", accepted_answers=["可接受答案标记XQ3"],
                    rubric=[dict(description="评分标记XQ2", points=q["max_points"], knowledge_tags=[])],
                    blanks=[])
    for kind in ("single_choice", "fill_blank", "term", "short_answer"):
        question = {**q, **sentinel, "question_type": kind, "options": q["options"] if kind == "single_choice" else {}}
        science = quality.review_payload("science", qid, "x", question, refs, style, quality.CURRENT_CONTRACT)
        assert set(science["question"]) == {"subject", "question_type", "stem", "options", "knowledge_tags"}
        sent = json.dumps(science, ensure_ascii=False)
        assert not any(mark in sent for mark in ("XQ1", "XQ2", "XQ3")), kind
        answer = quality.review_payload("answer", qid, "x", question, refs, style, quality.CURRENT_CONTRACT)
        assert "XQ1" in json.dumps(answer, ensure_ascii=False) and "XQ2" in json.dumps(answer, ensure_ascii=False)
    assert "Ambiguity rule" in jobs["science"]["payload"]["instructions"]
    assert "Ambiguity rule" not in jobs["answer"]["payload"]["instructions"]


def test_the_two_rounds_share_no_conclusions_and_the_answer_round_has_no_style(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    science = synthetic_result(jobs["science"], db, quote=quote)
    marker = science["result"]["response"]["rationale"]
    assert marker not in json.dumps(jobs["answer"]["payload"], ensure_ascii=False)
    assert jobs["answer"]["payload"]["style"] == [] and jobs["science"]["payload"]["style"]


def test_new_review_jobs_use_the_current_prompt_contract_without_any_qa_switch(db, corpus, setup_ai, monkeypatch):
    monkeypatch.delenv("QA_AUTOMATION_ENABLED", raising=False)
    _, jobs, _ = fresh_choice(db, setup_ai)
    assert quality.CURRENT_CONTRACT == 8
    for job in jobs.values():
        assert "Output contract:" in job["payload"]["instructions"]
        assert "Boolean polarity" in job["payload"]["instructions"]
        # recomputing from the stored job reproduces exactly what was sent
        q, refs, style = quality.evidence(db, job["payload"]["question_id"])
        again = quality.review_payload(job["payload"]["phase"], job["payload"]["question_id"],
                                       job["payload"]["content_sha256"], q, refs, style, quality.job_contract_version(job))
        assert again == job["payload"]
    assert quality.job_contract_version(jobs["science"]) == 8  # the ambiguity rule is a science-round clause


def test_a_job_issued_under_an_older_contract_is_still_judged_as_it_was_sent():
    old = {"settings_snapshot": {}, "payload": {"instructions": "plain"}}
    seven = {"settings_snapshot": {}, "payload": {"instructions": "x Output contract: y"}}
    assert quality.job_contract_version(old) == 1 and quality.job_contract_version(seven) == 7


# ---- failures and conflicts block ------------------------------------------------------


@pytest.mark.parametrize("failing,passing", [("science", "answer"), ("answer", "science")])
def test_one_failing_round_blocks_even_when_the_other_passes(db, corpus, setup_ai, failing, passing):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    assert not run(db, jobs[failing], quote, passed=False)
    assert state(db, qid)["status"] == "blocked"
    run(db, jobs[passing], quote)
    assert not quality.ready(db, qid)
    with pytest.raises(ValueError):
        quality.promote(db, qid)
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] != "published"


def test_the_blind_round_choosing_another_option_conflicts_with_the_key(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    science = synthetic_result(jobs["science"], db, quote=quote)
    science = tweak(db, science, ["response", "independent_choice"], "C")
    assert not quality.accept_round(db, science)
    assert "independent_answer_conflict" in state(db, qid)["reason"]


def test_a_blind_round_that_cannot_pick_one_option_blocks(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    science = synthetic_result(jobs["science"], db, quote=quote)
    db.execute("UPDATE ai_jobs SET result=jsonb_set(result,'{response,independent_choice}','null') WHERE id=%s", (science["id"],))
    science = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (science["id"],)).fetchone()
    assert not quality.accept_round(db, science)
    assert "independent_answer_conflict" in state(db, qid)["reason"]


@pytest.mark.parametrize("phase", ["science", "answer"])
def test_a_quote_that_is_not_in_the_sent_text_blocks(db, corpus, setup_ai, phase):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    assert not run(db, jobs[phase], "评审凭空写出的教材原文，数据库里根本不存在")
    assert "invented_quote" in state(db, qid)["reason"]


def test_a_citation_that_no_longer_matches_the_source_text_voids_a_finished_approval(db, corpus, setup_ai, monkeypatch):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    run(db, jobs["science"], quote), run(db, jobs["answer"], quote)
    assert quality.promote(db, qid) and quality.ready(db, qid)
    real = quality.snapshot_unit
    monkeypatch.setattr(quality, "snapshot_unit", lambda conn, uid: {**real(conn, uid), "text": "教材原文已经变成了别的内容"})
    assert not quality.ready(db, qid)


def test_a_failed_round_cannot_be_rerolled_on_the_same_content(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    assert not run(db, jobs["science"], quote, passed=False)
    again = quality.schedule_checks(db, setup_ai, qid)
    assert {j["id"] for j in again} == {jobs["science"]["id"], jobs["answer"]["id"]}
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind IN ('science_review','answer_review')").fetchone()["n"] == 2


# ---- changing the question voids earlier results ---------------------------------------


def test_an_approved_question_cannot_be_edited_in_place(db, corpus, setup_ai):
    from psycopg.errors import RaiseException

    qid, jobs, quote = fresh_choice(db, setup_ai)
    run(db, jobs["science"], quote), run(db, jobs["answer"], quote)
    assert quality.promote(db, qid)
    with pytest.raises(RaiseException, match="immutable"):
        db.execute("UPDATE reviewed_questions SET content=jsonb_set(content,'{stem}',%s) WHERE id=%s",
                   (Jsonb("改动之后的题干：下列哪种酶封闭缺口？"), qid))


def test_approval_is_tied_to_the_exact_content_it_was_given_for(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    run(db, jobs["science"], quote), run(db, jobs["answer"], quote)
    assert quality.promote(db, qid) and quality.ready(db, qid)
    db.execute("UPDATE auto_question_states SET content_sha256=%s WHERE question_id=%s", ("0" * 64, qid))
    assert not quality.ready(db, qid)  # the recorded approval is for different content
    assert quality.promote(db, qid) is False
    assert state(db, qid) == {"status": "blocked", "reason": "incomplete_or_stale_rounds"}


def test_a_repaired_version_starts_with_no_approval_and_the_old_one_is_retired(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    run(db, jobs["science"], quote, passed=False)
    content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
    revised = {**content, "stem": "修订之后的题干：下列哪种酶负责封闭相邻片段之间的缺口？"}
    new = quality.repair(db, qid, revised, "Reworded the stem so that the unique answer is unambiguous.")
    assert new is None or new != qid
    new_id = db.execute("SELECT question_id FROM auto_question_states WHERE reason IS NULL OR status<>'retired' ORDER BY question_id DESC LIMIT 1").fetchone()["question_id"]
    assert new_id != qid
    assert state(db, qid)["status"] == "retired" and not quality.ready(db, qid)
    assert not quality.ready(db, new_id)
    assert db.execute("SELECT count(*) AS n FROM auto_quality_events WHERE question_id=%s AND phase IN ('science','answer')", (new_id,)).fetchone()["n"] == 0
    assert state(db, new_id)["status"] == "candidate"


# ---- bounded automatic retries ---------------------------------------------------------


def repair_jobs(db):
    return db.execute("SELECT * FROM ai_jobs WHERE kind='repair'").fetchall()


def test_a_blocked_question_gets_a_limited_number_of_automatic_repairs(db, corpus, setup_ai):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    assert not run(db, jobs["answer"], quote, passed=False)
    auto_worker.plan(db, setup_ai)
    assert [j["payload"]["question_id"] for j in repair_jobs(db)] == [qid]
    db.execute("DELETE FROM ai_jobs WHERE kind='repair'")
    db.execute("UPDATE auto_question_states SET status='blocked',revision_count=2 WHERE question_id=%s", (qid,))
    auto_worker.plan(db, setup_ai)
    assert repair_jobs(db) == []  # budget spent: it stays blocked
    with pytest.raises(ValueError, match="repair_limit"):
        quality.repair(db, qid, {}, "x" * 30)


@pytest.mark.parametrize("reason", ["unresolvable_ocr_or_image_dependency", "source_file_integrity_failed"])
def test_defects_a_rewrite_cannot_fix_are_never_retried(db, corpus, setup_ai, reason):
    qid, jobs, quote = fresh_choice(db, setup_ai)
    db.execute("UPDATE auto_question_states SET status='blocked',reason=%s WHERE question_id=%s", (reason, qid))
    auto_worker.plan(db, setup_ai)
    assert repair_jobs(db) == []


# ---- no human anywhere on the path -----------------------------------------------------


def test_a_full_60_question_bank_reaches_a_paper_with_no_human_record(db, tmp_path, setup_ai, monkeypatch):
    from test_ocr_only import material
    from app import sources

    base = packet_fixture.__wrapped__(db, tmp_path)
    monkeypatch.setattr(source_policy, "pinned_science_evidence", lambda content: True)
    for subject in SUBJECTS:
        template = deepcopy(base)
        if subject == "cell_biology":
            path, meta = material(tmp_path, "cell_biology", content="Synthetic cell mechanism evidence for isolated regression only")
            unit = db_first_unit(db, sources.import_source(db, path, meta))
            u = snapshot_unit(db, unit)
            template["question"]["evidence"][0].update(
                file_id=u["file_id"], source_sha256=u["source_sha256"], ocr_page=u["ocr_page"], paragraph_id=unit,
                source_unit_id=unit, chapter=u["chapter"], locator=f"OCR:{u['ocr_page']}:UNIT:{unit}", excerpt=u["text"])
            path, meta = material(tmp_path, "cell_biology_exams")
            doc = sources.import_source(db, path, meta)
            template["style_sources"][0]["unit_id"] = db.execute(
                "SELECT id FROM source_units WHERE document_id=%s AND text LIKE '1. Synthetic%%' ORDER BY ordinal LIMIT 1", (doc,)).fetchone()["id"]
            template["ai_analysis"]["rubric_evidence"][0]["unit_ids"] = [unit]
        for kind, count in COUNTS.items():
            for _ in range(count):
                p = deepcopy(template)
                p["candidate_key"] = str(uuid4())
                q = p["question"]
                q.update(subject=subject, question_type=kind, max_points=POINTS[kind],
                         stem=f"{uuid4()} {uuid4()} Synthetic isolated question")
                q["rubric"][0]["points"] = POINTS[kind]
                if kind != "single_choice":
                    q.update(options={}, answer="fixture solution")
                if kind == "fill_blank":
                    q.update(stem=q["stem"] + " ____", accepted_answers=["fixture solution"])
                qid = create_packet(db, p)
                for job in quality.schedule_checks(db, setup_ai, qid):
                    assert quality.accept_round(db, synthetic_result(job, db))
                assert quality.promote(db, qid)
    spec = {"specification": SPECIFICATION, "quotas": default_blueprint()["quotas"]}
    from app.question_quality import Blueprint

    paper = quiz_v2.create_paper(db, setup_ai, Blueprint.model_validate(spec))
    assert (paper["count"], paper["max_points"]) == (60, 150)
    training_sessions.start(db, setup_ai, paper["paper_id"])
    for table in ("question_audits", "candidate_human_decisions", "source_review_events", "exam_annotations"):
        assert db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0, table


def db_first_unit(db, doc):
    return db.execute("SELECT id FROM source_units WHERE document_id=%s ORDER BY ordinal LIMIT 1", (doc,)).fetchone()["id"]
