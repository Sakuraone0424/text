"""Preserve bounded rejection causes without logging textbook/model answer text."""
from pydantic import BaseModel, model_validator

from app.auto_worker import safe_dispatch_failure


class InvalidRubric(BaseModel):
    count: int

    @model_validator(mode="after")
    def invalid(self):
        raise ValueError("Rubric points must sum to max_points")


def test_structural_reason_is_safe_enum_only():
    try:
        InvalidRubric(count=2)
    except Exception as exc:
        result = safe_dispatch_failure(exc)
    assert result == {"code": "output_schema_violation",
                      "errors": [{"type": "value_error", "field": "root",
                                  "reason": "rubric_points_mismatch"}]}


def test_quality_reason_fails_closed_on_unknown_text():
    assert safe_dispatch_failure(ValueError(
        "generated_question_quality_gate_failed:unresolved_placeholder"
    )) == {"code": "generated_question_quality_gate_failed",
           "reasons": ["unresolved_placeholder"]}
    assert safe_dispatch_failure(ValueError(
        "generated_question_quality_gate_failed:private source quote"
    )) == {"code": "invalid_result"}


def test_narrative_error_is_not_emitted():
    assert safe_dispatch_failure(ValueError(
        "Private exam content: do not disclose this material"
    )) == {"code": "invalid_result"}
    assert safe_dispatch_failure(ValueError(
        "Textbook quote, subject or version identity mismatch"
    )) == {"code": "evidence_identity_or_quote_mismatch"}
