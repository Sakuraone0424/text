"""Questions written from a literature list, pointing at a figure that is not there, or giving the answer away; and a
bank that goes on filling after the first paper."""
import pytest

from app import auto_quality, cost_control, evidence_filter, exam_authoring
from app.common import ensure_user
from test_ai_automation import setup_ai  # noqa: F401

IZAWA = "6. Izawa D, Pines J. How APC/C-cdc20 changes its substrate specificity in mitosis. Nature Cell Biology, 2011, 13(3): 223-233."
HOLMGREN = "Holmgren M, Wagg J, Bezanilla F, et al. Three distinct and sequential steps in the release of sodium ions by Na+/K+-ATPase. Nature, 2000, 403: 898-901."


@pytest.mark.parametrize("text", [IZAWA, HOLMGREN, "[12] 王某, 李某. 细胞周期调控研究进展[J]. 生物化学杂志, 2015, 31(2): 100-108.", "参考文献\\n1. Smith J. Cell. 2001, 5: 1-9."])
def test_literature_entries_are_not_evidence(text):
    assert evidence_filter.is_reference_entry(text)
    assert evidence_filter.reason_unusable(text + " " * 0 + "x" * 80) == "reference_list"


def test_ordinary_textbook_prose_is_still_evidence():
    prose = ("动粒是着丝粒区域组装的蛋白质复合体，分为内板、中间区和外板三层。外板与纺锤体微管直接结合，"
             "在2011年之前人们认为该结构只负责附着，而后续研究表明它还参与纺锤体检验点信号的传递，整个过程需要多种激酶参与。")
    assert not evidence_filter.is_reference_entry(prose)
    assert evidence_filter.reason_unusable(prose) is None


def question(**over):
    q = dict(subject="cell_biology", question_type="single_choice", max_points=4, stem="下列叙述正确的是哪一项？",
             options={"A": "甲", "B": "乙", "C": "丙", "D": "丁"}, answer="A", accepted_answers=[], blanks=[],
             rubric=[dict(description="d", points=4)])
    q.update(over)
    return q


@pytest.mark.parametrize("stem", ["关于信号复合物装配的三种类型（图11-9A、B、C），下列哪项正确？", "如图11-9所示，哪项正确？", "下图中哪一项正确？", "根据图 3-2 判断"])
def test_a_stem_pointing_at_a_figure_is_flagged(stem):
    assert "unavailable_figure_reference" in exam_authoring.issues(question(stem=stem))


def test_an_option_pointing_at_a_figure_is_flagged():
    assert "unavailable_figure_reference" in exam_authoring.issues(question(options={"A": "如图所示的结构", "B": "乙", "C": "丙", "D": "丁"}))


def test_a_stem_without_a_figure_is_not_flagged():
    assert "unavailable_figure_reference" not in exam_authoring.issues(question(stem="糖酵解的关键限速酶是哪一个？"))


def fill(stem, answer, accepted=None):
    return question(question_type="fill_blank", stem=stem, options={}, answer=answer, accepted_answers=accepted or [answer])


def test_an_answer_written_in_the_stem_is_flagged():
    leaked = fill("在纺锤体组装中，位于动粒外侧、与纺锤体微管附着直接相关的结构部位是动粒的____。", "外")
    assert "answer_disclosed_in_stem" in exam_authoring.issues(leaked)
    assert "answer_disclosed_in_stem" in exam_authoring.issues(fill("ATP水解释放能量，供给____（ATP）做功。", "ATP"))


def test_a_fill_in_whose_answer_is_not_in_the_stem_is_not_flagged():
    assert "answer_disclosed_in_stem" not in exam_authoring.issues(fill("糖酵解的第一个限速酶是____。", "己糖激酶"))
    assert "answer_disclosed_in_stem" not in exam_authoring.issues(fill("人类染色体数目为____条，其中含有性染色体。", "46"))


# ---- the bank keeps filling after a paper -----------------------------------------------------------------------
def test_supply_counts_unused_questions_only(db, running, blueprint):
    uid, paper = running                                         # a started paper: every question of the bank is on it
    ids = [i["id"] for i in paper["questions"]]
    for qid in ids:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    # the paper holds the questions, so none of them is stock any more
    assert cost_control.bucket_supply(db, "biochemistry", "single_choice") == 0
    assert cost_control.bucket_supply(db, "cell_biology", "term") == 0


def test_a_ready_question_nobody_has_used_is_stock(db, bank):
    for qid in bank:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    assert cost_control.bucket_supply(db, "biochemistry", "single_choice") == 3


def test_the_one_time_clean_up_runs_once(db, bank):
    from app import legacy_reuse
    for qid in bank:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    assert legacy_reuse._state(db, "unusable_sources_v1") is None
    assert auto_quality.retire_unusable_sources(db) == 0       # nothing in this clean bank qualifies
    assert legacy_reuse._state(db, "unusable_sources_v1") == {"retired": 0}
    assert auto_quality.retire_unusable_sources(db) == 0       # and it does not run again


def test_questions_with_a_literature_list_source_are_retired_and_withdrawn(db, bank, monkeypatch):
    for qid in bank:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    victim = bank[0]
    monkeypatch.setattr(evidence_filter, "is_reference_entry", lambda text, _v=[victim]: True)
    assert auto_quality.retire_unusable_sources(db) == len(bank)
    states = {r["status"] for r in db.execute("SELECT status FROM auto_question_states").fetchall()}
    assert states == {"retired"}
    assert {r["status"] for r in db.execute("SELECT status FROM reviewed_questions WHERE id=ANY(%s)", (bank,)).fetchall()} == {"withdrawn"}


# ---- stock = never used, or last used 14+ days ago ----------------------------------------------------------------
def test_stock_is_unused_or_last_used_long_ago(db, running, blueprint):
    from app import auto_generation as gen
    uid, paper = running
    kind_ids = {}
    for i in paper["questions"]:
        kind_ids.setdefault((i["subject"], i["question_type"]), []).append(i["id"])
    for ids in kind_ids.values():
        for qid in ids:
            db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x') ON CONFLICT DO NOTHING", (qid,))
    subject, kind = "biochemistry", "single_choice"
    ids = kind_ids[(subject, kind)]
    # nothing is available right after the paper: it holds every question of the bucket
    assert gen.UNUSED_SQL and cost_control.bucket_supply(db, subject, kind) == 0
    # fourteen days later the paper is old enough to reuse them
    db.execute("ALTER TABLE papers_v2 DISABLE TRIGGER USER")
    db.execute("UPDATE papers_v2 SET paper_date = paper_date - 14")
    assert cost_control.bucket_supply(db, subject, kind) == len(ids)


def test_planning_resumes_after_a_paper_has_used_the_stock(db, bank, blueprint, setup_ai, monkeypatch, tmp_path):
    """A paper consumes the stock; the next planning round must see the new gap and queue a writing request."""
    import test_gap_generation as tg
    from app import auto_generation as gen
    from app import quiz_v2 as q, sources
    from test_ocr_only import material
    from test_past_exam_import import EXAMS

    uid = setup_ai
    monkeypatch.setenv("AI_BANK_TARGET_PER_BUCKET", "3")
    for qid in bank:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    points = {r["k"]: int(r["p"]) for r in db.execute(
        "SELECT content->>'question_type' AS k, max(content->>'max_points') AS p FROM reviewed_questions GROUP BY 1").fetchall()}
    monkeypatch.setattr(gen, "POINTS", {**gen.POINTS, **points})          # the fixture bank uses its own point values
    monkeypatch.setattr(gen, "eligible_verified_reserve", lambda conn, rows, cached=False: len(rows))   # (the review jobs are not the point here)
    before = gen.gap_diagnosis(db, uid)
    path, meta = material(tmp_path, "biochemistry_lower", content=tg.PARAGRAPH)
    sources.import_source(db, path, meta)
    path, meta = material(tmp_path, "biochemistry_exams", content=EXAMS)
    sources.import_source(db, path, meta)
    for module in (tg.pei, gen):
        monkeypatch.setattr(module, "pinned_source_bytes", lambda file_id, sha: True)
        monkeypatch.setattr(module, "source_bibliography", lambda conn, unit: dict(tg.BOOK))
    db.execute("DELETE FROM auto_question_states")              # (the paper builder wants its own review records: set them back after)
    q.create_paper(db, uid, blueprint)                         # a paper takes every question of the bank
    for qid in bank:
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    after = gen.gap_diagnosis(db, uid)
    assert after["gap"] > before["gap"] and after["buckets"] >= before["buckets"]
    assert after["gap"] == 3 * 10                              # 10 buckets, each back to 3 missing
    job = gen.plan_gap(db, uid)
    assert job is not None and job["kind"] == "generation"      # planning is not stuck: a request is queued


# ---- reading-comprehension wording ---------------------------------------------------------------------------------
import pytest as _pytest


@_pytest.mark.parametrize("stem", [
    "根据本章所提供的文献信息，下列哪一项对相关转运蛋白研究的表述与该文献信息一致？",
    "下列哪一项与教材表述完全一致？",
    "根据教材所述，糖酵解的限速酶是哪一个？",
    "Holmgren 等（2000）研究了 Na+/K+-ATPase 的哪个过程？",
    "关于本节介绍的三种信号复合物，下列叙述正确的是",
    "见表 3-1，哪个酶的活性最高？",
    "下列叙述与上述内容相符的是哪一项？",
])
def test_reading_comprehension_wording_is_blocked(stem):
    assert "textbook_wording_in_stem" in exam_authoring.issues(question(stem=stem))


def test_an_option_in_textbook_wording_is_blocked_too():
    assert "textbook_wording_in_stem" in exam_authoring.issues(question(options={"A": "与教材表述一致", "B": "乙", "C": "丙", "D": "丁"}))


@_pytest.mark.parametrize("stem", [
    "糖酵解途径中催化第一步限速反应的酶是哪一个？",
    "下列关于线粒体内膜上ATP合酶的叙述，正确的是",
    "真核生物DNA复制中，冈崎片段的连接由哪种酶完成？",
    "细胞周期中，APC/C在哪一时期被激活？",
])
def test_ordinary_exam_questions_are_not_blocked(stem):
    assert "textbook_wording_in_stem" not in exam_authoring.issues(question(stem=stem))


def test_the_writing_instructions_forbid_it():
    text = exam_authoring.instructions("single_choice", 4)
    for phrase in ("NEVER write 'according to the textbook", "Options compare biological facts", "must not appear anywhere in its stem"):
        assert phrase in text, phrase


def test_past_exam_single_choice_goes_first_and_ai_singles_cover_only_the_rest():
    from app import auto_generation as gen, past_exam_import as pei
    assert pei._PRIORITY_SQL.index("'single_choice' THEN 0") < pei._PRIORITY_SQL.index("'term'")
    import inspect
    assert "'single_choice'" in inspect.getsource(gen.bucket_detail)
