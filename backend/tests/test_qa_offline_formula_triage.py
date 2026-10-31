"""Synthetic arithmetic/notation fixtures, not acceptance of real OCR."""
import pytest
from app import qa_offline_formula_triage as audit
from app.source_policy import FILES

CELL_FILE_ID = next(fid for fid, item in FILES.items() if item["key"] == "cell_biology")


def test_formula_direction_and_cofactor_visual_checks_are_pending():
    result = audit.classify("苹果酸 + NAD+ ⇌ 草酰乙酸 + NADH")
    assert "reaction_direction_original_visual_check" in result
    assert "reaction_cofactor_charge_original_visual_check" in result
    assert "cofactor_stoichiometry_notation_check" in result


def test_replacement_glyph_requires_original_page():
    assert "replacement_or_missing_glyph_check" in audit.classify("反应式 �→ 产品")


def test_safe_paragraph_without_equation_generates_no_formula_issue():
    assert audit.classify("该蛋白可能通过磷酸化改变定位。") == ()


def test_formula_flags_are_not_scientific_verdicts(monkeypatch):
    monkeypatch.setattr(audit, "pinned_source_bytes", lambda fid, sha: fid==CELL_FILE_ID)
    rows, counts = audit.build([{
        "unit_id": 9, "file_id": CELL_FILE_ID,
        "source_sha256": "a"*64, "ocr_page": 37,
        "text": "ATP + H₂O → ADP + Pi",
    }])
    assert len(rows) >= 2
    assert counts["reaction_cofactor_charge_original_visual_check"] == 1
    assert all(x[2] == audit.VERSION for x in rows)


def test_formula_audit_refuses_unlocked_book(monkeypatch):
    monkeypatch.setattr(audit, "pinned_source_bytes", lambda *args: False)
    with pytest.raises(ValueError, match="unpinned"):
        audit.build([{
            "unit_id": 9, "file_id": "cell_biology",
            "source_sha256": "f"*64, "ocr_page": 1, "text": "A→B"
        }])


def test_formula_audit_refuses_missing_page(monkeypatch):
    monkeypatch.setattr(audit, "pinned_source_bytes", lambda *args: True)
    with pytest.raises(ValueError, match="page"):
        audit.build([{
            "unit_id": 9, "file_id": "cell_biology",
            "source_sha256": "a"*64, "ocr_page": None, "text": "A→B"
        }])
