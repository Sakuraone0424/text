"""Automatically select authentic user-provided historical exam wording.

Synthetic test DOCX fixtures prove a previous manually seeded review packet is
not required, but must never be mistaken for a production past-exam source.
"""
from app import sources
from app.auto_generation import select_exam_style
from app.candidate_review import snapshot_unit
from test_ocr_only import material


def test_style_anchor_reads_real_exam_transcript_without_candidate_packet(db, tmp_path):
    path, metadata = material(tmp_path, "biochemistry_exams")
    sources.import_source(db, path, metadata)
    assert db.execute("SELECT count(*) AS n FROM candidate_review_packets").fetchone()["n"] == 0
    style = select_exam_style(db, "biochemistry", "single_choice")
    assert style and style["year"] == 2023
    paragraph = snapshot_unit(db, style["id"])
    assert paragraph["source_kind"] == "exam_transcription"
    assert "Synthetic 2023 question" in paragraph["text"]
    assert select_exam_style(db, "biochemistry", "short_answer")["year"] == 2024
    assert select_exam_style(db, "biochemistry", "fill_blank") is None
    assert select_exam_style(db, "cell_biology", "single_choice") is None


def test_explicit_docx_fidelity_does_not_invent_missing_exam_question_types(db, tmp_path):
    path, metadata = material(tmp_path, "cell_biology_exams", content=[
        "2025年中国科学技术大学细胞生物学真题",
        "名词解释",
        "1. Synthetic cell-biology definition",
    ])
    sources.import_source(db, path, metadata)
    result = select_exam_style(db, "cell_biology", "term")
    assert result and result["year"] == 2025
    assert select_exam_style(db, "cell_biology", "single_choice") is None
