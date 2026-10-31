"""Six-source locked transcription heading inventory; not official-paper certification."""
from app.source_policy import FILES, pinned_source_bytes

BIO_HASH = "6add10fad4708453f7f8cff5562ee73bf0853d70e43c8c3d67d06299cf82c001"
CELL_HASH = "63b0eea0b1dbb5a7b6323dcd769792bba60a96ce9d93c6b8bd8c79fd4939ef79"
PARALLEL_VARIANTS = {
    2012: ("生化与分子", "生化"),
    2013: ("生化与分子", "生化"),
    2014: ("生化与分子", "生化"),
}


def summary():
    bio_id = next((fid for fid, item in FILES.items()
                   if item["key"] == "biochemistry_exams"), None)
    cell_id = next((fid for fid, item in FILES.items()
                    if item["key"] == "cell_biology_exams"), None)
    if not bio_id or not cell_id or not (
        pinned_source_bytes(bio_id, BIO_HASH) and pinned_source_bytes(cell_id, CELL_HASH)
    ):
        raise PermissionError("exam_variant_report_requires_exact_six_source_versions")
    return {
        "transcription_subject_year_slots": 52,
        "distinct_labeled_transcription_sections": 55,
        "parallel_biochemistry_years": {
            str(year): list(variants)
            for year, variants in sorted(PARALLEL_VARIANTS.items())
        },
        "additional_parallel_sections": 3,
        "biochemistry_2017_transcript_term_count": 6,
        "biochemistry_2017_transcript_short_answer_count": 4,
        "biochemistry_2017_transcript_total": 10,
        "actual_original_papers_authenticated": False,
        "paper_variant_aware_frequency_analysis_required": True,
    }
