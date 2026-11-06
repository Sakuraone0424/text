"""Gap-driven AI question writing (step 4). Fake model results only; no API call.

Covers: planning without AI_FULL_CORPUS_ENABLED, hard evidence caps, exam-
frequency chapter weighting, discounting of paragraphs already used, and the
single-choice rule that every wrong option is refuted from the textbook and the
answer round must look for a second correct option.
"""
import random
from contextlib import contextmanager

import pytest
from psycopg.types.json import Jsonb

from app import auto_generation as gen, auto_quality, auto_worker, past_exam_import as pei, sources
from test_ai_automation import setup_ai, synthetic_result  # noqa: F401  (fixtures/helpers)
from test_ocr_only import material
from test_past_exam_import import EXAMS, finish_job

BOOK = {"textbook": "Synthetic", "authors": "Test", "edition": "Test"}
from app import cost_control

PARAGRAPH = (
    "冈崎片段是DNA复制过程中滞后链上不连续合成的短DNA片段。引物酶先合成短的RNA引物，"
    "随后DNA聚合酶沿着引物延伸新生链。当延伸到前一个片段时，核酸酶切除RNA引物，"
    "聚合酶填补缺口，DNA连接酶封闭切口，最终各片段连接成完整连续的DNA链。"
)


@pytest.fixture
def corpus(db, tmp_path, monkeypatch):
    path, meta = material(tmp_path, "biochemistry_lower", content=PARAGRAPH)
    sources.import_source(db, path, meta)
    path, meta = material(tmp_path, "biochemistry_exams", content=EXAMS)
    sources.import_source(db, path, meta)
    for module in (pei, gen):  # synthetic fixtures are not the user's locked bytes
        monkeypatch.setattr(module, "pinned_source_bytes", lambda file_id, sha: True)
        monkeypatch.setattr(module, "source_bibliography", lambda conn, unit: dict(BOOK))
    monkeypatch.delenv("AI_FULL_CORPUS_ENABLED", raising=False)


def generation_jobs(db):
    return db.execute(
        "SELECT * FROM ai_jobs WHERE kind='generation' AND COALESCE(payload->>'origin','')<>'past_exam' ORDER BY id"
    ).fetchall()


# ---- planning ---------------------------------------------------------------


def test_gap_planning_needs_no_corpus_flag_and_stays_within_caps(db, corpus, setup_ai):
    job = gen.plan_gap(db, setup_ai)
    assert job is not None and job["kind"] == "generation"
    p = job["payload"]
    assert (p["subject"], p["question_type"]) == ("biochemistry", "single_choice")
    assert 1 <= len(p["evidence"]) <= pei.MAX_EVIDENCE_UNITS
    assert sum(len(u["text"]) for u in p["evidence"]) <= pei.MAX_EVIDENCE_CHARS
    assert all(u["source_kind"] == "textbook" and u["subject"] == "biochemistry" for u in p["evidence"])
    assert p["anchor_exact_excerpt"] in p["evidence"][0]["text"]
    assert len(job["payload"]["style"]) >= 1 and p["style"][0]["source_kind"] == "exam_transcription"
    assert len(__import__("json").dumps(p, ensure_ascii=False).encode()) <= 24000
    assert "distractor_checks" in p["schema"]["properties"]  # single choice uses the wrapper schema


def test_every_question_type_respects_the_same_caps(db, corpus, setup_ai):
    for kind in ("term", "short_answer", "fill_blank", "single_choice"):
        payload = gen.assemble_payload(
            db, "biochemistry", kind,
            gen.snapshot_unit(db, db.execute(
                "SELECT id FROM source_units WHERE text LIKE %s ORDER BY id LIMIT 1", ("%" + PARAGRAPH,)).fetchone()["id"]),
            "冈崎片段是DNA复制过程中滞后链上不连续合成的短DNA片段。", None,
        )
        assert payload is not None, kind
        assert len(payload["evidence"]) <= pei.MAX_EVIDENCE_UNITS
        assert sum(len(u["text"]) for u in payload["evidence"]) <= pei.MAX_EVIDENCE_CHARS


def test_an_oversized_anchor_paragraph_is_refused_not_trimmed(db, corpus):
    unit = gen.snapshot_unit(db, db.execute("SELECT id FROM source_units WHERE text LIKE %s ORDER BY id LIMIT 1", ("%" + PARAGRAPH,)).fetchone()["id"])
    unit["text"] = "冈" * (pei.MAX_EVIDENCE_CHARS + 1)
    assert gen.assemble_payload(db, "biochemistry", "term", unit, "冈" * 30, None) is None


def patch_worker(db, monkeypatch, run_once=lambda *a, **k: False):
    """Run auto_worker.main() against the test database with every model call faked."""
    calls = []

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    for name, module in (("knowledge", auto_worker.auto_knowledge), ("exams", auto_worker.auto_exams)):
        monkeypatch.setattr(module, "plan_small_batch" if name == "knowledge" else "plan",
                            lambda conn, uid, _n=name: calls.append(_n))
    monkeypatch.setattr(gen, "plan", lambda conn, uid: calls.append("knowledge_generation"))
    monkeypatch.setattr(auto_worker, "get_db", test_db)
    db.execute("UPDATE ai_settings SET max_concurrency=1")  # these tests share one connection: no threads
    monkeypatch.setattr(auto_worker, "tick", lambda: None)
    monkeypatch.setattr(auto_worker, "integrations_tick", lambda: None)
    monkeypatch.setattr(auto_worker.auto_insights, "plan", lambda conn, uid: None)
    monkeypatch.setattr(auto_worker.model_router, "authorized", lambda private=False: None)
    monkeypatch.setattr(auto_worker.ai_jobs, "run_once", run_once)
    monkeypatch.setattr("app.deployment_target.verify_isolated_target", lambda: None)
    monkeypatch.setenv("QUIZ_SERVICE_ROLE", "api")
    for key, value in dict(AI_AUTOMATION_ENABLED="1", QUIZ_DB_SCHEMA="source_staging_x",
                           DEEPSEEK_PRIVATE_DATA_AUTHORIZED="1", AI_QUEUE_AUTOPLAN="1").items():
        monkeypatch.setenv(key, value)
    return calls


def test_worker_never_sweeps_the_corpus_unless_the_flag_is_on(db, corpus, setup_ai, monkeypatch):
    calls = patch_worker(db, monkeypatch)
    auto_worker.main()  # AI_FULL_CORPUS_ENABLED is unset
    assert calls == []
    # ...yet bounded, per-question planning still happened.
    assert generation_jobs(db) and all(j["job_key"].startswith("gap:") for j in generation_jobs(db))
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE payload->>'origin'='past_exam'").fetchone()["n"] > 0

    monkeypatch.setenv("AI_FULL_CORPUS_ENABLED", "1")  # control: the flag really gates the sweeps
    auto_worker.main()
    assert set(calls) == {"knowledge", "exams", "knowledge_generation"}


@pytest.mark.parametrize(
    "value,expected",
    [(None, 2), ("", 2), ("abc", 2), ("1", 1), ("5", 5), ("10", 10), ("11", 10), ("99", 10), ("0", 1), ("-4", 1), ("2.5", 2)],
)
def test_calls_per_run_is_bounded_and_defaults_to_two(value, expected):
    env = {} if value is None else {"AI_CALLS_PER_RUN": value}
    assert auto_worker.calls_per_run(env) == expected


@pytest.mark.parametrize("value,expected", [(None, 2), ("5", 5), ("99", 10), ("junk", 2)])
def test_one_run_makes_no_more_calls_than_the_limit_and_dispatches_between_them(
    db, corpus, setup_ai, monkeypatch, value, expected
):
    seen = []
    patch_worker(db, monkeypatch, run_once=lambda *a, **k: seen.append("call") or True)
    monkeypatch.setattr(auto_worker, "dispatch_completed", lambda conn, **k: seen.append("dispatch"))
    if value is None:
        monkeypatch.delenv("AI_CALLS_PER_RUN", raising=False)
    else:
        monkeypatch.setenv("AI_CALLS_PER_RUN", value)
    monkeypatch.setattr(auto_worker, "MAX_BATCHES", 1)  # the limit is per batch; batching has its own tests
    auto_worker.main()
    assert seen.count("call") == expected
    # every call is followed by a dispatch, so its review jobs can run in the same run
    assert all(seen[i + 1] == "dispatch" for i, x in enumerate(seen) if x == "call")


def test_a_run_stops_early_when_nothing_can_be_claimed(db, corpus, setup_ai, monkeypatch):
    results = iter([True, False, True])
    seen = []
    patch_worker(db, monkeypatch, run_once=lambda *a, **k: seen.append(1) or next(results))
    monkeypatch.setenv("AI_CALLS_PER_RUN", "10")
    auto_worker.main()
    assert len(seen) == 2


# ---- weighting --------------------------------------------------------------


def draws(by_chapter, weights, used, n=3000, seed=1):
    rng = random.Random(seed)
    return [gen.pick_paragraph(by_chapter, weights, used, rng) for _ in range(n)]


POOL = (
    [dict(unit_id=i, chapter_key="A") for i in range(1, 11)]
    + [dict(unit_id=i, chapter_key="B") for i in range(11, 21)]
    + [dict(unit_id=i, chapter_key="C") for i in range(21, 31)]
)
CHAPTER = {u["unit_id"]: u["chapter_key"] for u in POOL}


def share(picks, chapter):
    return sum(CHAPTER[u] == chapter for u in picks) / len(picks)


def test_frequently_examined_chapters_are_chosen_far_more_often():
    used = {}
    by_chapter, weights = gen.chapter_weights(POOL, {"A": 30, "B": 2}, used)
    picks = draws(by_chapter, weights, used)
    assert share(picks, "A") > 0.8  # weights 31 : 3 : 1
    assert 0 < share(picks, "C") < share(picks, "B") < 0.15  # never exclusively the top chapter


def test_chapters_with_no_exam_data_are_still_covered():
    by_chapter, weights = gen.chapter_weights(POOL, {}, {})
    picks = draws(by_chapter, weights, {})
    assert all(0.28 < share(picks, c) < 0.38 for c in "ABC")


def test_a_chapter_that_already_supplied_questions_loses_weight():
    frequency = {"A": 30, "B": 2}
    fresh = gen.chapter_weights(POOL, frequency, {})
    used = {u: 3 for u in range(1, 11)}  # 30 questions already came from chapter A
    spent = gen.chapter_weights(POOL, frequency, used)
    assert share(draws(*fresh, {}), "A") > 0.8
    assert share(draws(spent[0], spent[1], used), "A") < 0.35


def test_a_paragraph_already_used_is_much_less_likely_to_be_chosen_again():
    by_chapter = {"A": [1, 2]}
    weights = {"A": 1}
    used = {1: 1}
    picks = draws(by_chapter, weights, used, n=2000)
    assert picks.count(1) / len(picks) < 0.25  # 0.2 : 1 -> about 17%
    assert gen.pick_paragraph(by_chapter, weights, used, random.Random(0), skip={1, 2}) is None


def test_chapter_frequency_is_built_from_imported_past_exams(db, corpus):
    pei.import_items(db)
    assert pei.index_item_chapters(db) == db.execute("SELECT count(*) AS n FROM past_exam_items").fetchone()["n"]
    frequency = pei.chapter_frequency(db, "biochemistry")
    assert len(frequency) == 1 and next(iter(frequency)).endswith("|第一章 合成测试")
    assert sum(frequency.values()) == 5  # term (2 years) + short answer + fill blank + choice
    assert pei.chapter_frequency(db, "cell_biology") == {}
    assert pei.index_item_chapters(db) == 0  # idempotent


def test_used_paragraphs_count_requests_already_written_from_them(db, corpus, setup_ai):
    job = gen.plan_gap(db, setup_ai)
    unit = job["payload"]["evidence"][0]["unit_id"]
    assert gen.used_paragraphs(db) == {unit: 1}
    second = gen.plan_gap(db, setup_ai)
    assert second is not None and second["id"] != job["id"]
    assert sum(gen.used_paragraphs(db).values()) == 2


def test_bucket_gap_deducts_work_already_on_its_way(db, corpus, setup_ai):
    pei.import_items(db)
    gaps = {(s, k): g for s, k, g in gen.bucket_gaps(db, setup_ai)}
    # an imported past-exam single choice is work on its way now too (past-exam singles come first, AI singles fill the rest)
    assert gaps[("biochemistry", "single_choice")] == 20 - 1
    assert gaps[("biochemistry", "term")] == 8  # an imported past-exam term is throttled (20% cap): it no longer holds back a new one
    assert gaps[("biochemistry", "short_answer")] == 2 - 1
    job = gen.plan_gap(db, setup_ai)
    after = {(s, k): g for s, k, g in gen.bucket_gaps(db, setup_ai)}
    key = (job["payload"]["subject"], job["payload"]["question_type"])
    assert after[key] == gaps[key] - 1


def test_temporary_bank_target_gives_five_per_bucket_forty_in_all(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setenv("AI_BANK_TARGET_PER_BUCKET", "5")
    gaps = {(s, k): g for s, k, g in gen.bucket_gaps(db, setup_ai)}
    assert len(gaps) == 10 and set(gaps.values()) == {5} and sum(gaps.values()) == 50
    pei.import_items(db)
    gaps = {(s, k): g for s, k, g in gen.bucket_gaps(db, setup_ai)}
    assert gaps[("biochemistry", "term")] == 5  # an imported (not yet queued) past-exam term is throttled and not counted
    job = gen.plan_gap(db, setup_ai)
    assert job is not None
    key = (job["payload"]["subject"], job["payload"]["question_type"])
    assert {(s, k): g for s, k, g in gen.bucket_gaps(db, setup_ai)}[key] == gaps[key] - 1


def test_bank_target_unset_or_invalid_keeps_the_two_day_reserve(db, corpus, setup_ai, monkeypatch):
    for value in (None, "", "0", "-3", "abc"):
        if value is None:
            monkeypatch.delenv("AI_BANK_TARGET_PER_BUCKET", raising=False)
        else:
            monkeypatch.setenv("AI_BANK_TARGET_PER_BUCKET", value)
        gaps = {(s, k): g for s, k, g in gen.bucket_gaps(db, setup_ai)}
        assert gaps[("biochemistry", "single_choice")] == 20


def test_a_stocked_bucket_is_not_fed_more_past_exam_work(db, corpus, setup_ai, monkeypatch):
    pei.import_items(db)
    monkeypatch.setenv("AI_BANK_TARGET_PER_BUCKET", "1")
    # one term/short answer item is already awaiting its answer in the same bucket? make the target tiny
    assert cost_control.bucket_supply(db, "biochemistry", "term") == 0
    queued = pei.plan(db, setup_ai)
    assert queued >= 1
    assert cost_control.bucket_supply(db, "biochemistry", "term") + cost_control.bucket_supply(db, "biochemistry", "short_answer") >= 1
    again = pei.plan(db, setup_ai)  # buckets now at target: nothing more queued
    assert again == 0


# ---- single choice: distractor proof + second-correct-option check ----------


def choice_response(job, *, drop=(), invent=None, answer="A"):
    p = job["payload"]
    unit = p["evidence"][0]
    text = unit["text"]
    keep = ["B", "C", "D"] if answer == "A" else [x for x in "ABCD" if x != answer]
    checks = {
        letter: dict(unit_id=unit["unit_id"], excerpt=text[i * 12 : i * 12 + 12] if text else "",
                     why_wrong=f"选项{letter}与教材所述内容不符")
        for i, letter in enumerate(keep)
    }
    for letter in drop:
        checks.pop(letter)
    if invent:
        checks[invent] = dict(checks[invent], excerpt="教材中根本不存在的编造原文")
    options = dict(A="DNA连接酶", B="拓扑异构酶", C="解旋酶", D="引发酶")
    question = dict(
        subject="biochemistry", question_type="single_choice",
        stem="下列哪种酶负责封闭相邻冈崎片段之间的缺口？", options=options, answer=answer,
        accepted_answers=[], blanks=[], difficulty="core", max_points=4, knowledge_tags=[],
        rubric=[dict(description="选出DNA连接酶得4分", points=4, knowledge_tags=[])],
        evidence=[pei.evidence_record(unit, BOOK, p["anchor_exact_excerpt"])],
    )
    return dict(
        real_api=True,
        usage=dict(input_tokens=10, output_tokens=10, cache_read_input_tokens=0),
        response=dict(question=question, distractor_checks=checks),
    )


def written(db, uid, **kwargs):
    job = gen.plan_gap(db, uid)
    finish_job(db, job, choice_response(job, **kwargs))
    auto_worker.dispatch_completed(db)
    return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()


def packets(db):
    return db.execute("SELECT * FROM candidate_review_packets").fetchall()


def test_single_choice_keeps_textbook_proof_for_every_distractor(db, corpus, setup_ai):
    job = written(db, setup_ai)
    assert job["state"] == "succeeded"
    (packet,) = packets(db)
    q = packet["question_snapshot"]
    proof = packet["ai_analysis"]["distractor_evidence"]
    assert set(proof) == {"B", "C", "D"}
    cited = {e["excerpt"] for e in q["evidence"]}
    assert all(p["excerpt"] in cited for p in proof.values())  # reviewers are sent these exact spans
    text = db.execute("SELECT text FROM source_units WHERE id=%s", (q["evidence"][0]["source_unit_id"],)).fetchone()["text"]
    assert all(p["excerpt"] in text for p in proof.values())
    origin = db.execute("SELECT * FROM question_origins WHERE question_id=%s", (packet["question_id"],)).fetchone()
    assert origin["origin"] == "generated" and origin["concept_keys"][0].startswith("unit:")


@pytest.mark.parametrize(
    "kwargs",
    [dict(drop=("C",)), dict(invent="B"), dict(drop=("B", "C", "D"))],
    ids=["one-distractor-unchecked", "invented-quote", "no-distractor-checks"],
)
def test_unproven_distractors_never_become_a_candidate(db, corpus, setup_ai, kwargs):
    job = written(db, setup_ai, **kwargs)
    assert job["state"] == "blocked" and packets(db) == []
    assert db.execute("SELECT count(*) AS n FROM question_origins").fetchone()["n"] == 0


def test_a_bare_question_without_the_wrapper_is_refused(db, corpus, setup_ai):
    job = gen.plan_gap(db, setup_ai)
    bare = choice_response(job)
    bare["response"] = bare["response"]["question"]
    finish_job(db, job, bare)
    auto_worker.dispatch_completed(db)
    assert packets(db) == []


def review_jobs(db):
    """The one independent review job of the newest candidate, keyed like the old rounds."""
    (job,) = db.execute("SELECT * FROM ai_jobs WHERE kind='quality_review' ORDER BY id").fetchall()
    return {"review": job}


def tweak(db, job, path, value):
    db.execute("UPDATE ai_jobs SET result=jsonb_set(result,%s,%s) WHERE id=%s", (path, Jsonb(value), job["id"]))
    return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()


def review_round(db, uid, edit=None):
    """Write a single-choice candidate, answer its one review (optionally edited); return (qid, job)."""
    written(db, uid)
    job = review_jobs(db)["review"]
    qid = job["payload"]["question_id"]
    # A reviewer quotes ONE cited span; the helper's default would paste all of them.
    quote = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]["evidence"][0]["excerpt"]
    result = synthetic_result(job, db, quote=quote)
    return qid, (edit(result) if edit else result)


def test_review_payload_demands_option_checks_only_for_single_choice(db, corpus, setup_ai):
    written(db, setup_ai)
    review = review_jobs(db)["review"]["payload"]
    assert {"option_checks", "other_correct_options"} <= set(review["schema"]["properties"])
    assert "SECOND correct option" in review["instructions"]
    qid = review["question_id"]
    q, refs, style = auto_quality.evidence(db, qid)
    other = auto_quality.review_payload("review", qid, "x", {**q, "question_type": "term", "options": {}}, refs, style)
    assert "option_checks" not in other["schema"]["properties"]
    assert "option_checks" not in auto_quality.Finding.model_json_schema()["properties"]


def test_review_with_every_option_checked_passes_and_makes_it_ready(db, corpus, setup_ai):
    qid, review = review_round(db, setup_ai)
    assert auto_quality.accept_round(db, review)
    assert auto_quality.promote(db, qid) and auto_quality.ready(db, qid)


def test_a_second_correct_option_blocks_the_question(db, corpus, setup_ai):
    def edit(job):
        job = tweak(db, job, ["response", "option_checks", "C", "verdict"], "correct")
        return tweak(db, job, ["response", "other_correct_options"], ["C"])

    qid, review = review_round(db, setup_ai, edit)
    assert not auto_quality.accept_round(db, review)
    row = db.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()
    assert row["status"] == "blocked" and "second_correct_option" in row["reason"]
    with pytest.raises(ValueError, match="not_in_complete_checking_state"):
        auto_quality.promote(db, qid)
    assert not auto_quality.ready(db, qid)


def test_reviewer_cannot_hide_a_second_correct_option_in_one_field(db, corpus, setup_ai):
    def edit(job):  # verdict says C is correct, the declared list says nobody else is
        return tweak(db, job, ["response", "option_checks", "C", "verdict"], "correct")

    qid, review = review_round(db, setup_ai, edit)
    assert not auto_quality.accept_round(db, review)
    reason = db.execute("SELECT reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["reason"]
    assert "second_correct_option" in reason and "option_verdicts_inconsistent" in reason


def test_key_not_confirmed_or_unquoted_option_blocks(db, corpus, setup_ai):
    def edit(job):
        job = tweak(db, job, ["response", "option_checks", "A", "verdict"], "wrong")
        return tweak(db, job, ["response", "option_checks", "D", "excerpt"], "评审凭空写出的原文引用")

    qid, review = review_round(db, setup_ai, edit)
    assert not auto_quality.accept_round(db, review)
    reason = db.execute("SELECT reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["reason"]
    assert "answer_key_not_confirmed_correct" in reason and "invented_quote" in reason


def test_review_without_option_checks_is_not_accepted(db, corpus, setup_ai):
    def edit(job):
        db.execute("UPDATE ai_jobs SET result=result #- '{response,option_checks}' WHERE id=%s", (job["id"],))
        return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()

    _, review = review_round(db, setup_ai, edit)
    with pytest.raises(ValueError):
        auto_quality.accept_round(db, review)


def test_incomplete_option_checks_are_a_review_format_problem_not_a_failed_question(db, corpus, setup_ai):
    def edit(job):
        db.execute("UPDATE ai_jobs SET result=result #- '{response,option_checks,B}' WHERE id=%s", (job["id"],))
        return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()

    qid, review = review_round(db, setup_ai, edit)
    with pytest.raises(auto_quality.ReviewFormatError):
        auto_quality.accept_round(db, review)  # the question is not judged; the worker asks for the review again


# ---- essay (问答) in the 300-point specification ----------------------------


def test_an_essay_request_carries_18_points_and_the_four_to_six_point_rule(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setattr(gen, "bucket_gaps", lambda conn, uid: [("biochemistry", "essay", 1)])
    job = gen.plan_gap(db, setup_ai)
    assert job is not None
    p = job["payload"]
    assert (p["question_type"], p["max_points"]) == ("essay", 18)
    assert "EXACTLY 18 points" in p["instructions"] and "four to six" in p["instructions"]
    assert p["style"] and p["style"][0]["source_kind"] == "exam_transcription"  # nearest genuine style: a short answer


def test_wenda_and_lunshu_sections_are_essays_and_stored_wenda_counts_as_essay():
    from app import transcript_analysis as ta

    assert ta.TYPES["问答题"] == ta.TYPES["问答"] == ta.TYPES["论述题"] == ta.TYPES["论述"] == "essay"
    assert ta.TYPES["简答题"] == "short_answer"  # 简答 stays a short answer
    assert ta.stored_types("essay") == ["essay", "question_answer"] and ta.stored_types("term") == ["term"]
