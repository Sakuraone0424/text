"""Synthetic evidence only. Exact text overlap is a pending search lead."""
import pytest
from app import qa_offline_topic_links as links


@pytest.fixture(autouse=True)
def pin_sources(monkeypatch):
    monkeypatch.setattr(links, "pinned_source_bytes",
                        lambda fid, digest: fid in {"cell_biology", "cell_biology_exams"}
                        and digest in {"a" * 64, "b" * 64})


def book(id, chapter="细胞骨架", text="微管与微丝的比较", **kw):
    return dict(unit_id=id, file_id="cell_biology", source_sha256="a" * 64,
                subject="cell_biology", chapter=chapter, text=text,
                ocr_page=100, quality_flags=[], **kw)


def exam(id=101, text="解释微管和微丝的动态变化。", **kw):
    return dict(unit_id=id, file_id="cell_biology_exams",
                source_sha256="b" * 64, subject="cell_biology", year=2024,
                question_number=3, transcription=text, concerns=[],
                quality_flags=[], **kw)


def test_two_literal_topics_produce_source_bound_review_candidates():
    rows, report = links.build([
        book(1), book(2, chapter="细胞运动", text="微管是细胞骨架组分"),
        book(3, chapter="细胞骨架", text="微管影响迁移"),
    ], [exam()])
    assert [(r[1], r[2]) for r in rows] == [
        (1, "微丝"), (1, "微管"), (2, "微管")
    ]
    assert report["lexically_linked_question_starts"] == 1
    assert report["candidate_links"] == 3
    assert report["scientifically_verified_links"] == 0
    assert all(r[6] == "b" * 64 and r[7] == "a" * 64 for r in rows)


def test_no_fabricated_relationship_without_exact_common_term():
    rows, report = links.build([book(1)], [exam(text="简述纺锤体功能")])
    assert rows == []
    assert report["unlinked_eligible_question_starts"] == 1


def test_risky_book_and_exam_are_excluded():
    risky_book = book(1)
    risky_book["quality_flags"] = ["corrupt_ocr"]
    risky_exam = exam()
    risky_exam["concerns"] = ["question_boundary_unconfirmed"]
    rows, report = links.build([risky_book], [risky_exam])
    assert rows == []
    assert report["blocked_review_reasons"]["unverified_or_damaged_transcription"] == 1


def test_unpinned_source_fails_closed():
    bad_book = book(1)
    bad_book["source_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="unlocked_textbook"):
        links.build([bad_book], [exam()])


def test_ascii_scientific_term_needs_token_boundary():
    assert "SRP" in links.terms_in("SRP与受体结合")
    assert "SRP" not in links.terms_in("XSRP2 marker")


def test_bounds_fail_closed():
    with pytest.raises(ValueError, match="bounds"):
        links.build([], [], per_exam_limit=0)


def test_style_continuation_risk_blocks_candidate():
    q = exam()
    q["concerns"] = ["untranscribed_image"]
    rows, report = links.build([book(1)], [q])
    assert rows == [] and report["eligible_exam_question_starts"] == 0
