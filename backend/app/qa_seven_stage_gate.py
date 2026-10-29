"""Read-only, fail-closed seven-stage progress snapshot from actual QA SQL.

This is a checkpoint, not a release authorization. Source byte integrity,
regex facets and historic DOCX parsing never imply scientific truth. Neither
a simulated CI pass nor an empty database can satisfy the 120 accepted
two-round question reserve.
"""
import json
import os
from app.source_intake import SCHEMA, isolated_db
from app.source_readiness import audit as source_audit
from app.qa_exam_year_inventory import summarize as exam_year_summary

def analyze(source,graph,risk,chapter,years,questions,papers):
    source_six=(
        source["imported_files"]==6
        and source["integrity_passed_files"]==6
        and source["all_six_byte_integrity_checks_passed"]
        and sum(x["ocr_page_markers"] for x in source["files"])==1915
        and sum((x["embedded_media_objects"] or 0) for x in source["files"])==31
    )
    graph_covered=graph["nodes"]==9655 and graph["chapters"]==83
    annual=years["expected_years"]==52 and years["years_with_transcription"]==52
    pending_risk=risk["pending"]
    pending_chapter=chapter["pending"]
    # A ready/published row is not a certified double-reviewed question.
    # This conservative count never treats an ordinary status flag as proof.
    reserve_verified=0
    return {
        "status":"seven_stage_QA_checkpoint_not_production_release",
        "stage_1": {
            "six_pinned_originals_postcommit_integrity_verified":source_six,
            "risk_issues_total":risk["total"],
            "risk_issues_pending":pending_risk,
            "chapter_mapping_issues_total":chapter["total"],
            "chapter_mapping_pending":pending_chapter,
            "all_visual_and_science_risks_resolved":False,
        },
        "stage_2":{
            "four_textbooks_all_unit_nodes_created":graph_covered,
            "textbook_nodes":graph["nodes"],
            "chapter_groups":graph["chapters"],
            "regex_facet_candidates":graph["facets"],
            "scientifically_approved_graph_nodes":graph["approved"],
            "definitions_and_mechanisms_scientifically_complete":False,
        },
        "stage_3":{
            "52_year_transcription_inventory":annual,
            "numbered_parser_candidates":years["candidate_numbered_starts"],
            "official_original_papers_independently_authenticated":False,
        },
        "stage_4":{
            "draft_or_reviewed_question_records_in_QA":questions,
            "evidence_based_generation_real_verified":False,
        },
        "stage_5":{
            "confirmed_double_round_six_source_eligible_questions":reserve_verified,
            "required_questions":120,
            "qualified_reserve_complete":False,
        },
        "stage_6":{
            "papers_v2_in_private_QA":papers,
            "real_qualified_60_question_end_to_end_completed":False,
        },
        "stage_7":{
            "all_seven_stages_accepted":False,
            "merge_draft_pr_authorized":False,
            "production_deployment_authorized":False,
            "rollback_plan_is_technical_implementation_not_acceptance":True,
        },
    }


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME","").strip().lower()!="qa":
        raise SystemExit("qa_gate_refuses_non_qa_environment")
    schema=os.environ.get("QA_IMPORT_SCHEMA","")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_gate_requires_isolated_schema")
    with isolated_db(schema,"staging",readonly=True) as conn:
        source=source_audit(conn)
        g=conn.execute(
            "SELECT count(*) AS nodes,"
            "count(*) FILTER(WHERE scientific_review_state <> 'pending') AS approved "
            "FROM qa_textbook_evidence_nodes"
        ).fetchone()
        chapters=conn.execute(
            "SELECT count(*) AS n FROM qa_textbook_chapter_inventory"
        ).fetchone()["n"]
        facets=conn.execute(
            "SELECT count(*) AS n FROM qa_textbook_facet_candidates"
        ).fetchone()["n"]
        risk=conn.execute(
            "SELECT count(*) AS total,"
            "count(*) FILTER(WHERE review_state='pending') AS pending "
            "FROM qa_source_risk_progress"
        ).fetchone()
        chapter=conn.execute(
            "SELECT count(*) AS total,"
            "count(*) FILTER(WHERE review_state='pending') AS pending "
            "FROM qa_chapter_mapping_progress"
        ).fetchone()
        docs=conn.execute(
            "SELECT d.file_id,d.sha256 AS source_sha256,"
            "t.year,t.question_type,t.question_number,t.concerns,s.quality_flags "
            "FROM exam_transcript_candidates t "
            "JOIN source_documents d ON d.id=t.document_id "
            "JOIN source_units s ON s.id=t.unit_id"
        ).fetchall()
        years=exam_year_summary(docs)
        q=conn.execute("SELECT count(*) AS n FROM reviewed_questions").fetchone()["n"]
        papers=conn.execute("SELECT count(*) AS n FROM papers_v2").fetchone()["n"]
        data=analyze(
            source,{"nodes":g["nodes"],"approved":g["approved"],
                    "chapters":chapters,"facets":facets},
            risk,chapter,years,q,papers
        )
    print("QA_SEVEN_STAGE_HARD_GATE="+json.dumps(
        data,ensure_ascii=False,separators=(",",":"),sort_keys=True
    ),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
