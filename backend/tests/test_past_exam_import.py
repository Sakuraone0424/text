"""Past-exam import: verbatim stems, year de-duplication, textbook-only answers.

Everything here is synthetic. DeepSeek is replaced by hand-written job results
(`real_api=True` is only the marker the production code requires of a finished
job); no request is ever sent.
"""
import json

import pytest
from psycopg.types.json import Jsonb

from app import auto_quality, auto_worker, past_exam_import as pei, sources
from app.common import ensure_user
from app.training_spec_v4 import POINTS
from test_ai_automation import setup_ai  # noqa: F401  (fixture)
from test_ocr_only import material

TEXTBOOK = (
    "冈崎片段是DNA复制过程中滞后链上不连续合成的短DNA片段，由RNA引物引发，"
    "经DNA聚合酶延伸之后，RNA引物被切除，缺口由DNA连接酶封闭，"
    "最终各片段连接成连续完整的DNA链，保证滞后链的复制得以完成。"
)
EXAMS = [
    "2023年中国科学技术大学生物化学真题",
    "名词解释",
    "1. 冈崎片段",
    "2. 如图所示，指出该代谢途径的限速酶。",
    "2024年中国科学技术大学生物化学真题",
    "名词解释",
    "1. 冈崎片段",
    "简答题",
    "2. 简述DNA复制中滞后链的合成过程。",
    "填空题",
    "3. DNA复制时，滞后链上合成的短片段称为______。",
    "4. 糖酵解中____和____是两个关键的限速酶。",
    "单项选择题",
    "5. 下列哪种酶负责封闭冈崎片段之间的缺口？",
    "A. DNA连接酶",
    "B. 拓扑异构酶",
    "C. 解旋酶",
    "D. 引发酶",
    "6. 下列叙述中正确的一项是",
    "2025年中国科学技术大学生物化学真题",
]


@pytest.fixture
def corpus(db, tmp_path, monkeypatch):
    path, meta = material(tmp_path, "biochemistry_lower", content=TEXTBOOK)
    sources.import_source(db, path, meta)
    path, meta = material(tmp_path, "biochemistry_exams", content=EXAMS)
    sources.import_source(db, path, meta)
    # Synthetic fixtures are not the user's locked bytes; bypass only that pin.
    monkeypatch.setattr(pei, "pinned_source_bytes", lambda file_id, sha: True)
    monkeypatch.setattr(
        pei, "source_bibliography",
        lambda conn, unit: dict(textbook="Synthetic", authors="Test", edition="Test"),
    )


def items(db):
    return {
        (r["question_type"], r["question_number"]): r
        for r in db.execute("SELECT * FROM past_exam_items").fetchall()
    }


def test_repeated_stem_keeps_latest_year_and_all_years_verbatim(db, corpus):
    summary = pei.import_items(db)
    term = items(db)[("term", "1")]
    assert term["stem"] == "冈崎片段"  # only the leading number is dropped
    assert term["year"] == 2024 and term["years"] == [2024, 2023]
    assert [o["year"] for o in term["occurrences"]] == [2024, 2023]
    assert summary["duplicates_collapsed"] == 1
    assert db.execute(
        "SELECT count(*) AS n FROM past_exam_items WHERE stem='冈崎片段'"
    ).fetchone()["n"] == 1


def test_import_is_idempotent(db, corpus):
    pei.import_items(db)
    before = db.execute("SELECT * FROM past_exam_items ORDER BY id").fetchall()
    again = pei.import_items(db)
    assert again["created"] == 0 and again["merged"] == 0
    assert db.execute("SELECT * FROM past_exam_items ORDER BY id").fetchall() == before


def test_unusable_questions_are_blocked_with_a_reason_and_never_queued(db, corpus):
    pei.import_items(db)
    got = items(db)
    reasons = {key: r["blocked_reason"] for key, r in got.items() if r["status"] == "blocked"}
    assert reasons == {
        ("term", "2"): "image_dependent",
        ("fill_blank", "4"): "fill_blank_marker_unsupported",
        ("single_choice", "6"): "choice_options_not_parsed",
    }
    assert got[("short_answer", "2")]["status"] == "imported"
    fill = got[("fill_blank", "3")]
    assert fill["stem"] == "DNA复制时，滞后链上合成的短片段称为____。" and fill["stem_normalized"]
    choice = got[("single_choice", "5")]
    assert choice["stem"] == "下列哪种酶负责封闭冈崎片段之间的缺口？"
    assert choice["options"] == {"A": "DNA连接酶", "B": "拓扑异构酶", "C": "解旋酶", "D": "引发酶"}


def test_choice_and_blank_parsers():
    assert pei.parse_choice("题干很长很长\nA. 一\nB. 二\nC. 三\nD. 四") == (
        "题干很长很长", {"A": "一", "B": "二", "C": "三", "D": "四"})
    assert pei.parse_choice("题干很长很长 A. 一 B. 二 C. 三") is None
    assert pei.parse_choice("题干很长很长 A. 一 C. 三 B. 二 D. 四") is None
    assert pei.normalize_blank("称为（　）。") == "称为____。"
    assert pei.normalize_blank("A____B____") is None
    assert pei.normalize_blank("没有空") is None


def test_question_without_textbook_evidence_is_blocked_before_any_model_call(db, corpus, setup_ai):
    pei.import_items(db)
    db.execute(
        "UPDATE past_exam_items SET stem='试述核糖体上蛋白质翻译延伸的能量来源',stem_hash='x' "
        "WHERE question_type='short_answer'"
    )
    pei.plan(db, setup_ai)
    row = items(db)[("short_answer", "2")]
    assert (row["status"], row["blocked_reason"]) == ("blocked", "no_textbook_evidence")
    assert row["job_id"] is None
    assert db.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE payload->>'origin'='past_exam' AND payload->>'item_id'=%s",
        (str(row["id"]),),
    ).fetchone()["n"] == 0


def test_plan_sends_only_a_bounded_set_of_relevant_paragraphs(db, corpus, setup_ai, monkeypatch):
    monkeypatch.delenv("AI_FULL_CORPUS_ENABLED", raising=False)
    assert pei.plan(db, setup_ai) == 2
    jobs = db.execute(
        "SELECT * FROM ai_jobs WHERE kind='generation' AND payload->>'origin'='past_exam'"
    ).fetchall()
    assert len(jobs) == 2
    # Term and short-answer questions are queued first.
    assert {j["payload"]["question_type"] for j in jobs} == {"term", "short_answer"}
    for job in jobs:
        p = job["payload"]
        assert 1 <= len(p["evidence"]) <= pei.MAX_EVIDENCE_UNITS
        assert sum(len(u["text"]) for u in p["evidence"]) <= pei.MAX_EVIDENCE_CHARS
        assert all(u["source_kind"] == "textbook" for u in p["evidence"])
        assert len(json.dumps(p, ensure_ascii=False).encode()) <= 24000
        assert p["max_points"] == POINTS[p["question_type"]]
    # Never more than the concurrency cap, and planning again is idempotent.
    assert pei.plan(db, setup_ai) == 0
    assert db.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE payload->>'origin'='past_exam'"
    ).fetchone()["n"] == 2


def test_oversized_evidence_is_trimmed_never_sent_whole(db, corpus, setup_ai, monkeypatch):
    monkeypatch.setattr(pei, "MAX_EVIDENCE_UNITS", 4)
    original = pei.enqueue
    attempts = []

    def small_limit(conn, uid, key, kind, payload):
        attempts.append(len(payload["evidence"]))
        if len(payload["evidence"]) > 1:
            raise ValueError("bounded_input_exceeded")
        return original(conn, uid, key, kind, payload)

    monkeypatch.setattr(pei, "enqueue", small_limit)
    pei.plan(db, setup_ai, batch=1)
    assert attempts[-1] == 1 and attempts == sorted(attempts, reverse=True)


# ---- model result -> packet -> two independent rounds -----------------------


def model_result(job, *, supported=True, bad_quote=False, bad_points=False):
    p = job["payload"]
    text = p["evidence"][0]["text"]
    uid, kind, points = p["evidence"][0]["unit_id"], p["question_type"], p["max_points"]
    if kind == "single_choice":
        letter = next(k for k, v in p["options"].items() if "连接酶" in v)
        split, answer, accepted = [points], letter, []
    elif kind == "fill_blank":
        split, answer, accepted = [points], "冈崎片段", ["冈崎片段"]
    elif kind == "term":
        split, answer, accepted = [3, 3], "冈崎片段是滞后链上不连续合成的短DNA片段，由RNA引物引发并最终被连接。", []
    elif kind == "essay":
        split, answer, accepted = [4, 4, 4, 3, 3], "滞后链以冈崎片段方式合成：引物引发、聚合酶延伸、引物切除、连接酶封闭缺口并连接成链，并受多种酶协同调控。", []
    else:
        split, answer, accepted = [4, 4, 4], "滞后链以冈崎片段方式合成：引物引发、聚合酶延伸、引物切除、连接酶封闭缺口并连接成链。", []
    if bad_points:
        split = [split[0] + 1] + split[1:]
    rubric = [
        dict(
            description=f"评分要点{i + 1}",
            points=pts,
            unit_id=uid,
            excerpt=("编造的教材原文并不存在" if bad_quote and i == 0 else text[i * 20 : i * 20 + 20]),
        )
        for i, pts in enumerate(split)
    ]
    return dict(
        real_api=True,
        usage=dict(input_tokens=10, output_tokens=10, cache_read_input_tokens=0),
        response=dict(
            supported=supported,
            unsupported_reason=None if supported else "教材未覆盖",
            answer=answer if supported else "",
            accepted_answers=accepted,
            rubric=rubric if supported else [],
            knowledge_tags=["DNA复制"],
            concept="冈崎片段",
            rationale="根据所给教材段落作答",
        ),
    )


def finish_job(db, job, result):
    db.execute("UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s", (Jsonb(result), job["id"]))


def queued_jobs(db):
    return {
        j["payload"]["question_type"]: j
        for j in db.execute(
            "SELECT * FROM ai_jobs WHERE payload->>'origin'='past_exam' ORDER BY id"
        ).fetchall()
    }


def review_result(db, job):
    q = db.execute(
        "SELECT content FROM reviewed_questions WHERE id=%s", (job["payload"]["question_id"],)
    ).fetchone()["content"]
    claims = [
        dict(unit_id=e["source_unit_id"], excerpt=e["excerpt"],
             claim="教材原文支持该评分要点的说法，测试用合成声明",
             criterion_indices=[i])
        for i, e in enumerate(q["evidence"])
    ]
    unit_ids = [e["source_unit_id"] for e in q["evidence"]]
    return dict(
        real_api=True,
        usage=dict(input_tokens=10, output_tokens=10, cache_read_input_tokens=0),
        response=dict(
            passed=True,
            independent_choice=q["answer"] if q["question_type"] == "single_choice" else None,
            checks={a: True for a in auto_quality.AXES},
            claims=claims,
            unresolved=[],
            risk_resolutions={},
            mapping_evidence={tag: unit_ids for tag in q["knowledge_tags"]},
            rationale="这是合成的审核结果，仅用于测试流程，并不是真实的模型调用，也不是任何科学认证。",
        ),
    )


def run_to_ready(db, corpus_user):
    """Plan, answer (fake), dispatch, review once (fake), dispatch again."""
    pei.plan(db, corpus_user)
    for job in queued_jobs(db).values():
        finish_job(db, job, model_result(job))
    auto_worker.dispatch_completed(db)
    for job in db.execute(
        "SELECT * FROM ai_jobs WHERE kind='quality_review' AND state='pending'"
    ).fetchall():
        finish_job(db, job, review_result(db, job))
    auto_worker.dispatch_completed(db)


def test_end_to_end_with_fake_model_term_and_short_answer(db, corpus, setup_ai):
    pei.import_items(db)
    run_to_ready(db, setup_ai)
    got = items(db)
    for kind, number, points, stem in [
        ("term", "1", 6, "冈崎片段"),
        ("short_answer", "2", 12, "简述DNA复制中滞后链的合成过程。"),
    ]:
        item = got[(kind, number)]
        assert item["status"] == "packet_created", item["blocked_reason"]
        q = db.execute(
            "SELECT content FROM reviewed_questions WHERE id=%s", (item["question_id"],)
        ).fetchone()["content"]
        assert q["stem"] == stem  # verbatim
        assert q["max_points"] == points
        assert sum(c["points"] for c in q["rubric"]) == points
        assert len(q["rubric"]) >= 2
        unit_text = {
            r["id"]: r["text"]
            for r in db.execute("SELECT id,text FROM source_units WHERE id=ANY(%s)",
                                ([e["source_unit_id"] for e in q["evidence"]],)).fetchall()
        }
        for e in q["evidence"]:  # every point quotes the textbook and names a page
            assert e["excerpt"] in unit_text[e["source_unit_id"]]
            assert e["ocr_page"] and f"UNIT:{e['source_unit_id']}" in e["locator"]
        packet = db.execute(
            "SELECT ai_analysis FROM candidate_review_packets WHERE question_id=%s", (item["question_id"],)
        ).fetchone()
        assert all(p["excerpt"] and p["ocr_page"] for p in packet["ai_analysis"]["rubric_evidence"])
        origin = db.execute(
            "SELECT * FROM question_origins WHERE question_id=%s", (item["question_id"],)
        ).fetchone()
        assert origin["origin"] == "past_exam" and origin["past_exam_item_id"] == item["id"]
        assert any(k.startswith("unit:") for k in origin["concept_keys"])
        assert "concept:冈崎片段" in origin["concept_keys"]
        # Only the one independent review (plus the free program checks) makes it ready.
        assert auto_quality.ready(db, item["question_id"])


def test_choice_and_blank_items_also_import_with_exact_points(db, corpus, setup_ai):
    pei.import_items(db)
    db.execute("UPDATE past_exam_items SET status='blocked',blocked_reason='figure_reference' "
               "WHERE question_type IN ('term','short_answer')")
    pei.plan(db, setup_ai)
    for job in queued_jobs(db).values():
        finish_job(db, job, model_result(job))
    auto_worker.dispatch_completed(db)
    got = items(db)
    for key, answer in [(("single_choice", "5"), "A"), (("fill_blank", "3"), "冈崎片段")]:
        q = db.execute("SELECT content FROM reviewed_questions WHERE id=%s",
                       (got[key]["question_id"],)).fetchone()["content"]
        assert q["max_points"] == 4 and q["answer"] == answer
        assert q["stem"] == got[key]["stem"]


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        (dict(supported=False), "insufficient_textbook_evidence"),
        (dict(bad_quote=True), "invented_or_unmatched_quote"),
        (dict(bad_points=True), "output_schema_violation"),
    ],
)
def test_unsupported_or_untrustworthy_answers_never_enter_the_bank(db, corpus, setup_ai, kwargs, reason):
    pei.import_items(db)
    pei.plan(db, setup_ai)
    for job in queued_jobs(db).values():
        finish_job(db, job, model_result(job, **kwargs))
    auto_worker.dispatch_completed(db)
    got = items(db)
    for key in (("term", "1"), ("short_answer", "2")):
        assert (got[key]["status"], got[key]["blocked_reason"]) == ("blocked", reason)
        assert got[key]["question_id"] is None
    assert db.execute("SELECT count(*) AS n FROM question_origins").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM candidate_review_packets").fetchone()["n"] == 0


def test_a_changed_textbook_after_planning_invalidates_the_result(db, corpus, setup_ai):
    pei.import_items(db)
    pei.plan(db, setup_ai)
    job = queued_jobs(db)["term"]
    finish_job(db, job, model_result(job))
    stale = dict(job["payload"]["evidence"][0], unit_sha256="0" * 64)
    db.execute("UPDATE ai_jobs SET payload=jsonb_set(payload,'{evidence,0}',%s) WHERE id=%s",
               (Jsonb(stale), job["id"]))
    auto_worker.dispatch_completed(db)
    row = items(db)[("term", "1")]
    assert (row["status"], row["blocked_reason"]) == ("blocked", "evidence_version_changed")


def test_repair_cannot_rewrite_a_past_exam_stem(db, corpus, setup_ai):
    pei.import_items(db)
    run_to_ready(db, setup_ai)
    qid = items(db)[("term", "1")]["question_id"]
    content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
    with pytest.raises(ValueError, match="repair_must_preserve_past_exam_stem"):
        auto_quality.repair(db, qid, {**content, "stem": "改写后的题干，不再是真题原文"},
                            "Rewrote the wording to be clearer for learners.")


# ---- one knowledge point per paper ------------------------------------------


def test_concept_keys_collide_only_on_a_shared_key():
    keys = {1: {"unit:7", "concept:a"}, 2: {"unit:7"}, 3: {"unit:8"}, 4: set()}
    assert pei.concepts_collide(keys, 1, 2)
    assert not pei.concepts_collide(keys, 1, 3)
    assert not pei.concepts_collide(keys, 1, 4) and not pei.concepts_collide(keys, 5, 6)


def test_a_paper_never_contains_two_questions_on_the_same_knowledge_point(db, question_factory, passing_audit, monkeypatch):
    from app import quiz_v2, source_policy
    from app.question_quality import Blueprint, Question
    from spec_helpers import rubric
    from app.training_spec_v4 import COUNTS, POINTS as V2_POINTS, SPECIFICATION, default_blueprint

    ids = {}
    extra = {"biochemistry": ("single_choice", 1)}
    for subject in ("biochemistry", "cell_biology"):
        for kind, count in COUNTS.items():
            for n in range(count + (1 if (subject, kind) == ("biochemistry", "single_choice") else 0)):
                raw = question_factory(subject, kind).model_dump()
                raw["max_points"] = V2_POINTS[kind]
                raw["rubric"] = rubric(kind, V2_POINTS[kind])
                qid = quiz_v2.create_question(db, Question.model_validate(raw))["id"]
                assert quiz_v2.review_question(db, qid, passing_audit)["passed"]
                quiz_v2.publish_question(db, qid)
                db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
                ids.setdefault((subject, kind), []).append(qid)
    del extra
    pool = ids[("biochemistry", "single_choice")]
    assert len(pool) == 11
    first, second = pool[0], pool[1]
    for qid in (first, second):  # same cited paragraph, differently worded questions
        pei.mark_origin(db, qid, "generated", concept_keys=["unit:424242"])
    everything = {q for group in ids.values() for q in group}
    monkeypatch.setattr(auto_quality, "ready", lambda conn, qid: qid in everything)
    monkeypatch.setattr(source_policy, "pinned_science_evidence", lambda q: True)
    uid = ensure_user(db, "concept-student")
    spec = Blueprint.model_validate({"specification": SPECIFICATION, "quotas": default_blueprint()["quotas"]})
    paper = quiz_v2.create_paper(db, uid, spec)
    picked = {r["question_id"] for r in db.execute(
        "SELECT question_id FROM paper_items_v2 WHERE paper_id=%s", (paper["paper_id"],)).fetchall()}
    assert len(picked) == 60
    assert len(picked & {first, second}) == 1


def test_free_check_blocks_a_past_exam_question_whose_stem_no_longer_matches_the_original(db, corpus, setup_ai):
    pei.import_items(db)
    run_to_ready(db, setup_ai)
    item = items(db)[("term", "1")]
    assert auto_quality.ready(db, item["question_id"])
    db.execute("UPDATE past_exam_items SET stem=%s WHERE id=%s", ("另一个被改写过的题干", item["id"]))
    assert not auto_quality.ready(db, item["question_id"])  # the program check, not a model, catches it


def test_a_past_exam_question_discarded_after_its_repair_is_reported_as_review_failed(db, corpus, setup_ai):
    pei.import_items(db)
    pei.plan(db, setup_ai)
    for job in queued_jobs(db).values():
        finish_job(db, job, model_result(job))
    auto_worker.dispatch_completed(db)
    item = items(db)[("term", "1")]
    db.execute("UPDATE auto_question_states SET status='blocked',reason='unresolved_quality_findings',revision_count=1 WHERE question_id=%s",
               (item["question_id"],))
    assert auto_quality.discard_unfixable(db) == [item["question_id"]]
    row = items(db)[("term", "1")]
    assert (row["status"], row["blocked_reason"]) == ("blocked", "review_failed")
