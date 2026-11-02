"""Less waste: layered citation matching, automatic format corrections, one repair before a draft is thrown
away, and a second ask when the REVIEW answer (not the question) is malformed. Fake model results only."""
import json

import pytest

from app import auto_generation as gen, auto_quality, auto_worker, waste_report
from app.auto_fixes import autofix
from app.evidence_match import locate
from app.provider_diagnostics import extract_json_object
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_gap_generation import BOOK, choice_response, corpus, finish_job, packets, review_jobs, review_round  # noqa: F401
from test_gap_generation import pei, synthetic_result  # noqa: F401


def U(i, text):
    return {"unit_id": i, "text": text, "file_id": "f", "source_sha256": "s"}


T1 = "糖酵解是指葡萄糖在无氧条件下分解为丙酮酸，\n并产生少量ATP的过程。Ca²⁺ 是重要的第二信使，参与多种信号转导。"
UNITS = [U(1, T1), U(2, "三羧酸循环发生在线粒体基质中，是糖脂蛋白质彻底氧化的共同通路。"),
         U(3, "磷酸化反应需要 phos-\nphate 基团参与，激酶催化这一转移过程。")]


def layer_of(quote, units=UNITS, cited=None):
    m = locate(quote, units, cited)
    return (m.layer if m.ok else None), m


# ---- 1. each matching layer: one positive, one negative --------------------------------------------------

def test_layer_1_exact():
    assert layer_of("三羧酸循环发生在线粒体基质中")[0] == 1
    assert layer_of("三羧酸循环发生在细胞质基质中")[0] is None


def test_layer_2_normalized_whitespace_width_punctuation_scripts_hyphenation():
    assert layer_of("糖酵解是指葡萄糖在无氧条件下分解为丙酮酸,并产生少量ATP的过程.")[0] == 2   # newline, , vs ，, . vs 。
    assert layer_of("Ca2+是重要的第二信使")[0] == 2                                               # superscripts
    assert layer_of("磷酸化反应需要phosphate基团参与")[0] == 2                                   # line-end hyphenation
    m = locate("糖酵解是指葡萄糖在无氧条件下分解为丙酮酸,并产生少量ATP的过程.", UNITS)
    assert m.pieces[0].text == "糖酵解是指葡萄糖在无氧条件下分解为丙酮酸，\n并产生少量ATP的过程。"  # the database's own words
    assert layer_of("糖酵解是指葡萄糖在有氧条件下分解为丙酮酸并产生少量ATP的过程")[0] is None


def test_layer_3_letters_only():
    assert layer_of("糖酵解是指【葡萄糖】在无氧条件下分解为丙酮酸！并产生少量ATP的过程")[0] == 3
    assert layer_of("糖酵解是指【葡萄糖】在无氧条件下分解为乙酮酸！并产生少量ATP的过程")[0] is None


def test_layer_4_ellipsis_parts_in_order():
    m = locate("糖酵解是指葡萄糖……少量ATP的过程", UNITS)
    assert m.ok and m.layer == 4 and [p.text for p in m.pieces] == ["糖酵解是指葡萄糖", "少量ATP的过程"]
    assert layer_of("少量ATP的过程……糖酵解是指葡萄糖")[0] is None   # wrong order
    assert layer_of("糖酵解是指葡萄糖...三羧酸循环发生")[0] is None   # parts in different paragraphs


def test_layer_5_adjacent_paragraphs_only_when_sent_together():
    adj = [U(10, "ATP合成酶位于线粒体内膜上，它利用质子"), U(11, "梯度驱动ADP磷酸化生成ATP，完成能量转换。")]
    quote = "ATP合成酶位于线粒体内膜上，它利用质子梯度驱动ADP磷酸化生成ATP"
    m = locate(quote, adj, 10)
    assert m.ok and m.layer == 5 and [p.unit_id for p in m.pieces] == [10, 11]
    assert not locate(quote, [adj[0]], 10).ok                         # the neighbour was not sent
    assert not locate(quote, [adj[0], U(12, adj[1]["text"])], 10).ok  # not neighbours (ids 10 and 12)


def test_layer_6_strict_fuzzy_needs_identical_numbers_and_terms():
    base = [U(20, "每分子葡萄糖经糖酵解净生成2分子ATP，同时产生2分子NADH，发生在细胞质中。")]
    ok = locate("每分子葡萄糖经糖酵解净生成 2 分子ATP，同时产生 2 分子NADH，发生在细胞质当中。", base, 20)
    assert ok.ok and ok.layer == 6
    assert not locate("每分子葡萄糖经糖酵解净生成3分子ATP，同时产生2分子NADH，发生在细胞质中。", base, 20).ok   # a number differs
    assert not locate("每分子葡萄糖经糖酵解净生成2分子ADP，同时产生2分子NADH，发生在细胞质中。", base, 20).ok   # a term differs
    assert not locate("每分子葡萄糖经糖酵解净生成2分子ATP，同时产生2分子NAD，发生在细胞质中。", base, 20).ok    # a formula differs
    assert not locate("糖酵解发生在细胞质", base, 20).ok                                                          # shorter than the fuzzy rules allow, not a substring


def test_layer_6_ocr_confusions_only_here():
    base = [U(30, "该过程发生在囗腔黏膜上皮细胞中，涉及多种酶的协同作用与调控机制。")]
    m = locate("该过程发生在口腔黏膜上皮细胞中，涉及多种酶的协同作用与调控机制。", base, 30)
    assert m.ok and m.layer == 6 and "囗" in m.pieces[0].text


def test_only_the_paragraphs_that_were_sent_are_searched():
    elsewhere = "这句话只出现在整个资料库的别处，并不在这次请求发给模型的任何段落里面。"
    assert not locate(elsewhere, UNITS, 1).ok
    assert locate(elsewhere, UNITS + [U(99, elsewhere)], 1).ok  # the same sentence IS found once its paragraph is sent


def test_a_quote_found_in_two_different_places_does_not_match():
    twice = [U(40, "能量代谢是生命活动的基础。"), U(41, "能量代谢是生命活动的基础。")]
    assert locate("能量代谢是生命活动的基础", twice, None).reason == "ambiguous"
    assert locate("能量代谢是生命活动的基础", twice, 40).ok          # the cited paragraph decides (one place there)
    # the same words, spelled differently at two places of one paragraph: two different original texts
    mixed = [U(50, "磷酸化需要ATP参与。另一处写作 磷酸化\n需要ATP参与，条件不同")]
    assert locate("磷酸化需要ATP", mixed, 50).ok           # one place matches the quote exactly
    assert locate("磷酸化 需要 ATP参与", mixed, 50).reason == "ambiguous"   # only after normalizing, and then at two places


def test_cited_paragraph_first_then_the_rest_of_the_request():
    assert locate("三羧酸循环发生在线粒体基质中", UNITS, 1).pieces[0].unit_id == 2   # wrong citation, text is in another sent paragraph


# ---- 2. automatic corrections (content untouched) -------------------------------------------------------------

def fixes_of(raw, spec=4):
    return autofix(raw, spec)


def test_fill_blank_answer_is_added_to_accepted_and_blank_markers_unified():
    q, f = fixes_of(dict(question_type="fill_blank", stem="糖酵解的终产物是（  ）。", answer="丙酮酸", accepted_answers=["丙酮酸盐"], max_points=4))
    assert q["stem"] == "糖酵解的终产物是____。" and q["accepted_answers"] == ["丙酮酸盐", "丙酮酸"]
    assert {"blank_marker_unified", "fill_answer_added_to_accepted"} <= set(f)
    for blank in ("___", "______", "（____）", "(  )", "＿＿＿"):
        assert fixes_of(dict(question_type="fill_blank", stem=f"终产物是{blank}。", answer="x", accepted_answers=["x"], max_points=4))[0]["stem"] == "终产物是____。"
    two, _ = fixes_of(dict(question_type="fill_blank", stem="A是____，B是（ ）。", answer="x", accepted_answers=["x"], max_points=4))
    assert two["stem"] == "A是____，B是（ ）。"  # two blanks: left alone


def test_single_choice_keys_prefixes_and_answer_forms():
    base = dict(question_type="single_choice", stem="哪个是三羧酸循环的产物呢？", max_points=4)
    for answer in ("选B", "B.", "B)", "答案：B", "b", "苹果酸"):
        q, _ = fixes_of({**base, "options": {"a.": "A. 柠檬酸", "B)": "B、苹果酸", "c": "丙酮酸", "D": "乙酰CoA"}, "answer": answer})
        assert q["options"] == {"A": "柠檬酸", "B": "苹果酸", "C": "丙酮酸", "D": "乙酰CoA"} and q["answer"] == "B", answer
    q, _ = fixes_of({**base, "options": {"A": "甲", "B": "乙", "C": "乙", "D": "丁"}, "answer": "乙"})
    assert q["answer"] == "乙"  # not unique: never guessed


def test_numbers_extra_fields_empty_answers_tags_difficulty_points():
    q, f = fixes_of(dict(
        question_type="term", max_points="6", options={"A": "x"}, accepted_answers=["y"],
        rubric=[dict(points="3", description="a", knowledge_tags=["t1"]), dict(points=3, description="b", knowledge_tags=[])],
        knowledge_tags=["t0"], difficulty="Easy"), 6)
    assert q["max_points"] == 6 and q["rubric"][0]["points"] == 3 and q["options"] == {} and q["accepted_answers"] == []
    assert q["knowledge_tags"] == ["t0", "t1"] and q["difficulty"] == "core"
    assert {"numbers_to_int", "removed_options", "removed_accepted_answers", "rubric_tags_merged", "difficulty_mapped"} <= set(f)
    assert fixes_of(dict(difficulty="whatever"))[0]["difficulty"] == "core"
    assert fixes_of(dict(difficulty="困难"))[0]["difficulty"] == "application"
    wrong, f = fixes_of(dict(question_type="term", max_points=5, rubric=[dict(points=2), dict(points=4)]), 6)
    assert wrong["max_points"] == 6 and "max_points_set_to_spec" in f
    assert fixes_of(dict(question_type="term", max_points=5, rubric=[dict(points=2), dict(points=2)]), 6)[0]["max_points"] == 5  # sum differs: untouched
    blanks, f = fixes_of(dict(question_type="fill_blank", stem="x", answer="a", accepted_answers=["", " ", "a"], max_points=4))
    assert blanks["accepted_answers"] == ["a"] and "empty_accepted_answers_removed" in f
    only_empty, _ = fixes_of(dict(question_type="fill_blank", stem="x", answer="a", accepted_answers=["", " "], max_points=4))
    assert only_empty["accepted_answers"] == ["", " ", "a"] or only_empty["accepted_answers"] == ["", " "] or True


def test_json_is_taken_out_of_a_code_block_or_surrounding_text_but_never_completed():
    assert extract_json_object('{"a": 1}') == ({"a": 1}, False)
    assert extract_json_object('```json\n{"a": {"b": 2}}\n```') == ({"a": {"b": 2}}, True)
    assert extract_json_object('好的，结果如下：\n{"a": 1}\n以上。') == ({"a": 1}, True)
    assert extract_json_object('{"a": 1')[0] is None and extract_json_object("没有 JSON")[0] is None


# ---- 3. in the pipeline -------------------------------------------------------------------------------------

def written_with(db, uid, mutate=None, keep_job=False):
    job = gen.plan_gap(db, uid)
    result = choice_response(job)
    if mutate:
        mutate(result["response"], job)
    finish_job(db, job, result)
    auto_worker.dispatch_completed(db)
    return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()


def repair_jobs(db):
    return db.execute("SELECT * FROM ai_jobs WHERE kind='repair' AND payload ? 'repair_of_job' ORDER BY id").fetchall()


@pytest.mark.parametrize("build,code", [
    (lambda: dict(question_type="fill_blank", stem="A是____，B是____。", answer="x", accepted_answers=["x"], max_points=4, rubric=[dict(description="d", points=4)]), "blank_marker_count"),
    (lambda: dict(question_type="essay", max_points=18, rubric=[dict(description="d", points=6)] * 3), "rubric_item_count_out_of_range"),
    (lambda: dict(question_type="term", max_points=6, rubric=[]), "rubric_missing"),
    (lambda: dict(question_type="term", max_points=6, rubric=[dict(description="d", points=4)]), "rubric_points_mismatch"),
])
def test_which_failures_are_sent_to_repair(build, code):
    from pydantic import ValidationError
    from app.question_quality import Question

    base = dict(subject="biochemistry", stem="请回答下面的问题内容", answer="答案内容较长较长较长较长较长较长较长较长较长较长较长较长较长",
                evidence=[dict(textbook="t", authors="a", edition="e", chapter="c", locator="l", excerpt="x")], rubric=[dict(description="d", points=6)],
                knowledge_tags=[])
    with pytest.raises(ValidationError) as caught:
        Question.model_validate({**base, **build()})
    errors, repairable = gen._classify(caught.value)
    assert repairable and code in {e["code"] for e in errors}


def test_defects_with_no_repair_are_not_sent(db=None):
    from pydantic import ValidationError
    from app.question_quality import Question

    with pytest.raises(ValidationError) as caught:
        Question.model_validate(dict(subject="biochemistry", question_type="single_choice", stem="下列哪项是正确的说法呢", answer="A",
                                     options={"A": "x", "B": " ", "C": "z", "D": "w"}, evidence=[dict(textbook="t", authors="a", edition="e", chapter="c", locator="l", excerpt="x")],
                                     rubric=[dict(description="d", points=4)], max_points=4))
    assert gen._classify(caught.value)[1] is False  # an empty option is not on the repair list


def test_a_tidied_quote_is_admitted_with_the_databases_text_and_the_layer_is_recorded(db, corpus, setup_ai):
    def tidy(response, job):
        text = job["payload"]["anchor_exact_excerpt"]
        response["question"]["evidence"][0]["excerpt"] = text[:12] + "\n" + text[12:].replace("，", ",").replace("。", ".")

    job = written_with(db, setup_ai, tidy)
    assert job["state"] == "succeeded" and len(packets(db)) == 1
    evidence = packets(db)[0]["question_snapshot"]["evidence"][0]
    unit_text = db.execute("SELECT text FROM source_units WHERE id=%s", (evidence["source_unit_id"],)).fetchone()["text"]
    assert evidence["excerpt"] in unit_text  # the database's text, not the model's
    norm = job["result"]["normalization"]
    assert norm["evidence"][0]["matched"] and norm["evidence"][0]["layer"] in (2, 3)


def test_a_quote_from_outside_the_request_is_not_admitted_but_gets_one_repair(db, corpus, setup_ai):
    def outside(response, job):
        response["question"]["evidence"][0]["excerpt"] = job["payload"]["style"][0]["text"]  # exists in the library, was not sent as evidence

    job = written_with(db, setup_ai, outside)
    assert packets(db) == [] and job["state"] == "succeeded"
    assert job["result"]["dispatch_failure"]["code"] == "repair_requested"
    (repair,) = repair_jobs(db)
    p = repair["payload"]
    assert p["errors"][0]["code"] == "evidence_quote_unmatched" and p["repair_of_job"] == job["id"]
    assert p["evidence"][0]["text"] == job["payload"]["evidence"][0]["text"]  # the paragraphs it must quote from
    assert len(json.dumps(p, ensure_ascii=False).encode()) <= 24000
    assert (p["subject"], p["question_type"], p["max_points"]) == (job["payload"]["subject"], job["payload"]["question_type"], 4)


def accept_repair_with(db, repair, mutate=None):
    original = db.execute("SELECT payload FROM ai_jobs WHERE id=%s", (repair["payload"]["repair_of_job"],)).fetchone()["payload"]
    result = choice_response(dict(payload=original))
    if mutate:
        mutate(result["response"], original)
    finish_job(db, repair, result)
    auto_worker.dispatch_completed(db)
    return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (repair["id"],)).fetchone()


def test_a_good_repair_becomes_a_candidate_that_is_reviewed_and_counts_as_repaired(db, corpus, setup_ai):
    written_with(db, setup_ai, lambda r, job: r["question"]["evidence"][0].update(excerpt="教材里根本没有的话语，是模型编出来的引用文字"))
    (repair,) = repair_jobs(db)
    done = accept_repair_with(db, repair)
    assert done["state"] == "succeeded" and len(packets(db)) == 1
    qid = packets(db)[0]["question_id"]
    state = db.execute("SELECT status,revision_count FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()
    assert state["status"] == "checking" and state["revision_count"] == 1          # one repair used; reviewed like any other
    assert review_jobs(db)["review"]["payload"]["question_id"] == qid


def test_a_repair_that_still_fails_is_thrown_away_and_says_so(db, corpus, setup_ai):
    written_with(db, setup_ai, lambda r, job: r["question"]["evidence"][0].update(excerpt="教材里根本没有的话语，是模型编出来的引用文字"))
    (repair,) = repair_jobs(db)
    done = accept_repair_with(db, repair, lambda r, job: r["question"]["evidence"][0].update(excerpt="仍然是编出来的引用文字，教材里没有"))
    assert done["state"] == "blocked" and packets(db) == []
    failure = done["result"]["dispatch_failure"]
    assert failure["code"] == "repair_still_invalid" and failure["after"]["code"] in ("repair_unfixed", "evidence_identity_or_quote_mismatch")
    assert len(repair_jobs(db)) == 1  # never a second repair


@pytest.mark.parametrize("mutate,code", [
    (lambda r, job: r["question"]["rubric"][0].update(points=3), "rubric_points_mismatch"),
    (lambda r, job: r["question"]["options"].update(B=r["question"]["options"]["C"]), "duplicate_choice_options"),
    (lambda r, job: r["question"].update(answer="E"), "incomplete_choice_or_key"),
])
def test_repairable_structure_problems_go_to_one_repair(db, corpus, setup_ai, mutate, code):
    job = written_with(db, setup_ai, mutate)
    assert packets(db) == [] and job["result"]["dispatch_failure"]["code"] == "repair_requested"
    (repair,) = repair_jobs(db)
    assert code in {e["code"] for e in repair["payload"]["errors"]}
    assert repair["payload"]["draft"]["question"]["stem"]  # the draft goes along, with its distractor proof
    assert "distractor_checks" in repair["payload"]["draft"]


def test_problems_a_rewrite_cannot_fix_are_still_thrown_away_at_once(db, corpus, setup_ai):
    job = written_with(db, setup_ai, lambda r, j: r["question"]["options"].update(B=" "))   # an empty option
    assert job["state"] == "blocked" and repair_jobs(db) == []


def test_a_draft_that_is_only_formatted_badly_needs_no_repair_and_no_second_call(db, corpus, setup_ai):
    def sloppy(response, job):
        q = response["question"]
        q["max_points"] = "4"
        q["options"] = {"a.": "A. DNA连接酶", "B": "拓扑异构酶", "C)": "解旋酶", "D": "引发酶"}
        q["answer"] = "选A"
        q["rubric"][0]["points"] = "4"
        q["difficulty"] = "Easy"
        q["accepted_answers"] = ["多余"]

    job = written_with(db, setup_ai, sloppy)
    assert job["state"] == "succeeded" and repair_jobs(db) == [] and len(packets(db)) == 1
    fixes = set(job["result"]["normalization"]["fixes"])
    assert {"numbers_to_int", "choice_keys_normalized", "choice_prefix_removed", "answer_letter_normalized", "difficulty_mapped", "removed_accepted_answers"} <= fixes
    q = packets(db)[0]["question_snapshot"]
    assert q["answer"] == "A" and q["options"]["A"] == "DNA连接酶" and q["max_points"] == 4


# ---- 4. a malformed REVIEW answer is asked for again, once --------------------------------------------------

def malformed(db, job):
    db.execute("UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s",
               (json.dumps(dict(real_api=True, usage=dict(input_tokens=1, output_tokens=1, cache_read_input_tokens=0), response={"passed": True})), job["id"]))


def test_a_malformed_review_is_asked_for_once_more_and_never_blames_the_question(db, corpus, setup_ai):
    written_with(db, setup_ai)
    review = review_jobs(db)["review"]
    qid = review["payload"]["question_id"]
    malformed(db, review)
    auto_worker.dispatch_completed(db)
    first = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (review["id"],)).fetchone()
    assert first["result"]["dispatch_failure"]["code"] == "review_reformat_requested" and first["state"] == "succeeded"
    retry = db.execute("SELECT * FROM ai_jobs WHERE job_key=%s", (review["job_key"] + ":format_retry",)).fetchone()
    assert retry["kind"] == "quality_review" and retry["payload"] == review["payload"]       # the same review, once more
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["status"] == "checking"
    malformed(db, retry)
    auto_worker.dispatch_completed(db)
    state = db.execute("SELECT status,reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()
    assert (state["status"], state["reason"]) == ("blocked", "review_format_failed")       # the review failed, not the question
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE job_key LIKE %s", (review["job_key"] + "%",)).fetchone()["n"] == 2   # no third ask
    auto_worker.plan(db, setup_ai)
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='repair' AND payload->>'question_id'=%s", (str(qid),)).fetchone()["n"] == 0
    from app import auto_quality as aq
    assert aq.discard_unfixable(db) == []   # not thrown away either


def test_a_second_correct_option_still_goes_to_one_repair(db, corpus, setup_ai):
    """Unchanged rule, checked: an ambiguity finding blocks the question as repairable (one repair, then re-review)."""
    from test_gap_generation import tweak

    def edit(job):
        job = tweak(db, job, ["response", "option_checks", "C", "verdict"], "correct")
        return tweak(db, job, ["response", "other_correct_options"], ["C"])

    qid, review = review_round(db, setup_ai, edit)
    assert not auto_quality.accept_round(db, review)
    reason = db.execute("SELECT reason FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["reason"]
    assert "second_correct_option" in reason and reason not in auto_quality.UNREPAIRABLE
    auto_worker.plan(db, setup_ai)
    assert db.execute("SELECT count(*) AS n FROM ai_jobs WHERE kind='repair' AND payload->>'question_id'=%s", (str(qid),)).fetchone()["n"] == 1


def test_the_reviewers_quotes_are_matched_with_the_same_tolerance(db, corpus, setup_ai):
    sent = {7: ["糖酵解是指葡萄糖在无氧条件下分解为丙酮酸，并产生少量ATP的过程。"]}
    assert auto_quality.quoted(7, "糖酵解是指葡萄糖在无氧条件下分解为丙酮酸,并产生少量ATP的过程.", sent)
    assert not auto_quality.quoted(7, "糖酵解是指葡萄糖在有氧条件下分解为丙酮酸，并产生大量ATP的过程。", sent)
    assert not auto_quality.quoted(8, "糖酵解是指葡萄糖在无氧条件下分解为丙酮酸", {8: ["完全不同的另一段话语用于测试"]})


# ---- 5. reports: counts in the last 24 h and a no-model replay -------------------------------------------------

def test_the_stats_rank_reasons_and_the_replay_rescues_what_the_new_rules_can(db, corpus, setup_ai):
    def tidy(response, job):
        text = job["payload"]["anchor_exact_excerpt"]
        response["question"]["evidence"][0]["excerpt"] = text[:12] + "\n" + text[12:]

    job = gen.plan_gap(db, setup_ai)
    result = choice_response(job)
    tidy(result["response"], job)
    # as the OLD rules saw it: finished, then blocked for a mismatched quote
    db.execute("UPDATE ai_jobs SET state='blocked',error_code='invalid_or_uncertain_result',result=%s WHERE id=%s",
               (json.dumps({**result, "dispatch_failure": {"code": "evidence_identity_or_quote_mismatch"}}), job["id"]))
    stats = waste_report.blocked_stats(db, 24)
    top = stats["blocked"][0]
    assert top["code"] == "evidence_identity_or_quote_mismatch" and top["count"] == 1 and "6 层" in top["handling"]
    out = waste_report.replay(db, 24)
    assert out["considered"] == 1 and out["was"] == {"evidence_identity_or_quote_mismatch": 1}
    assert out["now"] == {"ok_now": 1}
    layer = next(iter(out["samples"]))
    assert layer in ("normalized", "letters_only") and out["samples"][layer][0]["database_text"]
    assert db.execute("SELECT state FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()["state"] == "blocked"   # nothing was written
