"""Six-source, book-first automated science policy. No manual or expert approval stage.

This module does not call external APIs or launch any paid jobs. Original book
bytes are immutable; external material may supplement, never replace them.
"""
from __future__ import annotations
from dataclasses import dataclass

POLICY_ID = "ustc-textbook-first-auto-only-v2"
SOURCE_PRECEDENCE = ("locked_textbook", "locked_past_exam_style", "external_supplement")
REVIEW_STAGES = ("source_integrity", "science_independent_ai", "answer_independent_ai", "rubric_and_uniqueness", "release_gate")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    state: str
    reasons: tuple[str, ...]


def decide_admission(record: dict) -> Verdict:
    """Require source, two separate automated checks, and an unambiguous rubric.

    Human or expert signatures are not an input. Missing proof always blocks.
    """
    bad = []
    if record.get("six_source_sha_verified") is not True:
        bad.append("locked_six_source_identity_unverified")
    if record.get("exact_textbook_quote_verified") is not True:
        bad.append("exact_textbook_quote_unverified")
    if record.get("science_ai_round_passed") is not True:
        bad.append("automated_science_round_missing_or_failed")
    if record.get("answer_ai_round_passed") is not True:
        bad.append("automated_answer_round_missing_or_failed")
    if record.get("independent_round_evidence_separation") is not True:
        bad.append("blind_review_separation_missing")
    if record.get("rubric_and_unique_answer_verified") is not True:
        bad.append("rubric_or_answer_ambiguous")
    if record.get("duplicate_guard_passed") is not True:
        bad.append("duplicate_guard_failed")
    if record.get("source_media_resolved") is not True:
        bad.append("image_or_media_unresolved")
    if record.get("external_overrode_textbook") is True:
        bad.append("external_evidence_must_not_override_textbook")
    if record.get("textbook_has_unresolved_conflict") is True:
        bad.append("unresolved_textbook_conflict")
    return Verdict(not bad, "AUTO_APPROVED" if not bad else "BLOCKED_OR_PENDING", tuple(bad))


def select_science_authority(source_claim: str | None, internet_claim: str | None):
    """A disagreement is flagged, not silently corrected in the source."""
    if source_claim and str(source_claim).strip():
        if internet_claim and str(internet_claim).strip() != source_claim.strip():
            return {"authoritative": "textbook_as_written", "quote": source_claim,
                    "web_note": internet_claim,
                    "review_state": "CONFLICT_FLAGGED_NOT_AUTO_CORRECTED"}
        return {"authoritative": "textbook_as_written", "quote": source_claim,
                "web_note": internet_claim, "review_state": "SOURCE_FIRST"}
    return {"authoritative": "none", "quote": None, "web_note": internet_claim,
            "review_state": "NO_TEXTBOOK_SUPPORT_NOT_SCORABLE"}
