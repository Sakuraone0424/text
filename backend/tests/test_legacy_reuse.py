"""Step 3.5: reuse of the old proposals and the old all-year index. Fake model only.

Synthetic data is written to a temp directory; the real files are read only for
shape/count checks and never printed or copied.
"""
import json
import re
from pathlib import Path

import pytest

from app import auto_quality, auto_worker, legacy_reuse as lr, past_exam_import as pei, sources
from app import auto_generation as gen
from app.source_policy import FILES
from test_ai_automation import setup_ai, synthetic_result  # noqa: F401  (fixture/helper)
from test_gap_generation import BOOK
from test_ocr_only import material
from test_past_exam_import import EXAMS, finish_job, model_result, queued_jobs, review_result

KINASE = "星尘激酶是细胞内催化底物磷酸化的一类合成测试酶，其活性受别构调节，并在能量充足时被抑制，在能量不足时被激活，从而维持细胞能量平衡。"
SHARED = "这是两个不同页面里逐字相同的一句合成测试文字，用于检验唯一命中。"
LYSE = ("冈崎片段是DNA复制过程中滞后链上不连续合成的短DNA片段，由RNA引物引发，聚合酶延伸后引物被切除，"
        "缺口由连接酶封闭，最终各个短片段连成完整连续的新生DNA链，从而保证滞后链复制的完成。")
MARK = "旧解析标记ZZ9"
FAKE_TERMS = ["星尘激酶", "月光磷酸", "雾凇合酶", "琥珀转移", "青岚水解", "赤霞氧化",
              "墨羽连接", "银汉还原", "金乌异构", "玄冰裂解", "紫电羧化", "碧落脱氢"]


def unique_textbook(tmp_path, key, special):
    """A textbook whose every page is different; `special` maps page -> body."""
    path, meta = material(tmp_path, key)
    label = next(f for f in FILES.values() if f["key"] == key).get("page_label")
    prefix = f"{label} " if label else ""
    pages = []
    for page in range(1, meta["expected_pages"] + 1):
        body = special.get(page) or f"合成教材第{page:04d}页的普通说明文字，仅用于测试，请忽略。" + "无关填充" * 12
        pages.append(f"===== {prefix}page-{page:03} =====\n第一章 合成测试\n{body}\n")
    path.write_text("".join(pages), encoding="utf-8")
    return path, meta


def book_pages():
    special = {5: LYSE, 10: KINASE, 20: SHARED + "甲页补充说明。" * 8, 21: SHARED + "乙页补充说明。" * 8}
    for i, term in enumerate(FAKE_TERMS):
        special[100 + 7 * i] = f"{term}是一种合成测试用的虚构分子，参与虚构的代谢过程并受到虚构的调节，在合成测试中只用来定位页码，并无任何真实含义，也不对应真实教材的内容。"
    return special


@pytest.fixture
def books(db, tmp_path, monkeypatch):
    path, meta = unique_textbook(tmp_path, "biochemistry_lower", book_pages())
    sources.import_source(db, path, meta)
    path, meta = material(tmp_path, "biochemistry_exams", content=EXAMS)
    sources.import_source(db, path, meta)
    for module in (pei, gen, lr):  # synthetic fixtures are not the user's locked bytes
        monkeypatch.setattr(module, "pinned_source_bytes", lambda file_id, sha: True)
    for module in (pei, gen, lr):
        monkeypatch.setattr(module, "source_bibliography", lambda conn, unit: dict(BOOK))
    monkeypatch.delenv("AI_FULL_CORPUS_ENABLED", raising=False)
    return FILES_BY_KEY["biochemistry_lower"]["file_id"]


FILES_BY_KEY = {f["key"]: f for f in FILES.values()}


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def proposal(draft_id, excerpt, file_id, kind="single_choice", stem=None):
    question = dict(
        subject="biochemistry", question_type=kind, difficulty="core", max_points=2,
        stem=stem or "下列关于星尘激酶的叙述，哪一项是正确的？",
        options=dict(A="活性受别构调节", B="只能被激活", C="与能量无关", D="不催化反应") if kind == "single_choice" else {},
        answer="A" if kind == "single_choice" else "星尘激酶",
        accepted_answers=[] if kind == "single_choice" else ["星尘激酶"],
        blanks=[], style_reference_ids=[], knowledge_tags=["能量代谢"],
        rubric=[dict(description="答对得2分", points=2, knowledge_tags=["能量代谢"])],
        evidence=[dict(authors="旧作者", chapter="旧章节", edition="旧版", excerpt=excerpt, file_id=file_id,
                       isbn=None, locator="OCR:1:UNIT:1", ocr_page=1, paragraph_id=None,
                       source_sha256="a" * 64, source_unit_id=None, textbook="旧书")],
    )
    return dict(admission_forbidden=True, derived_from=draft_id, draft_id=draft_id, question=question,
                revision_proposal_id=None, source_chain=[{"page": 1}], status="BOOK_EXACT_TOPIC_PROPOSAL_NOT_ADMITTED",
                tag_evidence=[{"candidate_answer_exact_normalized": True}], unapproved_independent_reviews=3)


def packets(db):
    return db.execute("SELECT * FROM candidate_review_packets ORDER BY question_id").fetchall()


# ---- 1. old proposals -> ordinary generated candidates --------------------------


def test_proposal_is_rebound_to_the_database_and_no_old_state_survives(db, books, tmp_path):
    path = write(tmp_path, "p.json", [proposal("D-1", KINASE[:40] + KINASE[40:70], books)])
    assert lr.import_proposals(db, path) == {"imported": 1}
    (packet,) = packets(db)
    ev = packet["question_snapshot"]["evidence"][0]
    unit = db.execute("SELECT id,ocr_global_page FROM source_units WHERE text LIKE %s", ("%" + KINASE,)).fetchone()
    assert unit["ocr_global_page"] == 10  # the page numbering the hints are compared against
    assert (ev["source_unit_id"], ev["paragraph_id"], ev["ocr_page"]) == (unit["id"], unit["id"], 10)
    assert ev["locator"] == f"OCR:10:UNIT:{unit['id']}" and ev["textbook"] == BOOK["textbook"]
    assert ev["file_id"] == books and ev["source_sha256"] != "a" * 64
    blob = json.dumps([dict(p) for p in packets(db)], ensure_ascii=False, default=str)
    for old in ("NOT_ADMITTED", "admission_forbidden", "unapproved_independent_reviews", "旧作者", "旧书"):
        assert old not in blob
    assert packet["ai_analysis"]["independent_human_review"] is False
    assert packet["ai_analysis"]["status"].startswith("AI自动核验")
    origin = db.execute("SELECT * FROM question_origins WHERE question_id=%s", (packet["question_id"],)).fetchone()
    assert origin["origin"] == "generated" and origin["source_ref"] == {"legacy_draft_id": "D-1"}
    row = db.execute("SELECT * FROM legacy_proposals").fetchone()
    assert (row["draft_id"], row["status"], row["question_id"]) == ("D-1", "imported", packet["question_id"])


def test_imported_proposal_still_needs_a_fresh_independent_review(db, books, tmp_path, setup_ai):
    path = write(tmp_path, "p.json", [proposal("D-1", KINASE[:60], books)])
    lr.import_proposals(db, path)
    qid = packets(db)[0]["question_id"]
    assert not auto_quality.ready(db, qid)  # nothing is approved by being imported
    (job,) = auto_quality.schedule_checks(db, setup_ai, qid)
    quote = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]["evidence"][0]["excerpt"]
    assert not auto_quality.ready(db, qid)
    assert auto_quality.accept_round(db, synthetic_result(job, db, quote=quote))
    assert auto_quality.promote(db, qid) and auto_quality.ready(db, qid)


def test_fill_blank_proposal_is_supported_too(db, books, tmp_path):
    path = write(tmp_path, "p.json", [proposal("D-2", KINASE[:60], books, "fill_blank", stem="催化底物磷酸化的合成测试酶称为____。")])
    assert lr.import_proposals(db, path) == {"imported": 1}


@pytest.mark.parametrize(
    "excerpt,file_key,reason",
    [
        (SHARED, "biochemistry_lower", "evidence_ambiguous"),
        ("教材里根本找不到的一句话，因此无法绑定到任何段落。", "biochemistry_lower", "evidence_not_found"),
        (KINASE[:60], "biochemistry_upper", "evidence_book_mismatch"),
    ],
)
def test_evidence_must_hit_exactly_one_paragraph_of_the_right_book(db, books, tmp_path, excerpt, file_key, reason):
    path = write(tmp_path, "p.json", [proposal("D-3", excerpt, FILES_BY_KEY[file_key]["file_id"])])
    assert lr.import_proposals(db, path) == {reason: 1}
    assert packets(db) == []
    row = db.execute("SELECT * FROM legacy_proposals").fetchone()
    assert (row["status"], row["blocked_reason"], row["question_id"]) == ("blocked", reason, None)


def test_rerun_does_nothing_and_unloaded_sources_are_not_blocked_for_good(db, tmp_path, books):
    path = write(tmp_path, "p.json", [proposal("D-1", KINASE[:60], books)])
    lr.import_proposals(db, path)
    assert lr.import_proposals(db, path) == {}
    assert db.execute("SELECT count(*) AS n FROM legacy_proposals").fetchone()["n"] == 1


def test_proposals_wait_for_the_textbook_instead_of_being_blocked(db, tmp_path):
    path = write(tmp_path, "p.json", [proposal("D-1", KINASE[:60], "x" * 33)])
    assert lr.import_proposals(db, path) == {}
    assert db.execute("SELECT count(*) AS n FROM legacy_proposals").fetchone()["n"] == 0


def test_a_missing_style_anchor_is_retried_later_not_recorded(db, books, tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "select_exam_style", lambda *a, **k: None)
    path = write(tmp_path, "p.json", [proposal("D-1", KINASE[:60], books)])
    assert lr.import_proposals(db, path) == {"deferred": 1}
    assert db.execute("SELECT count(*) AS n FROM legacy_proposals").fetchone()["n"] == 0


def test_batches_are_bounded(db, books, tmp_path):
    many = [proposal(f"D-{i}", SHARED, books) for i in range(25)]  # all ambiguous -> recorded blocked
    path = write(tmp_path, "p.json", many)
    assert sum(lr.import_proposals(db, path).values()) == lr.PROPOSALS_PER_TICK
    assert sum(lr.import_proposals(db, path).values()) == lr.PROPOSALS_PER_TICK


def test_real_proposal_file_shape():
    data = json.loads(lr.PROPOSALS_FILE.read_text(encoding="utf-8"))
    assert len(data) == 120
    kinds = {(d["question"]["question_type"], d["question"]["max_points"]) for d in data}
    assert kinds == {("single_choice", 2), ("fill_blank", 2)}
    assert all(d["admission_forbidden"] is True for d in data)  # ignored, never trusted
    assert len({d["draft_id"] for d in data}) == 120


# ---- 2. old master index -> hints only -------------------------------------------


def record(rid, stem, section="名词解释", kind="二手历年真题汇编DOCX", pages=None, explanation=MARK, year=2023, **extra):
    base = dict(
        id=rid, subject="生化", year=year, section=section, stem=stem, source_kind=kind, issues=[],
        source_audit_20261018={"possible_truncation_or_wording_issue": False},
        independent_detailed_review_2026_10_17=dict(answer_summary="摘要", detailed_explanation=explanation),
        book_body_pages_verified=[dict(source="生物化学下册", pdf_pages=pages or [], printed_pages=[p + 9 for p in pages or []])],
    )
    base.update(extra)
    return base


def test_only_text_checked_unflagged_known_type_entries_are_usable():
    index = {"records": [
        record("ok", "冈崎片段"),
        record("photo", "冈崎片段", kind="辅导资料翻印照片"),
        record("scan", "冈崎片段", kind="辅导资料扫描PDF"),
        record("pdf", "冈崎片段", kind="辅导资料PDF文字版"),
        record("issue", "冈崎片段", issues=["x"]),
        record("cut", "冈崎片段", source_audit_20261018={"possible_truncation_or_wording_issue": True}),
        record("essay", "冈崎片段", section="论述题"),
        record("blank", "   "),
        record("nosubject", "冈崎片段", subject="物理"),
    ]}
    assert [r["id"] for r in lr.usable_records(index)] == ["ok", "pdf"]


def test_real_index_usable_counts():
    index = json.loads(lr.INDEX_FILE.read_text(encoding="utf-8"))
    usable = list(lr.usable_records(index))
    assert len(index["records"]) == 1908 and len(usable) == 1278
    assert {r["kind"] for r in usable} == {"term", "fill_blank", "short_answer", "single_choice"}
    assert all(r["stem"].strip() for r in usable)


def exam_items(db):
    pei.import_items(db)
    return {(r["question_type"], r["question_number"]): r for r in db.execute("SELECT * FROM past_exam_items").fetchall()}


def test_hints_are_kept_only_for_stems_that_match_an_item(db, books, tmp_path):
    items = exam_items(db)
    index = {"records": [
        record("m-old", "1. 冈崎片段", year=2021, pages=[40], explanation="较早年份的解析"),
        record("m-new", "冈崎片段", year=2024, pages=[41, 42]),
        record("photo", "冈崎片段", kind="辅导资料翻印照片", pages=[900], explanation="照片来源不可用"),
        record("loose", "完全不在真题里的另一道题", pages=[7], explanation="无关解析内容"),
        record("choice", "5. 下列哪种酶负责封闭冈崎片段之间的缺口？ A. DNA连接酶 B. 拓扑异构酶 C. 解旋酶 D. 引发酶",
               section="选择题", pages=[50], explanation="选择题解析"),
    ]}
    counts = lr.import_hints(db, write(tmp_path, "i.json", index))
    assert counts["matched_items"] == 2
    rows = {r["item_id"]: r for r in db.execute("SELECT * FROM legacy_hints").fetchall()}
    term = rows[items[("term", "1")]["id"]]
    assert term["record_id"] == "m-new" and term["explanation"] == MARK  # newest year wins
    assert term["pages"] == [dict(file_key="biochemistry_lower", pdf_pages=[40, 41, 42], printed_pages=[49, 50, 51])]
    assert rows[items[("single_choice", "5")]["id"]]["explanation"] == "选择题解析"
    stored = json.dumps([dict(r) for r in rows.values()], ensure_ascii=False, default=str)
    assert "照片来源不可用" not in stored and "无关解析内容" not in stored and "较早年份的解析" not in stored


def test_explanation_is_capped(db, books, tmp_path):
    exam_items(db)
    index = {"records": [record("m", "冈崎片段", explanation="很长" * 3000)]}
    lr.import_hints(db, write(tmp_path, "i.json", index))
    assert len(db.execute("SELECT explanation FROM legacy_hints").fetchone()["explanation"]) == lr.MAX_EXPLANATION_CHARS


# ---- page calibration ---------------------------------------------------------------


def calibration_world(db, tmp_path, pdf_for, printed_for=None):
    """Past-exam items for the fake terms plus an index with the given page lists."""
    document = db.execute("SELECT d.id AS d, min(u.id) AS u FROM source_documents d JOIN source_units u ON u.document_id=d.id "
                          "WHERE d.metadata->>'source_kind'='exam_transcription' GROUP BY d.id").fetchone()
    records = []
    for i, term in enumerate(FAKE_TERMS):
        db.execute(
            "INSERT INTO past_exam_items(subject,question_type,stem,stem_hash,document_id,unit_id,question_number,year,years) "
            "VALUES ('biochemistry','term',%s,%s,%s,%s,%s,2024,'[2024]')",
            (term, pei.stem_hash("term", term), document["d"], document["u"], str(i + 1)),
        )
        pdf = pdf_for(i)
        rec = record(f"r{i}", term, pages=pdf)
        if printed_for:
            rec["book_body_pages_verified"][0]["printed_pages"] = printed_for(i)
        records.append(rec)
    lr.import_hints(db, write(tmp_path, "i.json", {"records": records}))


def page_of(i):
    return 100 + 7 * i


def item_id(db, term):
    return db.execute("SELECT id FROM past_exam_items WHERE stem=%s", (term,)).fetchone()["id"]


def test_a_constant_offset_is_detected_and_pages_become_hints(db, books, tmp_path):
    # index pdf pages are 3 below the OCR page, listed as a 3-page topic span
    calibration_world(db, tmp_path, lambda i: [page_of(i) - 4, page_of(i) - 3, page_of(i) - 2])
    assert lr.calibrate_pages(db, min_samples=8)["biochemistry_lower"] is True
    cal = db.execute("SELECT * FROM legacy_page_calibration WHERE file_key='biochemistry_lower'").fetchone()
    assert cal["verified"] and cal["page_column"] == "pdf_pages" and cal["agreeing"] >= 0.25 * cal["samples"]
    hints = lr.page_hints(db, item_id(db, FAKE_TERMS[3]))
    assert page_of(3) in hints[books]  # the true OCR page is among the hinted pages
    other = lr.calibrate_pages(db, min_samples=8)
    assert other["cell_biology"] is False  # a book with no data is never verified


def test_the_printed_page_column_is_chosen_when_only_it_lines_up(db, books, tmp_path):
    calibration_world(
        db, tmp_path,
        pdf_for=lambda i: [(i * 37) % 560 + 1, (i * 91) % 560 + 3],  # unrelated numbers
        printed_for=lambda i: [page_of(i) - 8, page_of(i) - 7],
    )
    assert lr.calibrate_pages(db, min_samples=8)["biochemistry_lower"] is True
    assert db.execute("SELECT page_column FROM legacy_page_calibration WHERE file_key='biochemistry_lower'").fetchone()["page_column"] == "printed_pages"


def test_unrelated_page_numbers_are_never_used(db, books, tmp_path):
    calibration_world(db, tmp_path, lambda i: [(i * 53) % 560 + 1, (i * 131) % 560 + 2, (i * 17) % 560 + 3])
    assert lr.calibrate_pages(db, min_samples=8)["biochemistry_lower"] is False
    assert lr.page_hints(db, item_id(db, FAKE_TERMS[3])) == {}


def test_too_few_measurements_do_not_verify(db, books, tmp_path):
    calibration_world(db, tmp_path, lambda i: [page_of(i) - 3])
    assert lr.calibrate_pages(db)["biochemistry_lower"] is False  # 12 samples < 30 by default


def test_page_hints_raise_a_matching_paragraph_but_never_admit_a_non_matching_one(db, books):
    left, right = [r["id"] for r in db.execute(
        "SELECT id FROM source_units WHERE ocr_global_page IN (20,21) ORDER BY ocr_global_page").fetchall()]
    text = "这是两个不同页面里逐字相同的一句合成测试文字"
    assert [u["unit_id"] for u in pei.retrieve_evidence(db, "biochemistry", text)][0] == left  # tie -> lower id
    boosted = pei.retrieve_evidence(db, "biochemistry", text, {books: {21}})
    assert boosted[0]["unit_id"] == right
    unrelated = pei.retrieve_evidence(db, "biochemistry", "星尘激酶", {books: {300}})
    assert all("星尘激酶" in u["text"] for u in unrelated)  # page 300 matches nothing and is not added
    assert pei.retrieve_evidence(db, "biochemistry", "完全无关的内容文字", {books: {10}}) == []


# ---- the unreviewed draft: answer writing only -----------------------------------------


def hinted_term(db, explanation=MARK):
    items = exam_items(db)
    term = items[("term", "1")]
    db.execute("INSERT INTO legacy_hints(item_id,record_id,record_year,pages,answer_summary,explanation) "
               "VALUES (%s,'r',2024,'[]','摘要',%s)", (term["id"], explanation))
    return term


def test_draft_goes_to_answer_writing_with_a_warning_and_only_for_hinted_items(db, books, setup_ai):
    term = hinted_term(db)
    pei.plan(db, setup_ai)
    jobs = db.execute("SELECT * FROM ai_jobs WHERE payload->>'origin'='past_exam' ORDER BY id").fetchall()
    by_item = {j["payload"]["item_id"]: j["payload"] for j in jobs}
    hinted = by_item[term["id"]]
    assert hinted["unreviewed_reference_draft"]["explanation"] == MARK
    assert "UNREVIEWED" in hinted["unreviewed_reference_draft"]["status"]
    assert "may be wrong" in hinted["instructions"] and "textbook units are the only authority" in hinted["instructions"]
    assert all("unreviewed_reference_draft" not in p for i, p in by_item.items() if i != term["id"])


def test_draft_is_dropped_before_any_textbook_paragraph_when_the_request_is_too_big(db, books, setup_ai, monkeypatch):
    term = hinted_term(db)
    sizes = []
    original = pei.enqueue

    def tight(conn, uid, key, kind, payload):
        sizes.append(("unreviewed_reference_draft" in payload, len(payload["evidence"])))
        if "unreviewed_reference_draft" in payload:
            raise ValueError("bounded_input_exceeded")
        return original(conn, uid, key, kind, payload)

    monkeypatch.setattr(pei, "enqueue", tight)
    pei._enqueue_item(db, setup_ai, db.execute("SELECT * FROM past_exam_items WHERE id=%s", (term["id"],)).fetchone())
    assert sizes[0][0] is True and sizes[1][0] is False and sizes[0][1] == sizes[1][1]


def test_the_review_never_sees_the_draft(db, books, setup_ai):
    term = hinted_term(db)
    pei.plan(db, setup_ai)
    for job in queued_jobs(db).values():
        finish_job(db, job, model_result(job))
    auto_worker.dispatch_completed(db)
    qid = db.execute("SELECT question_id FROM past_exam_items WHERE id=%s", (term["id"],)).fetchone()["question_id"]
    assert qid is not None
    reviews = db.execute("SELECT * FROM ai_jobs WHERE kind='quality_review' ORDER BY id").fetchall()
    assert len(reviews) >= 1
    sent = json.dumps([r["payload"] for r in reviews], ensure_ascii=False)
    assert MARK not in sent and "unreviewed_reference_draft" not in sent and "摘要" not in sent
    stored = json.dumps([dict(p) for p in packets(db)], ensure_ascii=False, default=str)
    content = json.dumps(db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"], ensure_ascii=False)
    assert MARK not in stored and MARK not in content


# ---- wiring, privacy -----------------------------------------------------------------------


def test_plan_orders_the_work_and_is_idempotent(db, books, tmp_path, monkeypatch):
    props = write(tmp_path, "p.json", [proposal("D-1", KINASE[:60], books)])
    index = write(tmp_path, "i.json", {"records": [record("m", "冈崎片段", pages=[5])]})
    monkeypatch.setattr(lr, "PROPOSALS_FILE", props)
    monkeypatch.setattr(lr, "INDEX_FILE", index)
    first = lr.plan(db)
    assert first["proposals"] == {"imported": 1} and first["hints"]["matched_items"] == 1 and "calibration" in first
    assert lr.plan(db) == {"proposals": {}}  # nothing left to do, nothing recomputed


def test_missing_files_are_skipped_quietly(db, books, tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "PROPOSALS_FILE", tmp_path / "absent1.json")
    monkeypatch.setattr(lr, "INDEX_FILE", tmp_path / "absent2.json")
    assert lr.plan(db) == {"proposals": {}}


def test_file_contents_are_never_logged_or_printed(db, books, tmp_path, monkeypatch, caplog, capsys):
    import logging

    caplog.set_level(logging.DEBUG)
    secret = "绝密教材摘录标记QQ7"
    props = write(tmp_path, "p.json", [proposal("D-1", KINASE[:60], books), proposal("D-9", secret, books)])
    index = write(tmp_path, "i.json", {"records": [record("m", "冈崎片段", pages=[5], explanation=secret)]})
    monkeypatch.setattr(lr, "PROPOSALS_FILE", props)
    monkeypatch.setattr(lr, "INDEX_FILE", index)
    lr.plan(db)
    out = capsys.readouterr()
    assert secret not in caplog.text and secret not in out.out and secret not in out.err
    assert KINASE[:20] not in caplog.text and KINASE[:20] not in out.out


def test_the_data_files_ship_only_in_the_ai_worker_image():
    root = Path(__file__).resolve().parents[2]
    names = (lr.PROPOSALS_FILE.name, lr.INDEX_FILE.name)
    worker = (root / "deploy/railway-automation/Dockerfile").read_text()
    api = (root / "backend/Dockerfile").read_text()
    for name in names:
        assert re.search(rf"^COPY .*backend/data/{re.escape(name)} ./data/{re.escape(name)}$", worker, re.M)
        assert name not in api
    assert not re.search(r"COPY .*backend/data/?\s", worker + api)  # never the whole directory
    for path in (root / "backend/app").glob("*.py"):  # no other module reads the files
        if path.name != "legacy_reuse.py":
            assert not any(n in path.read_text(encoding="utf-8") for n in names), path.name
