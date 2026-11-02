"""The release gate: free program checks + ONE independent AI review, no human sign-off.

Fake model results only. Each test states the property it protects.
"""
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from app import auto_quality as quality, auto_worker, quiz_v2, source_policy, training_sessions
from app.candidate_review import create_packet, snapshot_unit
from spec_helpers import rubric
from app.training_spec_v4 import COUNTS, POINTS, SPECIFICATION, SUBJECTS, default_blueprint
from test_ai_automation import setup_ai, synthetic_result  # noqa: F401  (fixture/helper)
from test_candidate_review import packet as packet_fixture
from test_gap_generation import corpus, review_jobs, tweak, written  # noqa: F401  (fixtures/helpers)


def fresh_choice(db, uid):
    """A single-choice candidate with its ONE pending review job, plus a citable quote."""
    written(db, uid)
    job = review_jobs(db)["review"]
    qid = job["payload"]["question_id"]
    quote = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]["evidence"][0]["excerpt"]
    return qid, job, quote


def run(db, job, quote, **kwargs):
    return quality.accept_round(db, synthetic_result(job, db, quote=quote, **kwargs))


def state(db, qid):
    return db.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()


# ---- release condition -------------------------------------------------------------------


def test_one_passing_review_plus_the_program_checks_publish_a_question(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind IN ('science_review','answer_review')").fetchone()["n"] == 0
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] != "published"
    assert not quality.ready(db, qid)
    identity = db.execute("SELECT passed FROM auto_quality_events WHERE question_id=%s AND phase='identity'", (qid,)).fetchone()
    assert identity["passed"]  # the free program checks already ran, before any model call
    assert run(db, job, quote)
    assert quality.promote(db, qid) and quality.ready(db, qid)
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] == "published"


def test_the_review_needs_a_real_completed_model_call(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    result = synthetic_result(job, db, quote=quote)
    db.execute("UPDATE ai_jobs SET result=jsonb_set(result,'{real_api}','false') WHERE id=%s", (result["id"],))
    stored = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (result["id"],)).fetchone()
    with pytest.raises(ValueError, match="completed_actual_model_call_required"):
        quality.accept_round(db, stored)
    assert not quality.ready(db, qid)


def test_the_review_sees_answer_and_scoring_but_not_the_authors_reasoning(db, corpus, setup_ai):
    qid, job, _ = fresh_choice(db, setup_ai)
    q, refs, style = quality.evidence(db, qid)
    payload = job["payload"]
    assert set(payload) == {"phase", "content_sha256", "question_id", "question", "evidence", "style",
                            "source_level", "schema", "instructions"}
    assert {"answer", "rubric", "options", "stem"} <= set(payload["question"])
    sent = json.dumps(payload, ensure_ascii=False)
    generation = db.execute("SELECT * FROM ai_jobs WHERE kind='generation'").fetchone()
    assert generation["payload"]["anchor_exact_excerpt"] not in {None, ""}
    for author_only in ("anchor_exact_excerpt", "distractor_checks", "选项B与教材所述内容不符", "style_overlap", "source_bibliography"):
        assert author_only not in sent, author_only
    sentinel = dict(answer="唯一答案标记XQ1", rubric=[dict(description="评分标记XQ2", points=q["max_points"], knowledge_tags=[])])
    for kind in ("single_choice", "fill_blank", "term", "short_answer"):
        question = {**q, **sentinel, "question_type": kind, "options": q["options"] if kind == "single_choice" else {}}
        full = json.dumps(quality.review_payload("review", qid, "x", question, refs, style), ensure_ascii=False)
        assert "XQ1" in full and "XQ2" in full, kind  # the reviewer must be able to check them


def test_the_review_job_is_recognised_and_recomputed_exactly(db, corpus, setup_ai):
    qid, job, _ = fresh_choice(db, setup_ai)
    assert quality.job_contract_version(job) == 10 and job["payload"]["instructions"].startswith("Single independent review")
    q, refs, style = quality.evidence(db, qid)
    assert quality.review_payload("review", qid, job["payload"]["content_sha256"], q, refs, style) == job["payload"]


# ---- failures and contradictions block ---------------------------------------------------


def test_a_failing_review_blocks_and_nothing_publishes(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    assert not run(db, job, quote, passed=False)
    assert state(db, qid)["status"] == "blocked"
    assert not quality.ready(db, qid)
    with pytest.raises(ValueError):
        quality.promote(db, qid)
    assert db.execute("SELECT status FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["status"] != "published"


@pytest.mark.parametrize(
    "path,value,reason",
    [
        (["response", "unresolved"], ["a problem the reviewer itself reported"], "unresolved_quality_findings"),
        (["response", "checks", "uniqueness"], False, "unresolved_quality_findings"),
        (["response", "passed"], False, "unresolved_quality_findings"),
        (["response", "independent_choice"], "C", "independent_answer_conflict"),
        (["response", "independent_choice"], None, "independent_answer_conflict"),
    ],
    ids=["passed-but-unresolved", "passed-but-check-false", "checks-true-but-not-passed", "other-option", "no-unique-option"],
)
def test_a_self_contradicting_or_non_unique_verdict_blocks(db, corpus, setup_ai, path, value, reason):
    qid, job, quote = fresh_choice(db, setup_ai)
    review = tweak(db, synthetic_result(job, db, quote=quote), path, value)
    assert not quality.accept_round(db, review)
    assert reason in state(db, qid)["reason"]


def test_a_quote_that_is_not_in_the_sent_text_blocks(db, corpus, setup_ai):
    qid, job, _ = fresh_choice(db, setup_ai)
    assert not run(db, job, "评审凭空写出的教材原文，数据库里根本不存在")
    assert "invented_quote" in state(db, qid)["reason"]


def test_a_rubric_point_left_unsupported_blocks(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    review = tweak(db, synthetic_result(job, db, quote=quote), ["response", "claims"], [])
    with pytest.raises(ValueError):  # a review with no claim at all is not even a valid finding
        quality.accept_round(db, review)
    review = tweak(db, synthetic_result(job, db, quote=quote), ["response", "claims", "0", "criterion_indices"], [])
    assert not quality.accept_round(db, review)
    assert "rubric_not_fully_evidenced" in state(db, qid)["reason"]


# ---- the free program checks still gate everything ----------------------------------------


def test_a_duplicate_of_a_published_question_is_stopped_by_the_free_checks_before_any_model_call(db, corpus, setup_ai):
    first, job, quote = fresh_choice(db, setup_ai)
    assert run(db, job, quote) and quality.promote(db, first)
    old = db.execute("SELECT * FROM candidate_review_packets WHERE question_id=%s", (first,)).fetchone()
    second = create_packet(db, dict(
        candidate_key="duplicate-of-the-first", batch_key="synthetic", question=old["question_snapshot"],
        ai_analysis=old["ai_analysis"],
        style_sources=[dict(unit_id=u["unit_id"], year=u.get("year"), relationship="same style") for u in old["style_snapshot"]],
    ))
    assert second != first
    assert quality.schedule_checks(db, setup_ai, second) == []  # no model call is queued
    assert "duplicate_ready_question" in state(db, second)["reason"] and state(db, second)["status"] == "blocked"
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='quality_review'").fetchone()["n"] == 1


def test_a_citation_that_no_longer_matches_the_source_text_voids_a_finished_approval(db, corpus, setup_ai, monkeypatch):
    qid, job, quote = fresh_choice(db, setup_ai)
    assert run(db, job, quote) and quality.promote(db, qid) and quality.ready(db, qid)
    real = quality.snapshot_unit
    monkeypatch.setattr(quality, "snapshot_unit", lambda conn, uid: {**real(conn, uid), "text": "教材原文已经变成了别的内容"})
    assert not quality.ready(db, qid)


def test_the_database_refuses_to_publish_without_the_current_checks_and_review(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    db.execute("UPDATE auto_question_states SET status='ready' WHERE question_id=%s", (qid,))
    with pytest.raises(RaiseException, match="program checks and one passed independent review"):
        db.execute("UPDATE reviewed_questions SET status='published' WHERE id=%s", (qid,))


def test_events_from_the_retired_two_round_flow_do_not_qualify(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    sha = job["payload"]["content_sha256"]
    for phase in ("science", "answer"):  # exactly what the old gate required
        db.execute(
            "INSERT INTO auto_quality_events(question_id,content_sha256,policy_version,phase,run_key,job_id,evidence_snapshot,result,passed) "
            "VALUES (%s,%s,'auto-evidence/1',%s,%s,%s,'[]','{}',true)", (qid, sha, phase, f"old:{phase}", job["id"]))
    db.execute("UPDATE auto_question_states SET status='ready' WHERE question_id=%s", (qid,))
    assert not quality.ready(db, qid)
    with pytest.raises(RaiseException):
        db.execute("UPDATE reviewed_questions SET status='published' WHERE id=%s", (qid,))


# ---- changing the question voids earlier results ------------------------------------------


def test_an_approved_question_cannot_be_edited_in_place(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    assert run(db, job, quote) and quality.promote(db, qid)
    with pytest.raises(RaiseException, match="immutable"):
        db.execute("UPDATE reviewed_questions SET content=jsonb_set(content,'{stem}',%s) WHERE id=%s",
                   (Jsonb("改动之后的题干：下列哪种酶封闭缺口？"), qid))


def test_approval_is_tied_to_the_exact_content_it_was_given_for(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    assert run(db, job, quote) and quality.promote(db, qid) and quality.ready(db, qid)
    db.execute("UPDATE auto_question_states SET content_sha256=%s WHERE question_id=%s", ("0" * 64, qid))
    assert not quality.ready(db, qid)  # the recorded approval is for different content
    assert quality.promote(db, qid) is False
    assert state(db, qid) == {"status": "blocked", "reason": "incomplete_or_stale_rounds"}


def test_a_failed_review_cannot_be_rerolled_on_the_same_content(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    assert not run(db, job, quote, passed=False)
    again = quality.schedule_checks(db, setup_ai, qid)
    assert [j["id"] for j in again] == [job["id"]]
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='quality_review'").fetchone()["n"] == 1


def test_a_repaired_version_starts_with_no_approval_and_the_old_one_is_retired(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    run(db, job, quote, passed=False)
    content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
    revised = {**content, "stem": "修订之后的题干：下列哪种酶负责封闭相邻片段之间的缺口？"}
    quality.repair(db, qid, revised, "Reworded the stem so that the unique answer is unambiguous.")
    new_id = db.execute("SELECT max(question_id) AS q FROM auto_question_states").fetchone()["q"]
    assert new_id != qid and state(db, qid)["status"] == "retired"
    assert not quality.ready(db, qid) and not quality.ready(db, new_id)
    assert db.execute("SELECT count(*) AS n FROM auto_quality_events WHERE question_id=%s AND phase='review'", (new_id,)).fetchone()["n"] == 0
    assert state(db, new_id)["status"] == "candidate"


# ---- exactly one automatic repair, then discard -------------------------------------------


def repair_jobs(db):
    return db.execute("SELECT * FROM ai_jobs WHERE kind='repair'").fetchall()


def test_a_blocked_question_gets_one_automatic_repair(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    assert quality.MAX_REPAIRS == 1
    assert not run(db, job, quote, passed=False)
    auto_worker.plan(db, setup_ai)
    assert [j["payload"]["question_id"] for j in repair_jobs(db)] == [qid]
    assert state(db, qid)["status"] != "retired"
    content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
    quality.repair(db, qid, {**content, "stem": "第一次修订后的题干：下列哪种酶封闭缺口？"}, "Reworded once, as the single allowed repair.")
    with pytest.raises(ValueError, match="repair_limit"):  # the new version has used the allowance
        new_id = db.execute("SELECT max(question_id) AS q FROM auto_question_states").fetchone()["q"]
        quality.repair(db, new_id, {**content, "stem": "第二次修订后的题干：下列哪种酶封闭缺口？"}, "A second repair is not allowed.")


def test_a_version_that_fails_after_its_repair_is_discarded(db, corpus, setup_ai):
    qid, job, quote = fresh_choice(db, setup_ai)
    run(db, job, quote, passed=False)
    db.execute("DELETE FROM ai_jobs WHERE kind='repair'")
    db.execute("UPDATE auto_question_states SET status='blocked',reason='unresolved_quality_findings',revision_count=1 WHERE question_id=%s", (qid,))
    auto_worker.plan(db, setup_ai)
    assert repair_jobs(db) == []
    row = state(db, qid)
    assert row["status"] == "retired" and row["reason"].startswith("discarded:")
    assert not quality.ready(db, qid)


@pytest.mark.parametrize("reason", ["unresolvable_ocr_or_image_dependency", "source_file_integrity_failed"])
def test_defects_a_rewrite_cannot_fix_are_discarded_not_retried(db, corpus, setup_ai, reason):
    qid, job, quote = fresh_choice(db, setup_ai)
    db.execute("UPDATE auto_question_states SET status='blocked',reason=%s WHERE question_id=%s", (reason, qid))
    auto_worker.plan(db, setup_ai)
    assert repair_jobs(db) == []
    assert state(db, qid)["status"] == "retired"


# ---- no human anywhere on the path --------------------------------------------------------


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
                if kind == "essay":  # four to six scoring points, each bound to evidence
                    q["rubric"] = [dict(r, knowledge_tags=[]) for r in rubric(kind, POINTS[kind])]
                    p["ai_analysis"]["rubric_evidence"] = [deepcopy(p["ai_analysis"]["rubric_evidence"][0]) for _ in q["rubric"]]
                if kind != "single_choice":
                    q.update(options={}, answer="fixture solution")
                if kind == "fill_blank":
                    q.update(stem=q["stem"] + " ____", accepted_answers=["fixture solution"])
                qid = create_packet(db, p)
                (review,) = quality.schedule_checks(db, setup_ai, qid)
                assert quality.accept_round(db, synthetic_result(review, db))
                assert quality.promote(db, qid)
    spec = {"specification": SPECIFICATION, "quotas": default_blueprint()["quotas"]}
    from app.question_quality import Blueprint

    paper = quiz_v2.create_paper(db, setup_ai, Blueprint.model_validate(spec))
    assert (paper["count"], paper["max_points"]) == (60, 300)
    training_sessions.start(db, setup_ai, paper["paper_id"])
    for table in ("question_audits", "candidate_human_decisions", "source_review_events", "exam_annotations"):
        assert db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0, table


def db_first_unit(db, doc):
    return db.execute("SELECT id FROM source_units WHERE document_id=%s ORDER BY ordinal LIMIT 1", (doc,)).fetchone()["id"]
