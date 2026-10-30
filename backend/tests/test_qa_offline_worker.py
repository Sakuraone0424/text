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
    fake_conn = object()

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
    monkeypatch.setattr(worker, "review_priorities", lambda conn: {
        "metadata_only_priority_counts": {"P0_visual_or_content_loss_candidate": 2},
        "source_issues_total": 2,
        "chapter_mapping_issues": 1,
    })
    result = worker.cycle()
    assert calls == ["db", "migrate"]
    assert result["pending_exam_work_added"] == 7
    assert result["exam_textbook_lexical_review"]["candidate_links"] == 9
    assert result["source_risk_items_tracked"] == 2
    assert result["unmarked_ocr_pages"] == 0
    assert result["model_calls"] == 0
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
