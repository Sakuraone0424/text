"""No external model requests in deterministic offline review preparation."""
from contextlib import contextmanager

import pytest

from app import qa_offline_worker as worker


def _env(**updates):
    values = {name: "false" for name in worker.FORBIDDEN_ON}
    values.update(updates)
    return values


def test_offline_refuses_other_railway_services(monkeypatch):
    monkeypatch.setattr(worker, "pinned_worker", lambda env: False)
    with pytest.raises(PermissionError, match="isolated_qa"):
        worker.offline_authorized(_env())


@pytest.mark.parametrize("switch", worker.FORBIDDEN_ON)
def test_offline_refuses_any_enabled_model_switch(monkeypatch, switch):
    monkeypatch.setattr(worker, "pinned_worker", lambda env: True)
    with pytest.raises(PermissionError, match="model_enabled"):
        worker.offline_authorized(_env(**{switch: "true"}))


def test_offline_cycle_seeds_only_pending_exam_review(monkeypatch):
    import migrate

    monkeypatch.setattr(worker, "offline_authorized", lambda: None)
    calls = []
    from types import SimpleNamespace
    fake_conn = SimpleNamespace(execute=lambda query: SimpleNamespace(fetchall=lambda: []))

    @contextmanager
    def fake_db():
        calls.append("db")
        yield fake_conn

    monkeypatch.setattr(worker, "qa_db", fake_db)
    monkeypatch.setattr(migrate, "apply_migrations", lambda conn: calls.append("migrate"))
    monkeypatch.setattr(worker, "source_audit", lambda conn: {
        "imported_files": 6,
        "all_six_byte_integrity_checks_passed": True,
        "files": [{"missing_ocr_page_markers": [], "blank_ocr_pages": []}],
    })
    monkeypatch.setattr(worker, "seed_exam_pending", lambda conn: {
        "algorithm": "evidence-work/1.7",
        "new": 7,
        "already_present": 0,
    })
    monkeypatch.setattr(worker, "seed_topic_links", lambda conn: {
        "candidate_links": 9,
        "scientifically_verified_links": 0,
    })
    monkeypatch.setattr(worker, "seed_formula_triage", lambda conn: {
        "pending_visual_candidates": 4,
        "actual_formula_correctness_certified": False,
    })
    monkeypatch.setattr(worker, "seed_science_pending", lambda conn: {
        "source_bound_science_review_tasks": 9655,
        "new_pending_tasks": 12,
        "scientifically_approved_nodes": 0,
    })
    monkeypatch.setattr(worker, "seed_chapter_neighbors", lambda conn: {
        "unmapped_pages_with_navigation_records": 72,
        "chapter_repairs_applied": 0,
    })
    monkeypatch.setattr(worker, "seed_editorial_first_draft", lambda conn: {
        "status": "pending_independent_science_and_answer_review",
        "draft_question_id": 91,
        "published": False,
        "independent_science_approved": False,
    })
    monkeypatch.setattr(worker, "stage1_provenance", lambda conn, state: {
        "issue_provenance_and_states_accepted": True,
    })
    monkeypatch.setattr(worker, "graph_provenance", lambda conn: {
        "all_nodes_source_exact": True,
        "all_lexical_facets_source_exact": True,
    })
    monkeypatch.setattr(worker, "summarize_exam_years", lambda rows: {
        "years_with_transcription": 52,
        "candidate_numbered_starts": 1921,
        "years_missing": [],
        "years": [{"source_key": "cell_biology_exams", "year": 2024, "candidate_type_counts": {"term": 1}}],
    })
    monkeypatch.setattr(worker, "seed_editorial_batch", lambda conn: {
        "reviewed_source_fact_count": 12,
        "private_drafts_created_or_reused": 9,
        "published_count": 0,
        "model_calls": 0,
    })
    monkeypatch.setattr(worker, "review_priorities", lambda conn: {
        "metadata_only_priority_counts": {"P0_visual_or_content_loss_candidate": 2},
        "source_issues_total": 2,
        "chapter_mapping_issues": 1,
    })
    result = worker.cycle()
    assert calls == ["db", "migrate"]
    assert result["pending_exam_work_added"] == 7
    assert result["exam_textbook_lexical_review"]["candidate_links"] == 9
    assert result["formula_visual_review"]["pending_visual_candidates"] == 4
    assert result["pending_science_review_worklist"]["new_pending_tasks"] == 12
    assert result["chapter_navigation_review"]["unmapped_pages_with_navigation_records"] == 72
    assert result["first_source_grounded_editorial_draft"]["published"] is False
    assert result["twelve_source_reviewed_private_drafts"]["reviewed_source_fact_count"] == 12
    assert result["exam_years_with_transcription"] == 52
    assert result["source_risk_items_tracked"] == 2
    assert result["unmarked_ocr_pages"] == 0
    assert result["model_calls"] == 0
    assert result["only_six_ocr_sources"]["additional_pdf_sources_allowed"] is False
    assert result["scientific_approvals"] == 0


def test_offline_cycle_fails_closed_on_source_byte_mismatch(monkeypatch):
    import migrate

    monkeypatch.setattr(worker, "offline_authorized", lambda: None)

    @contextmanager
    def fake_db():
        yield object()

    monkeypatch.setattr(worker, "qa_db", fake_db)
    monkeypatch.setattr(migrate, "apply_migrations", lambda conn: None)
    monkeypatch.setattr(worker, "source_audit", lambda conn: {
        "imported_files": 6,
        "all_six_byte_integrity_checks_passed": False,
    })
    monkeypatch.setattr(worker, "seed_exam_pending", lambda conn: pytest.fail(
        "must not seed from mismatched source bytes"
    ))
    with pytest.raises(PermissionError, match="integrity_failed"):
        worker.cycle()


def test_stage_errors_report_only_allowlisted_step_and_exception_type():
    def failing():
        raise ValueError("secret-ocr-text-and-api-key-must-not-leak")

    with pytest.raises(worker.OfflineStageError) as info:
        worker.step("twelve_editorial_drafts", failing)
    assert info.value.stage == "twelve_editorial_drafts"
    assert info.value.error_type == "ValueError"
    assert "secret-ocr" not in str(info.value)
