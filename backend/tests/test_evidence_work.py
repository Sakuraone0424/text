"""Synthetic regression evidence only; never a claim of independent human review."""

import pytest
from psycopg.types.json import Jsonb
from psycopg.errors import RaiseException, CheckViolation
from app.review_transcripts import heading, questions_and_years
from app.evidence_work import (
    normalize,
    build,
    persist,
    progress,
    VERSION,
    refined_mapping_work,
)
from test_ocr_only import import_book, approve


def units(texts, numbering=None, positions=None):
    return [
        dict(
            id=i,
            ordinal=i,
            text=t,
            section=(positions or {}).get(i, "body/" + str(i))
            + (numbering or {}).get(i, ""),
            quality_flags=[],
        )
        for i, t in enumerate(texts, 1)
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("一 填空题（每空1分，共40分）", "fill_blank"),
        ("二 名词解释", "term"),
        ("二、填充和选择题：（每小题3分／共1小题）", "mixed"),
        ("六、叙述题和计算题", "mixed"),
        ("二、选择填空(每题2分，共40分)", "mixed"),
    ],
)
def test_real_heading_shapes(text, expected):
    assert heading(text)["type"] == expected


@pytest.mark.parametrize(
    "text",
    [
        "三、问答题（1、2题各14分；3、4题各25分）",
        "四、问答题（第1、2题各20分，共40分；第3、4、5题各15分，任选2题，共30分）",
    ],
)
def test_part_numbering_is_not_declared_total(text):
    assert heading(text)["declared_questions"] is None


def test_missing_delimiter_table_decimals_and_probable_subquestions():
    rows = units(
        [
            "2026年合成考试真题",
            "一 简答题",
            "1. Synthetic main",
            "2另一个主问题",
            "10.4",
            "Synthetic child",
            "Synthetic next",
        ],
        {6: "|num:2:0:decimal:1", 7: "|num:1:0:decimal:3"},
        {5: "body/5/row1/cell1/1"},
    )
    qs, years, parsed = questions_and_years(rows, dict(year_start=2026, year_end=2026))
    assert not any(q["number"] == "10" for q in qs)
    assert (
        parsed[3]["analysis"]["number_recovery"]
        == "missing_delimiter_sequence_supported"
    )
    assert len(qs[0]["blanks"]) == 0
    assert (
        years[0]["human_text_checked"] == 0
        and years[0]["trusted_frequency_eligible"] == 0
    )


def test_distinct_word_lists_are_children_when_main_resumes():
    rows = units(
        [
            "2026年合成考试真题",
            "问答题",
            "Synthetic main",
            "Synthetic child",
            "Synthetic child2",
            "Synthetic next",
        ],
        {
            3: "|num:1:0:decimal:1",
            4: "|num:2:0:decimal:1",
            5: "|num:2:0:decimal:2",
            6: "|num:1:0:decimal:2",
        },
    )
    qs, _, parsed = questions_and_years(rows, dict(year_start=2026, year_end=2026))
    assert len(qs) == 2 and qs[0]["unit_ids"] == [3, 4, 5]
    assert parsed[3]["analysis"]["role"] == "probable_subquestion"


def test_blanks_have_separate_exact_offsets_not_guessed_answers():
    qs, _, _ = questions_and_years(
        units(["2026年合成考试真题", "填空题", "1. Synthetic ___ and （   ）"]),
        dict(year_start=2026, year_end=2026),
    )
    assert len(qs[0]["blanks"]) == 2
    for blank in qs[0]["blanks"]:
        assert qs[0]["text"][blank["start"] : blank["end"]] == blank["marker"]
    assert "answer" not in qs[0]


def test_numbered_blanks_keep_original_global_numbers_without_becoming_subquestions():
    qs, _, _ = questions_and_years(
        units(
            [
                "2026年合成考试真题",
                "填空题",
                "1. 合成（12）及（13）和（）",
                "名词解释",
                "1. Synthetic (12) label",
            ]
        ),
        dict(year_start=2026, year_end=2026),
    )
    assert [b["source_number"] for b in qs[0]["blanks"]] == [12, 13, None]
    assert qs[1]["blanks"] == []


def test_normalization_keeps_scientifically_significant_case_and_symbols():
    assert normalize(" ＡＴＰ  kinase ") == "ATP kinase"
    assert normalize("NADH") != normalize("NADPH")
    assert normalize("ATP") != normalize("atp")
    assert normalize("3′→5′") != normalize("5′→3′")


def test_mapping_attaches_subquestion_hit_to_full_parent_without_approval():
    question = dict(
        unit_ids=[10, 11],
        reference={"unit_id": 10},
        text="Synthetic full main and child",
        year=2026,
    )
    concept = dict(
        exam_unit_ids=[11],
        subject="biochemistry",
        name="kinase",
        textbook_anchors=[{"unit_id": 2, "quote": "Synthetic kinase"}],
        proposal_ids=[4],
    )
    rows = refined_mapping_work([question], [concept])
    assert len(rows) == 1 and rows[0]["source_unit_ids"] == [2, 10, 11]
    assert rows[0]["evidence"]["question_reference"]["unit_id"] == 10
    assert rows[0]["status"] == "pending"
    assert (
        rows[0]["evidence"]["assessment_role"] == "primary_or_secondary_requires_review"
    )
    assert not rows[0]["evidence"]["original_exam_verified"]


def test_space_number_between_adjacent_questions_is_recovered_without_changing_text():
    # Recovery requires Chinese immediately after optional whitespace, as in
    # real OCR; ordinary English continuations stay uncertain.
    rows = units(
        ["2026年合成考试真题", "简答题", "9. 前题", "10 什么是合成概念", "11. 后题"]
    )
    qs, years, _ = questions_and_years(rows, dict(year_start=2026, year_end=2026))
    assert [q["number"] for q in qs] == ["9", "10", "11"]
    assert qs[1]["text"] == "10 什么是合成概念"
    assert years[0]["human_text_checked"] == 0


def test_pending_work_is_resumable_readback_and_never_approval(db, tmp_path):
    _, _, _, unit = import_book(
        db, tmp_path, content="Synthetic kinase (kinase) evidence ___"
    )
    before = db.execute("SELECT count(*) AS n FROM source_units").fetchone()["n"]
    db.execute(
        "INSERT INTO source_mapping_proposals(source_unit_id,knowledge_label,method,evidence) VALUES (%s,'kinase','synthetic',%s)",
        (unit["id"], Jsonb({"quote": "kinase"})),
    )
    data = build(db)
    assert data["concepts"][0]["textbook_anchors"][0]["quote"] == "kinase"
    assert len(data["work"]) > 0
    assert persist(db, data["work"]) == len(data["work"])
    assert persist(db, data["work"]) == 0
    with pytest.raises(ValueError, match="pending"):
        persist(db, [{**data["work"][0], "status": "approved"}])
    with pytest.raises(ValueError, match="fingerprint"):
        persist(db, [{**data["work"][0], "priority": 1}])
    assert (
        db.execute("SELECT count(*) AS n FROM source_units").fetchone()["n"] == before
    )
    assert progress(db)["text_checked"] == 0
    assert not progress(db)["fixed_quota_available"]
    approve(db, unit["id"], "text")
    assert progress(db)["text_checked"] == 1
    assert progress(db)["science_checked"] == 0
    assert progress(db)["published_questions"] == 0
    with pytest.raises(RaiseException), db.transaction():
        db.execute(
            "UPDATE evidence_review_work SET priority=100 WHERE algorithm=%s",
            (VERSION,),
        )
    with pytest.raises(CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO evidence_review_work(work_key,algorithm,kind,subject,source_unit_ids,evidence,priority,status) VALUES ('bad','test','test','biochemistry','{}','{}',1,'approved')"
        )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("二 简要解释以下名词（每题2分，共6分）", "term"),
        ("简要回答以下问题(每题4分，共8分）", "short_answer"),
    ],
)
def test_instruction_sections_require_explicit_per_item_and_total_points(
    text, expected
):
    result = heading(text)
    assert result["type"] == expected
    assert result["declared_questions"] is None  # points are not a stated item count


@pytest.mark.parametrize(
    "text",
    [
        "1. 简要回答以下问题（共8分）",
        "解释以下名词（8分）",
        "简要回答以下问题：为什么合成条件成立？",
    ],
)
def test_instruction_question_is_not_misclassified_as_section(text):
    assert heading(text) is None


def test_instruction_section_stops_fill_blank_type_bleeding():
    rows = units(
        [
            "2026年合成考试试题",
            "一、填空题",
            "合成空____",
            "二 简要解释以下名词（每题2分，共2分）",
            "Synthetic concept",
            "简要回答以下问题(每题4分，共4分）",
            "Synthetic mechanism",
        ],
        {
            3: "|num:1:0:decimal:1",
            5: "|num:2:0:decimal:1",
            6: "|num:9:0:chineseCounting:3",
            7: "|num:3:0:decimal:1",
        },
    )
    qs, years, parsed = questions_and_years(rows, dict(year_start=2026, year_end=2026))
    assert [q["question_type"] for q in qs] == ["fill_blank", "term", "short_answer"]
    assert years[0]["trusted_frequency_eligible"] == 0
    assert parsed[5]["analysis"]["role"] == "section_heading"
    assert "每题" not in qs[1]["text"]


def test_parser_upgrade_keeps_prior_pending_queue_visible_without_summing_versions(db):
    for key, algorithm in [
        ("old", "evidence-work/1.6"),
        ("older", "evidence-work/1.5"),
        ("future", "evidence-work/9.0"),
    ]:
        db.execute(
            "INSERT INTO evidence_review_work(work_key,algorithm,kind,subject,source_unit_ids,evidence,priority) VALUES (%s,%s,'concept_mapping','biochemistry','{}','{}',1)",
            (key, algorithm),
        )
    result = progress(db)
    assert result["pending_work_algorithm"] == "evidence-work/1.6"
    assert result["normalized_pending_concepts"] == 1
    assert not result["pending_work_algorithm_is_current"]
    assert result["science_checked"] == 0
    db.execute(
        "INSERT INTO evidence_review_work(work_key,algorithm,kind,subject,source_unit_ids,evidence,priority) VALUES ('current',%s,'concept_mapping','biochemistry','{}','{}',1)",
        (VERSION,),
    )
    result = progress(db)
    assert result["pending_work_algorithm"] == VERSION
    assert result["normalized_pending_concepts"] == 1
    assert result["pending_work_algorithm_is_current"]
