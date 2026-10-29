"""Release cannot pass with byte-only or regex-only science evidence."""
from app.qa_seven_stage_gate import analyze


def test_all_paper_counts_even_without_sources_cannot_force_production():
    sources={
        "imported_files":6,
        "integrity_passed_files":6,
        "all_six_byte_integrity_checks_passed":True,
        "files":[
            {"ocr_page_markers":654,"embedded_media_objects":None},
            {"ocr_page_markers":587,"embedded_media_objects":None},
            {"ocr_page_markers":289,"embedded_media_objects":None},
            {"ocr_page_markers":385,"embedded_media_objects":None},
            {"ocr_page_markers":0,"embedded_media_objects":27},
            {"ocr_page_markers":0,"embedded_media_objects":4},
        ],
    }
    state=analyze(
        sources,{"nodes":9655,"chapters":83,"facets":12896,"approved":0},
        {"total":5094,"pending":5094},
        {"total":72,"pending":72},
        {"expected_years":52,"years_with_transcription":52,
         "candidate_numbered_starts":1986},
        500,10,
    )
    assert state["stage_1"]["six_pinned_originals_postcommit_integrity_verified"]
    assert state["stage_2"]["four_textbooks_all_unit_nodes_created"]
    assert state["stage_3"]["52_year_transcription_inventory"]
    assert state["stage_5"]["confirmed_double_round_six_source_eligible_questions"]==0
    assert not state["stage_5"]["qualified_reserve_complete"]
    assert not state["stage_6"]["real_qualified_60_question_end_to_end_completed"]
    assert not state["stage_7"]["merge_draft_pr_authorized"]
    assert not state["stage_7"]["production_deployment_authorized"]


def test_missing_source_coverage_fails_source_gate_even_when_all_other_counts_match():
    sources={
        "imported_files":5,"integrity_passed_files":5,
        "all_six_byte_integrity_checks_passed":False,
        "files":[],
    }
    state=analyze(
        sources,{"nodes":9655,"chapters":83,"facets":12896,"approved":0},
        {"total":5094,"pending":5094},{"total":72,"pending":72},
        {"expected_years":52,"years_with_transcription":52,
         "candidate_numbered_starts":1986},
        0,0,
    )
    assert not state["stage_1"]["six_pinned_originals_postcommit_integrity_verified"]
    assert not state["stage_7"]["production_deployment_authorized"]
