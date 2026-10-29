"""The 2017 locked transcript has 6+4 UNNUMBERED question-like lines.

No automatic number or answer is synthesized, and candidate style is blocked
until original-paper review.
"""
import pytest
from app.offline_unnumbered_exam_review import review_candidates

ITEM = {"key": "biochemistry_exams", "year_start": 2004, "year_end": 2026}


def make_rows(term_count=6, short_count=4):
    texts = [
        "2017年硕士研究生招生考试试题",
        "一、名词解释",
        *[f"第{i}个合成名词" for i in range(term_count)],
        "二、简答题",
        *[f"简述第{i}个合成过程及其机理？" for i in range(short_count)],
        "2016年硕士研究生招生考试试题",
        "一、填空题",
        "某过程的反应物为______",
    ]
    return [
        {"ordinal": i, "text": text,
         "section": "", "quality_flags": []}
        for i, text in enumerate(texts, 1)
    ]


def test_six_plus_four_is_provisional_and_citation_locked():
    data = review_candidates(make_rows(), ITEM, source_sha256="a"*64)
    assert data["distinct_source_units"] == 10
    assert data["term_candidate_lines"] == 6
    assert data["short_answer_candidate_lines"] == 4
    assert len({v["unit_id"] for v in data["candidates"]}) == 10
    assert len({v["text_sha256"] for v in data["candidates"]}) == 10
    assert all(v["original_question_number"] is None for v in data["candidates"])
    assert all(v["usable_for_generation_style"] is False for v in data["candidates"])
    assert all(v["scientific_answer_verified"] is False for v in data["candidates"])
    assert all("question" not in v or v["question"] is None for v in data["candidates"])


@pytest.mark.parametrize("term_count,short_count", [(5,4),(6,3),(7,4),(6,5)])
def test_missing_or_extra_2017_transcript_text_fails_closed(term_count, short_count):
    with pytest.raises(ValueError,match="locked 6\\+4"):
        review_candidates(make_rows(term_count, short_count), ITEM, source_sha256="a"*64)


def test_risky_unnumbered_unit_refuses_provisional_certification():
    units=make_rows()
    units[3]["quality_flags"]=["untranscribed_image"]
    with pytest.raises(ValueError,match="unresolved source flags"):
        review_candidates(units, ITEM, source_sha256="a"*64)


def test_other_exam_documents_are_not_silently_marked_2017():
    data=review_candidates(make_rows(),
        {"key":"cell_biology_exams","year_start":1998,"year_end":2026},
        source_sha256="a"*64)
    assert data["scope"]=="not_whitelisted"
    assert not data["candidates"]
