"""No seventh PDF or outside knowledge source can enter offline QA."""
from copy import deepcopy

import pytest

from app import qa_ocr_scope_guard as scope


def test_exact_six_locked_user_sources_are_allowed():
    result = scope.inspect()
    assert result["source_count"] == 6
    assert result["ocr_textbooks_and_notes"] == 4
    assert result["genuine_exam_transcriptions"] == 2
    assert result["additional_pdf_sources_allowed"] is False
    assert result["external_model_calls_allowed"] is False


def test_seventh_source_is_rejected(monkeypatch):
    manifest = deepcopy(scope.COLLECTION)
    manifest["files"].append({
        "key": "outside_pdf", "kind": "textbook",
        "filename": "external-scanned-book.pdf", "file_id": "outside"
    })
    monkeypatch.setattr(scope, "COLLECTION", manifest)
    with pytest.raises(PermissionError, match="six_source"):
        scope.inspect()


def test_external_pdf_cannot_replace_a_locked_ocr_file(monkeypatch):
    manifest = deepcopy(scope.COLLECTION)
    manifest["files"][0]["filename"] = "source.pdf"
    monkeypatch.setattr(scope, "COLLECTION", manifest)
    with pytest.raises(PermissionError, match="non_ocr"):
        scope.inspect()


def test_unknown_source_revision_mode_is_rejected(monkeypatch):
    manifest = deepcopy(scope.COLLECTION)
    manifest["files"][0]["source_mode"] = "external_pdf"
    monkeypatch.setattr(scope, "COLLECTION", manifest)
    with pytest.raises(PermissionError, match="not_ocr_only"):
        scope.inspect()
