"""Quality PR 3: past-exam duplicates, meaning-based scoring criteria, import types read from the text, term retrieval,
single-blank rubrics, planning shares, review failure categories."""
import pytest
from psycopg.types.json import Jsonb

from app import ai_jobs, auto_fixes, auto_generation as gen, auto_quality as quality, evidence_filter, exam_authoring
from app import past_exam_import as pei, sources, waste_report
from test_ai_automation import setup_ai  # noqa: F401
from test_ocr_only import material
from test_past_exam_import import (TEXTBOOK, corpus, finish_job, items, model_result, queued_jobs,  # noqa: F401
                                   old_planning_order, run_to_ready)

MISREAD = [
    "2024年中国科学技术大学生物化学真题",
    "名词解释",
    "1. 冈崎片段",
    "2. 细胞增殖的检测方法______",
    "3. G蛋白是由______三个亚单位构成",
    "4. 下列属于第二信使的是",
    "A. cAMP", "B. 葡萄糖", "C. 甘油", "D. 水",
    "5. 下列属于第二信使的有（多选）",
    "A. cAMP", "B. cGMP", "C. 甘油", "D. IP3",
    "6. 糖酵解",
    "二 填空题：（每题2分）",
    "7. DNA复制时，滞后链上合成的短片段称为____。",
    "2025年中国科学技术大学生物化学真题",
]


@pytest.fixture
def misread(db, tmp_path, monkeypatch):
    path, meta = material(tmp_path, "biochemistry_lower", content=TEXTBOOK)
    sources.import_source(db, path, meta)
    path, meta = material(tmp_path, "biochemistry_exams", content=MISREAD)
    sources.import_source(db, path, meta)
    monkeypatch.setattr(pei, "pinned_source_bytes", lambda file_id, sha: True)
    monkeypatch.setattr(pei, "source_bibliography", lambda conn, unit: dict(textbook="Synthetic", authors="Test", edition="Test"))


# ---- 一: a past-exam question is not shown its own source, and is not a "duplicate" of it -------------------------------

def reviewed_term(db, setup_ai):
    pei.import_items(db)
    pei.plan(db, setup_ai, batch=50)
    for job in queued_jobs(db).values():
        finish_job(db, job, model_result(job))
    from app import auto_worker
    auto_worker.dispatch_completed(db)
    return db.execute("SELECT * FROM ai_jobs WHERE kind='quality_review' ORDER BY id").fetchall()


def test_the_reviewer_is_not_shown_the_past_exam_question_it_is_reviewing_as_a_style_example(db, corpus, setup_ai):
    reviews = reviewed_term(db, setup_ai)
    term = next(j for j in reviews if j["payload"]["question"]["question_type"] == "term")
    assert quality.job_contract_version(term) == 11
    assert term["payload"]["style"] == []              # its only style unit is its own source
    q, refs, style = quality.evidence(db, term["payload"]["question_id"])
    assert style and any("冈崎片段" in u["text"] for u in style)   # the stored evidence is untouched (the bank's checks still match)
    old = quality.review_payload("review", term["payload"]["question_id"], quality.content_sha(q), q, refs, style, 10)
    assert old["style"] != []                          # contract 10 requests are judged exactly as they were sent
    text = quality.REVIEW_INSTRUCTIONS
    assert "NEVER judge duplication against them" in text and "set checks.duplication=true" in text


def test_past_exam_questions_blocked_as_duplicates_of_their_own_example_go_back_to_review(db, corpus, setup_ai):
    reviews = reviewed_term(db, setup_ai)
    review = next(j for j in reviews if j["payload"]["question"]["question_type"] == "term")
    qid = review["payload"]["question_id"]
    old_text = "Single independent review of one exam question (contract 10). You receive the question"
    db.execute("UPDATE ai_jobs SET payload=jsonb_set(payload,'{instructions}',to_jsonb(%s::text)),job_key=replace(job_key,':c11','') WHERE id=%s",
               (old_text, review["id"]))             # (what a contract-10 review looked like: no contract suffix in its key)
    sha = db.execute("SELECT content_sha256 FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["content_sha256"]
    finding = dict(checks=dict(uniqueness=True, scientific_correctness=True, exam_style=True, type_fit=True, duplication=False), unresolved=["与示例完全一致，属于重复"])
    db.execute("INSERT INTO auto_quality_events(question_id,content_sha256,policy_version,phase,run_key,job_id,evidence_snapshot,result,passed) "
               "VALUES (%s,%s,%s,'review','t-dup',%s,'[]',%s,false)",
               (qid, sha, quality.POLICY, review["id"], Jsonb(dict(finding=finding, issues=["unresolved_quality_findings"]))))
    db.execute("UPDATE auto_question_states SET status='retired',reason='discarded:unresolved_quality_findings',revision_count=1 WHERE question_id=%s", (qid,))
    db.execute("UPDATE past_exam_items SET status='blocked',blocked_reason='review_failed' WHERE question_id=%s", (qid,))
    assert quality.requeue_past_exam_duplicates(db) == [qid]
    assert db.execute("SELECT status FROM auto_question_states WHERE question_id=%s", (qid,)).fetchone()["status"] == "candidate"
    assert db.execute("SELECT status FROM past_exam_items WHERE question_id=%s", (qid,)).fetchone()["status"] == "packet_created"
    assert quality.requeue_past_exam_duplicates(db) == []                         # once
    jobs = quality.schedule_checks(db, setup_ai, qid)                             # the question is not written again: a new review
    assert jobs and quality.job_contract_version(jobs[0]) == 11 and jobs[0]["id"] != review["id"]


# ---- 二: scoring criteria need meaning support, quotes stay verbatim ---------------------------------------------------

def test_prompts_say_criteria_are_judged_by_meaning_and_must_stay_inside_the_paragraphs():
    assert "SUPPORTED IN MEANING" in quality.REVIEW_INSTRUCTIONS and "need not appear word for word" in quality.REVIEW_INSTRUCTIONS
    assert "exact substrings of the supplied textbook paragraphs" in quality.REVIEW_INSTRUCTIONS      # quotes: verbatim
    for kind in ("single_choice", "fill_blank", "term", "short_answer", "essay"):
        text = exam_authoring.instructions(kind, 4)
        assert "ONLY from what the supplied paragraphs say" in text and "cut off" in text and "受体鸟" in text
    assert "cut-off ending" in pei.INSTRUCTIONS and "add nothing from outside them" in pei.INSTRUCTIONS


# ---- 三: types are read from the text ------------------------------------------------------------------------------

def test_the_text_decides_the_type_and_titles_are_not_part_of_a_stem():
    assert pei.infer_type("term", "细胞增殖的检测方法______") == "fill_blank"
    assert pei.infer_type("short_answer", "G蛋白由______三个亚单位构成") == "fill_blank"
    assert pei.infer_type("term", "下列属于第二信使的是\nA. cAMP\nB. 葡萄糖\nC. 甘油\nD. 水") == "single_choice"
    assert pei.infer_type("term", "下列有（多选）\nA. a\nB. b\nC. c\nD. d") == "multiple_choice"
    assert pei.infer_type("term", "冈崎片段") == "term" and pei.infer_type("fill_blank", "称为____。") == "fill_blank"
    from app.transcript_analysis import is_section_title, section_heading
    for title in ("二 填空题：（每题2分）", "三、简答题（每题 X 分）", "一、名词解释（共20分）"):
        assert is_section_title(title)
    assert not is_section_title("1. 冈崎片段") and not is_section_title("简述DNA复制，每题要点")
    assert section_heading("二 填空题：（每题2分）")["type"] == "fill_blank"           # no punctuation after the number


def test_import_files_blanks_choices_and_multiple_choice_correctly_and_drops_the_title(db, misread):
    pei.import_items(db)
    got = items(db)
    assert got[("term", "1")]["stem"] == "冈崎片段" and got[("term", "6")]["stem"] == "糖酵解"        # no 「二 填空题」 spliced in
    assert got[("fill_blank", "2")]["stem"] == "细胞增殖的检测方法____" and got[("fill_blank", "3")]["stem"] == "G蛋白是由____三个亚单位构成"
    assert got[("single_choice", "4")]["options"]["A"] == "cAMP"
    multi = next(r for r in got.values() if r["question_number"] == "5")
    assert multi["status"] == "blocked" and multi["blocked_reason"] == "multiple_choice_unsupported"
    assert got[("fill_blank", "7")]["stem"] == "DNA复制时，滞后链上合成的短片段称为____。"


def test_reclassify_retires_misfiled_items_once_and_reports_the_counts(db, misread, setup_ai):
    pei.import_items(db)
    row = items(db)[("fill_blank", "2")]
    # how it used to be filed: as a term, with the blanks in the stem
    db.execute("INSERT INTO past_exam_items(subject,question_type,stem,stem_hash,document_id,unit_id,question_number,year,years) VALUES "
               "('biochemistry','term','细胞增殖的检测方法______',%s,%s,%s,'2',2024,'[2024]')",
               (pei.stem_hash("term", "细胞增殖的检测方法______"), row["document_id"], row["unit_id"]))
    summary = pei.reclassify_once(db)
    assert summary["retired_by_old_type"] == {"term": 1}
    assert summary["before"]["term"] == summary["after"]["term"] + 1
    assert db.execute("SELECT count(*) AS n FROM past_exam_items WHERE blocked_reason='reclassified'").fetchone()["n"] == 1
    assert pei.reclassify_once(db) is None                                           # once per version
    route = waste_report.past_exam_route(db, 24)
    assert route["items_by_state"]["term"]["blocked:reclassified"] == 1
    assert route["reclassification"]["retired_by_old_type"] == {"term": 1} and route["reclassification"]["after"]["fill_blank"] >= 3


# ---- 四: term retrieval --------------------------------------------------------------------------------------------

def add_units(db, rows):
    base = db.execute("SELECT u.document_id AS d, u.chapter FROM source_units u JOIN source_documents doc ON doc.id=u.document_id "
                      "WHERE doc.metadata->>'source_kind'='textbook' AND u.chapter NOT IN ('','unclassified','contents') LIMIT 1").fetchone()
    ids = []
    for i, (text, flags) in enumerate(rows):
        ids.append(db.execute("INSERT INTO source_units(document_id,ordinal,chapter,section,text,quality_flags) VALUES (%s,%s,%s,'s',%s,%s) RETURNING id",
                              (base["d"], 200000 + i, base["chapter"], text, Jsonb(flags))).fetchone()["id"])
    return ids


def test_only_severe_ocr_flags_make_a_paragraph_unusable():
    long = "这一段讲的是糖酵解途径中的关键步骤和限速酶，内容足够长，可以作为出题的教材依据来使用，字数超过八十字。" * 2
    assert evidence_filter.usable(long, ["scientific_notation_requires_source_review"])
    assert evidence_filter.usable(long, ["chemical_structure"])
    assert evidence_filter.reason_unusable(long, ["corrupt_ocr"]) == "ocr_quality_flag"
    assert evidence_filter.reason_unusable(long, ["complex_table", "chemical_structure"]) == "ocr_quality_flag"


def test_the_term_is_searched_in_every_paragraph_whatever_its_length_or_mild_flag(db, corpus):
    ids = add_units(db, [("前文。", []), ("孟德尔试剂丁是一种虚构的酶，催化ATP水解。", ["scientific_notation_requires_source_review"]), ("后文。", []),
                         ("孟德尔试剂丁的另一处出现，但这一段的 OCR 有乱码�。", ["corrupt_ocr"])])
    got = [u["unit_id"] for u in pei.retrieve_evidence(db, "biochemistry", "孟德尔试剂丁", term=True)]
    assert got == ids[:3]                 # the hit (short, mildly flagged) with its neighbours; the corrupt one is never used
    assert len(got) <= pei.MAX_EVIDENCE_UNITS


def test_a_term_that_is_nowhere_in_the_textbook_never_reaches_the_model(db, corpus, setup_ai):
    doc = db.execute("SELECT d.id AS d, min(u.id) AS u FROM source_documents d JOIN source_units u ON u.document_id=d.id "
                     "WHERE d.metadata->>'source_kind'='exam_transcription' GROUP BY d.id LIMIT 1").fetchone()
    db.execute("INSERT INTO past_exam_items(subject,question_type,stem,stem_hash,document_id,unit_id,question_number,year,years) "
               "VALUES ('biochemistry','term','完全不存在的名词乙丙',%s,%s,%s,'9',2024,'[2024]')", (pei.stem_hash("term", "完全不存在的名词乙丙"), doc["d"], doc["u"]))
    item = db.execute("SELECT * FROM past_exam_items WHERE stem='完全不存在的名词乙丙'").fetchone()
    before = db.execute("SELECT count(*) AS n FROM ai_jobs").fetchone()["n"]
    assert pei._enqueue_item(db, setup_ai, item) == "blocked"
    assert db.execute("SELECT blocked_reason FROM past_exam_items WHERE id=%s", (item["id"],)).fetchone()["blocked_reason"] == "term_not_in_textbook"
    assert db.execute("SELECT count(*) AS n FROM ai_jobs").fetchone()["n"] == before


def test_the_paragraph_count_report_shows_the_old_and_the_new_rule(db, corpus):
    add_units(db, [("含希腊字母 α 和 ATP 的一段话，" * 12, ["scientific_notation_requires_source_review"])])
    report = waste_report.filter_report(db)["biochemistry"]
    assert report["after"] >= report["after_old_rule"] + 1 and "ocr_quality_flag" in report["filtered_old_rule"]


# ---- 五: single-blank rubric ------------------------------------------------------------------------------------------

def test_a_single_blank_question_prompt_and_format_check_allow_one_rubric_item_only():
    assert "the single rubric item is worth max_points" in exam_authoring.instructions("fill_blank", 2.5)
    raw = dict(question_type="fill_blank", stem="G蛋白由____三个亚单位构成", answer="αβγ", accepted_answers=["αβγ"], blanks=[], max_points=4,
               knowledge_tags=[], rubric=[dict(description="答案：αβγ", points=2, knowledge_tags=[]), dict(description="解释其原理", points=2, knowledge_tags=[])])
    fixed, notes = auto_fixes.autofix(raw, 4)
    assert "fill_rubric_merged" in notes and fixed["rubric"] == [dict(description="答案：αβγ", points=4, knowledge_tags=[])]
    two_blanks = {**raw, "blanks": [dict(points=2), dict(points=2)]}
    assert auto_fixes.autofix(two_blanks, 4)[0]["rubric"] == raw["rubric"]


# ---- 六: planning shares --------------------------------------------------------------------------------------------

def test_requests_are_shared_out_by_gap_and_the_fill_in_keeps_at_least_30_percent(db, setup_ai):
    gaps = [("biochemistry", "fill_blank", 30), ("cell_biology", "fill_blank", 30), ("biochemistry", "term", 8),
            ("biochemistry", "short_answer", 6), ("cell_biology", "essay", 2)]
    assert gen.allocate_order(db, gaps)[0][1] == "fill_blank"                         # nothing written yet: the biggest gap first
    for i in range(10):                                                              # the last requests were all term definitions
        ai_jobs.enqueue(db, setup_ai, f"w{i}", "generation", dict(subject="biochemistry", question_type="term", stem=str(i), evidence=[]))
    order = gen.allocate_order(db, gaps)
    assert order[0][1] == "fill_blank" and {g[1] for g in order[:2]} == {"fill_blank"}   # below 30%: fill-in first
    for i in range(10, 16):
        ai_jobs.enqueue(db, setup_ai, f"w{i}", "generation", dict(subject="biochemistry", question_type="fill_blank", stem=str(i), evidence=[]))
    order = gen.allocate_order(db, gaps)                                             # 6 of 16 are fill-ins: above 30%, by deficit
    assert [g[:2] for g in order][0] != ("biochemistry", "term")                      # term is over-served


# ---- 七: failure kinds follow the reviewer's own axes ------------------------------------------------------------------

def test_a_review_belongs_to_every_axis_it_failed_and_each_sample_is_that_axis_own_sentence():
    finding = dict(checks=dict(uniqueness=False, scientific_correctness=True, exam_style=True, type_fit=True, duplication=False),
                   unresolved=["题干有歧义，条件未定义", "与示例完全一致，属于重复"])
    pairs = dict(waste_report.categorised(dict(issues=["unresolved_quality_findings", "invented_quote"]), finding))
    assert set(pairs) == {"题目有歧义", "与已有题目重复", "引用不符"}                       # several categories at once
    assert "歧义" in pairs["题目有歧义"] and "重复" not in pairs["题目有歧义"]
    assert "重复" in pairs["与已有题目重复"] and "歧义" not in pairs["与已有题目重复"]
    assert pairs["引用不符"].startswith("程序检查")
    unmatched = dict(waste_report.categorised(dict(issues=["unresolved_quality_findings"]),
                                              dict(checks=dict(exam_style=False), unresolved=["某种说不清的问题"])))
    assert unmatched["不符合考试风格"].startswith("（未单独说明）")
