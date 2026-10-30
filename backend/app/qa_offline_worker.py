"""No-model QA maintenance loop for the isolated USTC exam staging service.

Only deterministic bookkeeping: source byte checks, migration consistency and
pending historical-exam review work. It neither calls a model API nor
certifies textbook science, original exam answers, OCR/media fidelity,
question quality or publication.
"""
import json
import os
import time

from app.qa_harness_tick import qa_db, pinned_worker
from app.source_readiness import audit as source_audit
from app.qa_exam_worklist_seed import ensure as seed_exam_pending
from app.qa_offline_topic_links import seed as seed_topic_links
from app.qa_review_worklist import inspect as review_priorities
from app.qa_offline_formula_triage import seed as seed_formula_triage
from app.qa_exam_year_inventory import summarize as summarize_exam_years
from app.qa_stage1_acceptance import inspect as stage1_provenance
from app.qa_graph_provenance_gate import inspect as graph_provenance
from app.qa_graph_worklist_seed import ensure as seed_science_pending
from app.qa_chapter_neighbor_proposals import seed as seed_chapter_neighbors

FORBIDDEN_ON = (
    "DEEPSEEK_API_AUTHORIZED",
    "AI_AUTOMATION_ENABLED",
    "QA_AUTOMATION_ENABLED",
    "QA_HARNESS_EXECUTION_ENABLED",
)


def offline_authorized(env=None):
    env = os.environ if env is None else env
    if not pinned_worker(env):
        raise PermissionError("offline_worker_not_in_isolated_qa_service")
    if any(env.get(name, "").lower() in {"1", "true", "yes", "on"} for name in FORBIDDEN_ON):
        raise PermissionError("offline_worker_refuses_model_enabled_environment")


def cycle():
    """Make real, idempotent pending-only progress without billable APIs."""
    offline_authorized()
    from migrate import apply_migrations
    with qa_db() as conn:
        apply_migrations(conn)
        state = source_audit(conn)
        if state.get("imported_files") != 6 or not state.get(
            "all_six_byte_integrity_checks_passed"
        ):
            raise PermissionError("offline_worker_six_source_integrity_failed")
        result = seed_exam_pending(conn)
        linked = seed_topic_links(conn)
        formula = seed_formula_triage(conn)
        science_jobs = seed_science_pending(conn)
        chapter_hints = seed_chapter_neighbors(conn)
        review = review_priorities(conn)
        source_gate = stage1_provenance(conn, state)
        graph_gate = graph_provenance(conn)
        exam_rows = conn.execute(
            "SELECT d.file_id,d.sha256 AS source_sha256,"
            "t.year,t.question_type,t.question_number,t.concerns,u.quality_flags "
            "FROM exam_transcript_candidates t "
            "JOIN source_documents d ON d.id=t.document_id "
            "JOIN source_units u ON u.id=t.unit_id"
        ).fetchall()
        years = summarize_exam_years(exam_rows)
        return {
            "source_files": 6,
            "algorithm": result["algorithm"],
            "pending_exam_work_added": result["new"],
            "existing_exam_review_work": result["already_present"],
            "exam_textbook_lexical_review": linked,
            "formula_visual_review": formula,
            "pending_science_review_worklist": science_jobs,
            "chapter_navigation_review": chapter_hints,
            "source_ledger_provenance": source_gate["issue_provenance_and_states_accepted"],
            "textbook_graph_source_exact": graph_gate["all_nodes_source_exact"],
            "textbook_graph_facet_exact": graph_gate["all_lexical_facets_source_exact"],
            "exam_years_with_transcription": years["years_with_transcription"],
            "exam_parsed_question_starts": years["candidate_numbered_starts"],
            "exam_years_missing": years["years_missing"],
            "exam_question_type_candidates_by_year": [
                {"subject_source": row["source_key"], "year": row["year"],
                 "counts": row["candidate_type_counts"]}
                for row in years["years"]
            ],
            "source_review_priorities": review["metadata_only_priority_counts"],
            "source_risk_items_tracked": review["source_issues_total"],
            "chapter_mapping_items_tracked": review["chapter_mapping_issues"],
            "unmarked_ocr_pages": sum(len(d["missing_ocr_page_markers"]) for d in state["files"]),
            "blank_ocr_page_markers": sum(len(d["blank_ocr_pages"]) for d in state["files"]),
            "model_calls": 0,
            "scientific_approvals": 0,
        }


def main():
    # Never auto-resume old DeepSeek jobs even if somebody flips an enable flag.
    offline_authorized()
    while True:
        try:
            print("QA_OFFLINE_WORKER=" + json.dumps(
                cycle(), ensure_ascii=False, sort_keys=True
            ), flush=True)
        except Exception as exc:
            # No book passages, private records, credentials or API receipts in logs.
            print("QA_OFFLINE_ERROR=" + type(exc).__name__, flush=True)
        time.sleep(3600)


if __name__ == "__main__":
    main()
