"""Every exam-style count is derived from actual recorded question starts.

Synthetic, rollback-only DB fixtures validate extraction; these are not the
learner's actual past exams, and must not be counted as production inventory.
"""
from app import sources
from app.exam_style_readiness import report
from test_ocr_only import material
from test_api_v2 import LEARNER


def test_no_sources_means_all_eight_style_buckets_missing(db, client):
    status = report(db)
    assert status["source_status"] == "user_provided_genuine_past_exams"
    assert len(status["buckets"]) == 8
    assert status["ready_for_same_type_style"] is False
    assert len(status["missing_same_type_style"]) == 8
    assert all(b["safe_style_anchors"] == 0 for b in status["buckets"])
    response = client.get("/v2/quality/genuine-exam-style-readiness", headers=LEARNER)
    assert response.status_code == 200
    assert response.json()["buckets"] == status["buckets"]


def test_genuine_source_parsing_counts_only_real_question_starts(db, tmp_path):
    path, meta = material(tmp_path, "biochemistry_exams")
    sources.import_source(db, path, meta)
    status = report(db)
    def find(kind):
        return next(b for b in status["buckets"]
                    if b["subject"] == "biochemistry" and b["question_type"] == kind)
    assert find("single_choice")["safe_style_anchors"] == 1
    assert find("short_answer")["safe_style_anchors"] == 1
    assert find("term")["safe_style_anchors"] == 1
    assert find("fill_blank")["safe_style_anchors"] == 0
    assert find("single_choice")["years_with_safe_anchors"] == [2023]
    assert len(status["missing_same_type_style"]) == 5  # 1 biochem + 4 cell
    assert status["scope"] == "parsed_question_starts_only_not_official_answer_verification"


def test_question_with_unreadable_figure_is_not_safe_style_anchor(db, tmp_path):
    path, meta = material(tmp_path, "cell_biology_exams", content=[
        "2025年中国科学技术大学细胞生物学真题",
        "名词解释",
        "1. 图示以原图为准",
    ])
    sources.import_source(db, path, meta)
    status = report(db)
    term = next(b for b in status["buckets"]
                if b["subject"] == "cell_biology" and b["question_type"] == "term")
    assert term["observed_question_starts"] == 1
    assert term["safe_style_anchors"] == 0
    assert term["blocked_by_parsing_or_images"] == 1


def test_image_in_next_paragraph_blocks_parent_question_style_anchor():
    from app.transcript_analysis import candidates

    item = {
        "year_start": 1998,
        "year_end": 2026,
    }
    texts = [
        "2025年中国科学技术大学考研真题",
        "名词解释",
        "1. Identify the organelle illustrated here",
        "[DOCX image without transcribed text]",
        "2. Name the other organelle",
    ]
    units = [
        {
            "id": n,
            "text": value,
            "section": "",
            "quality_flags": (
                ["image_dependent", "untranscribed_image"] if n == 4 else []
            ),
        }
        for n, value in enumerate(texts, start=1)
    ]
    rows = list(candidates(units, item))
    starts = [row for row in rows if row["analysis"]["role"] == "question_start"]
    assert len(starts) == 2
    assert starts[0]["transcription"] == texts[2]
    assert "untranscribed_image" in starts[0]["concerns"]
    assert "image_dependent" in starts[0]["concerns"]
    assert "untranscribed_image" not in starts[1]["concerns"]
    assert "image_dependent" not in starts[1]["concerns"]
