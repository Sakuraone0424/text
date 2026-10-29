"""52 annual rows are canonical parsed DOCX text, not official exam certificates."""
import pytest
from app.qa_exam_year_inventory import summarize, EXAM_ITEMS


def row(source,year,typ="term",qnum="1",flags=None):
    return {
        "file_id":source["file_id"],
        "source_sha256":(
            "6add10fad4708453f7f8cff5562ee73bf0853d70e43c8c3d67d06299cf82c001"
            if source["key"]=="biochemistry_exams"
            else "63b0eea0b1dbb5a7b6323dcd769792bba60a96ce9d93c6b8bd8c79fd4939ef79"
        ),
        "year":year,
        "question_type":typ,
        "question_number":qnum,
        "concerns":flags or [],
        "quality_flags":[],
    }


def test_canonical_year_scope_and_2017_source_numbering_candidate():
    bio=next(x for x in EXAM_ITEMS.values() if x["key"]=="biochemistry_exams")
    cell=next(x for x in EXAM_ITEMS.values() if x["key"]=="cell_biology_exams")
    result=summarize([
        row(bio,2017,"term","1"),
        row(bio,2017,"short_answer","1"),
        row(cell,2002,"single_choice","3"),
    ])
    assert result["expected_years"]==52
    assert result["years_with_transcription"]==2
    assert result["candidate_numbered_starts"]==3
    item=next(x for x in result["years"]
              if x["source_key"]=="biochemistry_exams" and x["year"]==2017)
    assert item["parsed_question_starts"]==2
    assert item["candidate_type_counts"]=={"short_answer":1,"term":1}
    assert not result["official_original_exams_authenticated"]
    assert not result["question_counts_certified"]


def test_provisional_source_risk_blocks_style_not_transcript_count():
    bio=next(x for x in EXAM_ITEMS.values() if x["key"]=="biochemistry_exams")
    result=summarize([row(bio,2017,"term","1",["untranscribed_image"])])
    item=next(x for x in result["years"]
              if x["source_key"]=="biochemistry_exams" and x["year"]==2017)
    assert item["parsed_question_starts"]==1
    assert item["candidate_start_blocked"]==1
    assert item["parser_structurally_safe_starts"]==0


def test_changed_exam_source_hash_rejected():
    bio=next(x for x in EXAM_ITEMS.values() if x["key"]=="biochemistry_exams")
    bad=row(bio,2017)
    bad["source_sha256"]="0"*64
    with pytest.raises(ValueError,match="not in owner's six original file lock"):
        summarize([bad])
