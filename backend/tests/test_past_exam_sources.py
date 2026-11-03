"""More past-exam questions can be imported: choice options written in many ways, tables that only laid the question out, and
fill-in questions with several independent blanks (split into single-blank questions). Only the import and parsing change;
the review standards do not. Everything here is synthetic."""
import pytest

from app import past_exam_import as pei, sources
from test_ocr_only import material
from test_past_exam_import import TEXTBOOK

# ---- 1. choice options ---------------------------------------------------------------------------------------------
STEM = "下列哪种酶负责封闭冈崎片段之间的缺口？"
OPTIONS = {"A": "DNA连接酶", "B": "拓扑异构酶", "C": "解旋酶", "D": "引发酶"}

WRITTEN_AS = {
    "same line, A.": f"{STEM} A. DNA连接酶 B. 拓扑异构酶 C. 解旋酶 D. 引发酶",
    "one per line": f"{STEM}\nA. DNA连接酶\nB. 拓扑异构酶\nC. 解旋酶\nD. 引发酶",
    "full-width period": f"{STEM}\nA．DNA连接酶\nB．拓扑异构酶\nC．解旋酶\nD．引发酶",
    "enumeration comma": f"{STEM}\nA、DNA连接酶\nB、拓扑异构酶\nC、解旋酶\nD、引发酶",
    "half-width parentheses": f"{STEM}\n(A) DNA连接酶\n(B) 拓扑异构酶\n(C) 解旋酶\n(D) 引发酶",
    "full-width parentheses": f"{STEM}\n（A）DNA连接酶\n（B）拓扑异构酶\n（C）解旋酶\n（D）引发酶",
    "closing parenthesis only": f"{STEM}\nA) DNA连接酶\nB) 拓扑异构酶\nC) 解旋酶\nD) 引发酶",
    "colon": f"{STEM}\nA：DNA连接酶\nB：拓扑异构酶\nC：解旋酶\nD：引发酶",
    "full-width letters": f"{STEM}\nＡ．DNA连接酶\nＢ．拓扑异构酶\nＣ．解旋酶\nＤ．引发酶",
    "all on one line, full-width brackets": f"{STEM}（A）DNA连接酶（B）拓扑异构酶（C）解旋酶（D）引发酶",
    "glued to the end of the stem": f"{STEM}A.DNA连接酶B.拓扑异构酶C.解旋酶D.引发酶",
    "no punctuation, line starts": f"{STEM}\nA DNA连接酶\nB 拓扑异构酶\nC 解旋酶\nD 引发酶",
    "option text over two lines": f"{STEM}\nA. DNA连接酶\nB. 拓扑异构酶\nC. 解旋酶，\n能够解开双链\nD. 引发酶",
    "an earlier (A) inside the stem": f"在图中标注（A）处所指的酶，{STEM}\nA. DNA连接酶\nB. 拓扑异构酶\nC. 解旋酶\nD. 引发酶",
}


@pytest.mark.parametrize("name", list(WRITTEN_AS))
def test_choice_options_written_many_ways_are_parsed(name):
    parsed = pei.parse_choice(WRITTEN_AS[name])
    assert parsed is not None, name
    stem, options = parsed
    assert set(options) == set("ABCD") and options["A"] == "DNA连接酶" and options["B"] == "拓扑异构酶"
    assert options["D"] == "引发酶" and options["C"].startswith("解旋酶")
    assert stem.endswith("缺口？") or stem.endswith("（A）处所指的酶，" + STEM) or STEM in stem


def test_multi_line_option_keeps_its_second_line():
    assert pei.parse_choice(WRITTEN_AS["option text over two lines"])[1]["C"] == "解旋酶，\n能够解开双链"


def test_letters_inside_words_are_not_options():
    text = "ATP合成酶中的DNA. 以及B.subtilis的作用是什么？A. 一 B. 二 C. 三 D. 四"
    stem, options = pei.parse_choice(text)
    assert options == {"A": "一", "B": "二", "C": "三", "D": "四"} and "DNA." in stem


UNPARSEABLE = {
    "no_option_markers": "下列叙述中正确的一项是哪一个，请选择",
    "missing_option_D": f"{STEM} A. 一 B. 二 C. 三",
    "missing_option_BC": f"{STEM} A. 一 D. 四",
    "options_out_of_order": f"{STEM} A. 一 C. 三 B. 二 D. 四",
    "empty_option_text": f"{STEM}\nA. 一\nB.\nC. 三\nD. 四",
    "stem_too_short": "题？\nA. 一\nB. 二\nC. 三\nD. 四",
}


@pytest.mark.parametrize("detail", list(UNPARSEABLE))
def test_what_still_cannot_be_parsed_is_skipped_and_says_why(detail):
    assert pei.parse_choice(UNPARSEABLE[detail]) is None
    assert pei.choice_failure_detail(UNPARSEABLE[detail]) == detail


# ---- 2. tables -----------------------------------------------------------------------------------------------------
def kid(text, flags=("docx_table_requires_review",)):
    return {"transcription": text, "concerns": list(flags)}


def test_a_table_that_only_laid_the_question_out_is_let_through():
    assert pei.table_dependency("下列酶中哪个催化糖酵解的第一步限速反应？\nA. 己糖激酶\tB. 磷酸果糖激酶", []) is None
    assert pei.table_dependency("DNA复制的方向是____。", [kid("5′→3′"), kid("3′→5′")]) is None
    assert pei.clean_layout_text("A. 一\t\tB. 二\t C. 三") == "A. 一 B. 二 C. 三"


@pytest.mark.parametrize("text,why", [
    ("根据下表数据计算反应的Km值。", "text_refers_to_a_table"),
    ("见表1，指出酶的最适pH。", "text_refers_to_a_table"),
    ("下列各表格中酶的分类正确的是", "text_refers_to_a_table"),
    ("请填写 | 酶 | 底物 | 产物 | 完成反应", "pipe_delimited_rows"),
])
def test_a_question_that_needs_the_table_is_still_skipped(text, why):
    assert pei.table_dependency(text, []) == why


def test_a_grid_of_numbers_is_a_data_table_even_without_a_pointer_in_the_text():
    grid = [kid(c) for c in ["0.5", "1.0", "2.0", "4.0", "酶活性", "浓度"]]
    assert pei.table_dependency("计算各浓度下的相对酶活性并作图。", grid) == "grid_of_data_cells"
    assert pei.table_dependency("计算各浓度下的相对酶活性并作图。", grid[:3]) is None


# ---- 3. several blanks ---------------------------------------------------------------------------------------------
def test_independent_blanks_are_told_from_dependent_ones():
    def check(stem):
        flat = pei.re.sub(r"[_＿]{2,}", "____", stem)
        return pei.blanks_are_independent(flat, pei.blank_spans(flat))

    assert check("线粒体通过____合成ATP，其内膜上的____负责转运ADP与ATP。")
    assert not check("糖酵解中____和____是两个关键的限速酶。")          # a list: the order does not matter
    assert not check("三羧酸循环的产物为____、____、____。")
    assert not check("血红蛋白由____个亚基组成，依次为____和____。")      # 「依次」 ties them together
    assert not check("DNA复制中____与____相互配合共同完成。")


def test_answers_are_split_one_per_blank():
    assert pei.split_answers("氧化磷酸化；ADP/ATP转位酶", 2) == ["氧化磷酸化", "ADP/ATP转位酶"]
    assert pei.split_answers("4、血红素、二价铁", 3) == ["4", "血红素", "二价铁"]
    assert pei.split_answers("己糖激酶 磷酸果糖激酶", 2) == ["己糖激酶", "磷酸果糖激酶"]
    assert pei.split_answers("只有一个答案", 2) is None
    assert pei.split_answers("一；二；三", 2) is None


def test_each_single_blank_stem_keeps_the_whole_question_and_fills_the_others():
    stem = "线粒体通过____合成ATP，其内膜上的____负责转运ADP与ATP。"
    spans = pei.blank_spans(stem)
    singles = pei.split_blanks(stem, spans, ["氧化磷酸化", "ADP/ATP转位酶"])
    assert singles == ["线粒体通过____合成ATP，其内膜上的ADP/ATP转位酶负责转运ADP与ATP。",
                       "线粒体通过氧化磷酸化合成ATP，其内膜上的____负责转运ADP与ATP。"]
    assert all(pei.normalize_blank(one) == one for one in singles)


def units(*lines):
    return [dict(id=100 + i, ordinal=i, section=f"p{i}", text=text, quality_flags=list(flags))
            for i, (text, flags) in enumerate((l if isinstance(l, tuple) else (l, ()) for l in lines))]


from app.source_policy import FILES

_EXAM_FILE = next(i for i in FILES.values() if i["key"] == "biochemistry_exams")
DOC = {"id": 1, "file_id": _EXAM_FILE["file_id"], "subject": _EXAM_FILE["subject"]}
BLANK_EXAM = units(
    "2024年中国科学技术大学生物化学真题",
    "填空题",
    "1. 线粒体通过____合成ATP，其内膜上的____负责转运ADP与ATP。",
    "2. 糖酵解中____和____是两个关键的限速酶。",
    "3. 血红蛋白含有____个亚基，其辅基是____，该辅基的中心离子是____。",
    "4. 蛋白质合成中，____负责携带氨基酸，而____负责识别起始密码子。",
    "参考答案",
    "1. 氧化磷酸化；ADP/ATP转位酶",
    "2. 己糖激酶、磷酸果糖激酶",
    "3. 4、血红素、二价铁",
)


def parsed(lines, doc=DOC):
    return list(pei.questions_in_document(doc, lines))


def test_a_question_with_independent_blanks_becomes_one_question_per_blank():
    got = [q for q in parsed(BLANK_EXAM) if q["question_number"] in ("1", "3")]
    assert [(q["question_number"], q["source_blank_index"], q["source_blank_count"], q["blocked"]) for q in got] == [
        ("1", 1, 2, None), ("1", 2, 2, None), ("3", 1, 3, None), ("3", 2, 3, None), ("3", 3, 3, None)]
    first = got[0]
    assert first["stem"] == "线粒体通过____合成ATP，其内膜上的ADP/ATP转位酶负责转运ADP与ATP。"
    assert first["source_answer"] == "氧化磷酸化" and first["source_stem"].count("____") == 2
    assert got[3]["stem"] == "血红蛋白含有4个亚基，其辅基是血红素，该辅基的中心离子是____。".replace("血红素，该辅基的中心离子是____", "____，该辅基的中心离子是二价铁")
    assert all(q["question_type"] == "fill_blank" and q["options"] == {} for q in got)
    assert len({q["stem_hash"] for q in got}) == 5                       # five different questions


def test_dependent_blanks_and_missing_answers_stay_skipped_with_the_reason():
    got = {q["question_number"]: q for q in parsed(BLANK_EXAM) if q["question_number"] in ("2", "4")}
    assert got["2"]["blocked"] == "fill_blank_marker_unsupported" and got["2"]["blocked_detail"] == "blanks_depend_on_each_other"
    assert got["4"]["blocked"] == "fill_blank_marker_unsupported"
    assert got["4"]["blocked_detail"] == "reference_answers_missing_or_not_one_per_blank"   # the answer sheet has no line 4


def test_an_ambiguous_answer_line_is_not_used():
    lines = units("2024年中国科学技术大学生物化学真题", "填空题",
                  "1. 线粒体通过____合成ATP，其内膜上的____负责转运ADP与ATP。",
                  "参考答案", "1. 氧化磷酸化；ADP/ATP转位酶", "1. 别的答案；另一个")
    (q,) = parsed(lines)
    assert q["blocked"] == "fill_blank_marker_unsupported" and q["blocked_detail"].startswith("reference_answers")


def test_answers_of_another_year_are_not_used():
    lines = units("2023年中国科学技术大学生物化学真题", "填空题",
                  "1. 线粒体通过____合成ATP，其内膜上的____负责转运ADP与ATP。",
                  "2024年中国科学技术大学生物化学真题", "参考答案", "1. 氧化磷酸化；ADP/ATP转位酶")
    (q,) = parsed(lines)
    assert q["blocked"] and q["blocked_detail"].startswith("reference_answers")


def test_too_many_blanks_are_skipped():
    lines = units("2024年中国科学技术大学生物化学真题", "填空题", "1. 甲____乙乙乙乙____丙丙丙丙____丁丁丁丁____戊戊戊戊____己己己己____。")
    (q,) = parsed(lines)
    assert q["blocked_detail"] == "too_many_blanks"


# ---- table layout through the whole question parser ---------------------------------------------------------------
TABLE = "docx_table_requires_review"
TABLE_EXAM = units(
    "2024年中国科学技术大学生物化学真题",
    "单项选择题",
    ("1. 下列酶中哪个催化糖酵解的第一步限速反应？", [TABLE]),
    ("A. 己糖激酶\tB. 磷酸果糖激酶\tC. 丙酮酸激酶\tD. 烯醇化酶", [TABLE]),
    ("2. 根据下表数据，判断酶的类型是什么？", [TABLE]),
    ("A. 竞争性 B. 非竞争性 C. 反竞争性 D. 混合型", [TABLE]),
)


def test_table_layout_questions_are_let_through_and_content_tables_are_not():
    got = {q["question_number"]: q for q in parsed(TABLE_EXAM)}
    assert got["1"]["blocked"] is None and got["1"]["table_layout"] is True and got["1"]["options"]["A"] == "己糖激酶"
    assert got["2"]["blocked"] == "table_requires_review" and got["2"]["blocked_detail"] == "text_refers_to_a_table"
    assert got["2"]["table_layout"] is False


def test_other_blocking_reasons_are_not_hidden_by_the_table_logic():
    lines = units("2024年中国科学技术大学生物化学真题", "单项选择题", ("1. 如图所示，指出该酶的名称是什么酶？", [TABLE, "image_dependent"]))
    (q,) = parsed(lines)
    assert q["blocked"] == "image_dependent"


# ---- through the database ------------------------------------------------------------------------------------------
IMPORT_EXAMS = [
    "2023年中国科学技术大学生物化学真题",
    "填空题",
    "1. 线粒体通过____合成ATP，其内膜上的____负责转运ADP与ATP。",
    "单项选择题",
    "2. 下列哪种酶负责封闭冈崎片段之间的缺口？",
    "（A）DNA连接酶（B）拓扑异构酶（C）解旋酶（D）引发酶",
    "参考答案",
    "1. 氧化磷酸化；ADP/ATP转位酶",
    "2024年中国科学技术大学生物化学真题",
    "填空题",
    "1. 线粒体通过____合成ATP，其内膜上的____负责转运ADP与ATP。",
    "参考答案",
    "1. 氧化磷酸化；ADP/ATP转位酶",
    "2025年中国科学技术大学生物化学真题",
]


@pytest.fixture
def corpus(db, tmp_path, monkeypatch):
    path, meta = material(tmp_path, "biochemistry_lower", content=TEXTBOOK)
    sources.import_source(db, path, meta)
    path, meta = material(tmp_path, "biochemistry_exams", content=IMPORT_EXAMS)
    sources.import_source(db, path, meta)
    monkeypatch.setattr(pei, "pinned_source_bytes", lambda file_id, sha: True)


def test_split_questions_are_stored_with_their_source_and_repeats_across_years_collapse(db, corpus):
    summary = pei.import_items(db)
    rows = db.execute("SELECT * FROM past_exam_items WHERE source_blank_index IS NOT NULL ORDER BY source_blank_index").fetchall()
    assert [(r["source_blank_index"], r["source_blank_count"], r["source_answer"]) for r in rows] == [
        (1, 2, "氧化磷酸化"), (2, 2, "ADP/ATP转位酶")]
    assert all(r["question_type"] == "fill_blank" and r["status"] == "imported" for r in rows)
    assert all(r["years"] == [2024, 2023] and r["source_stem"].count("____") == 2 for r in rows)   # same question in two years: one item each
    assert summary["split_blanks"] == 2 and summary["duplicates_collapsed"] == 2
    choice = db.execute("SELECT * FROM past_exam_items WHERE question_type='single_choice'").fetchone()
    assert choice["status"] == "imported" and choice["options"]["D"] == "引发酶" and choice["blocked_detail"] is None


def test_import_with_the_new_columns_is_idempotent(db, corpus):
    pei.import_items(db)
    before = db.execute("SELECT * FROM past_exam_items ORDER BY id").fetchall()
    again = pei.import_items(db)
    assert again["created"] == 0 and again["merged"] == 0
    assert db.execute("SELECT * FROM past_exam_items ORDER BY id").fetchall() == before


def test_reclassify_reports_usable_items_before_and_after(db, corpus, monkeypatch):
    # first the old rules: the choice has bracketed options in one line and the blanks are not split
    monkeypatch.setattr(pei, "parse_choice", lambda text: None)
    monkeypatch.setattr(pei, "split_answers", lambda text, count: None)
    pei.import_items(db)
    assert db.execute("SELECT count(*) AS n FROM past_exam_items WHERE status<>'blocked'").fetchone()["n"] == 0
    monkeypatch.undo()
    monkeypatch.setattr(pei, "pinned_source_bytes", lambda file_id, sha: True)
    summary = pei.reclassify(db)
    assert summary["usable_before"] == {} and summary["usable_after"] == {"fill_blank": 2, "single_choice": 1}
    assert summary["skipped_before"].get("single_choice:choice_options_not_parsed") == 1
    assert summary["skipped_after"] == {}
    assert summary["split_blanks"] == 2
    report = pei.import_breakdown(db)
    assert report["usable_by_type"] == {"fill_blank": 2, "single_choice": 1}
    assert len(report["samples"]["split_blank"]) == 2 and report["samples"]["split_blank"][0]["of"] == 2


def test_a_started_item_is_never_replaced_by_the_split(db, corpus):
    pei.import_items(db)
    row = db.execute("SELECT id FROM past_exam_items WHERE source_blank_index=1").fetchone()
    db.execute("UPDATE past_exam_items SET status='queued' WHERE id=%s", (row["id"],))
    pei.reclassify(db)
    assert db.execute("SELECT status FROM past_exam_items WHERE id=%s", (row["id"],)).fetchone()["status"] == "queued"
