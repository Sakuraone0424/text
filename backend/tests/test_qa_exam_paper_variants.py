"""Regression for SHA-locked true exam year-vs-paper-variant inventory."""
import pytest
from app import qa_exam_paper_variants as variants


def test_locked_transcripts_show_55_sections_but_52_year_slots():
    s = variants.summary()
    assert s["transcription_subject_year_slots"] == 52
    assert s["distinct_labeled_transcription_sections"] == 55
    assert s["additional_parallel_sections"] == 3
    assert list(s["parallel_biochemistry_years"]) == ["2012", "2013", "2014"]
    assert all(v == ["生化与分子", "生化"]
               for v in s["parallel_biochemistry_years"].values())
    assert s["biochemistry_2017_transcript_total"] == 10
    assert s["actual_original_papers_authenticated"] is False


def test_variant_report_fails_closed_after_either_source_revision(monkeypatch):
    monkeypatch.setattr(variants, "pinned_source_bytes", lambda *args: False)
    with pytest.raises(PermissionError, match="exact_six_source_versions"):
        variants.summary()
