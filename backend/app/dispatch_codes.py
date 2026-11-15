"""Safe, model-prose-free reason codes for results that could not be used (shared by the worker, the
repair flow and the reports)."""

_SAFE_VALIDATOR_ERRORS = {
    "Rubric knowledge tags must belong to the question": "rubric_tag_mismatch",
    "Rubric points must sum to max_points": "rubric_points_mismatch",
    "max_points must match a supported versioned training specification": "invalid_versioned_points",
    "Single choice requires A-D options and one answer key": "incomplete_choice_or_key",
    "Empty option": "empty_choice_option",
    "Options must be distinct": "duplicate_choice_options",
    "Only single choice accepts options": "unexpected_options",
    "Multi-blank questions use blanks only": "invalid_blank_representation",
    "Every blank needs a matching placeholder and points summing to max_points": "blank_count_or_points_mismatch",
    "Every blank needs its own scoring point": "blank_count_or_points_mismatch",
    "Fill blank requires explicit nonempty accepted answers": "missing_accepted_answers",
    "Canonical answer must be accepted": "canonical_fill_answer_missing",
    "Fill blank requires exactly one ____ blank": "blank_marker_count",
    "accepted_answers is only for fill_blank": "unexpected_accepted_answers",
    "A valid ISBN-10 or ISBN-13 checksum is required": "invalid_isbn_checksum",
    "Essay needs 4 to 6 scoring points": "rubric_item_count_out_of_range",
}
_SAFE_QUALITY_CODES = frozenset((
    "unknown_question_type", "unresolved_placeholder",
    "unavailable_figure_reference", "unhelpful_all_or_none_distractor", "answer_disclosed_in_stem", "textbook_wording_in_stem",
    "missing_unique_answer_rationale", "missing_accepted_answer_set",
    "model_answer_too_terse_for_subjective_marking",
    "insufficient_independent_scoring_criteria", "rubric_item_count_out_of_range",
))
_SAFE_PACKET_ERRORS = {
    "Textbook quote, subject or version identity mismatch": "evidence_identity_or_quote_mismatch",
    "Each rubric point needs explicit cited source analysis": "rubric_source_analysis_missing",
    "At least one located transcription reference required": "style_transcription_missing",
}


def safe_dispatch_failure(exc):
    """Diagnostic reason codes only; never include a model's generated prose."""
    import re
    from pydantic import ValidationError
    if isinstance(exc, ValidationError):
        errors = []
        for item in exc.errors(include_input=False, include_context=True)[:10]:
            reason = str((item.get("ctx") or {}).get("error", ""))
            entry = {"type": item["type"],
                     "field": str(item["loc"][0]) if item["loc"] else "root"}
            if reason in _SAFE_VALIDATOR_ERRORS:
                entry["reason"] = _SAFE_VALIDATOR_ERRORS[reason]
            errors.append(entry)
        return {"code": "output_schema_violation", "errors": errors}
    if type(exc).__name__ == "RepairNeeded":  # a fixable draft whose repair could not be queued
        errors = [dict(field=e.get("field"), reason=e.get("code")) for e in exc.errors]
        quote_only = bool(errors) and all(e["reason"] == "evidence_quote_unmatched" for e in errors)
        return {"code": "evidence_identity_or_quote_mismatch" if quote_only else "output_schema_violation", "errors": errors}
    if type(exc).__name__ == "RepairStillInvalid":
        return {"code": "repair_still_invalid", "after": exc.inner}
    msg = str(exc)
    prefix = "generated_question_quality_gate_failed:"
    if msg.startswith(prefix):
        reasons = msg[len(prefix):].split(",")
        if reasons and len(reasons) <= 10 and all(x in _SAFE_QUALITY_CODES for x in reasons):
            return {"code": "generated_question_quality_gate_failed",
                    "reasons": reasons}
    if msg in _SAFE_PACKET_ERRORS:
        return {"code": _SAFE_PACKET_ERRORS[msg]}
    return {"code": msg if re.fullmatch(r"[a-z_]{1,100}", msg) else "invalid_result"}
