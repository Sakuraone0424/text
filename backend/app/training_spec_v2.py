"""Proposed 60-question daily exam contract (v2).

This module is intentionally not wired to /v2/papers yet. Do not activate a new
format until a reviewed, publication-ready bank and all migration tests pass.
Historical 28-question paper snapshots retain their original scoring contract.
"""

from collections import Counter

SPECIFICATION = "xiaohan-60q-150pt-v2"
SUBJECTS = ("biochemistry", "cell_biology")
QUESTION_TYPES = ("single_choice", "fill_blank", "term", "short_answer")
COUNTS = {
    "single_choice": 18,
    "fill_blank": 7,
    "term": 4,
    "short_answer": 1,
}
POINTS = {
    "single_choice": 2,
    "fill_blank": 2,
    "term": 4,
    "short_answer": 9,
}
DURATION_SECONDS = 3600
QUESTIONS_PER_SUBJECT = 30
POINTS_PER_SUBJECT = 75
TOTAL_QUESTIONS = 60
TOTAL_POINTS = 150


def default_blueprint():
    return {
        "specification": SPECIFICATION,
        "quotas": {subject: dict(COUNTS) for subject in SUBJECTS},
        "points": dict(POINTS),
        "duration_seconds": DURATION_SECONDS,
    }


def formal_specification():
    return {
        **default_blueprint(),
        "questions": TOTAL_QUESTIONS,
        "points_total": TOTAL_POINTS,
        "questions_per_subject": QUESTIONS_PER_SUBJECT,
        "points_per_subject": POINTS_PER_SUBJECT,
        "points_by_type": dict(POINTS),
    }


def validate_blueprint(blueprint):
    if not isinstance(blueprint, dict):
        raise ValueError("v2 blueprint must be a JSON object")
    if set(blueprint) != {"specification", "quotas", "points", "duration_seconds"}:
        raise ValueError("v2 blueprint has missing or unknown fields")
    if blueprint["specification"] != SPECIFICATION:
        raise ValueError("v2 specification ID mismatch")
    if blueprint["duration_seconds"] != DURATION_SECONDS or type(blueprint["duration_seconds"]) is not int:
        raise ValueError("v2 exam must last exactly 3600 seconds")
    if blueprint["points"] != POINTS:
        raise ValueError("v2 per-type scoring is immutable")
    quotas = blueprint["quotas"]
    if not isinstance(quotas, dict) or set(quotas) != set(SUBJECTS):
        raise ValueError("v2 requires both subjects")
    for subject in SUBJECTS:
        types = quotas[subject]
        if not isinstance(types, dict) or set(types) != set(QUESTION_TYPES):
            raise ValueError("v2 allows exactly four question types, no essay")
        if any(type(n) is not int or n != COUNTS[kind] for kind, n in types.items()):
            raise ValueError("v2 quotas must be exactly 18/7/4/1 per subject")
    return blueprint


def validate_items(items):
    """Validate a complete new v2 paper; never rescore historical v1 papers."""
    if not isinstance(items, list) or len(items) != TOTAL_QUESTIONS:
        raise ValueError("v2 paper requires exactly 60 questions")
    for subject in SUBJECTS:
        chosen = [q for q in items if q.get("subject") == subject]
        if len(chosen) != QUESTIONS_PER_SUBJECT:
            raise ValueError("v2 requires 30 questions per subject")
        if Counter(q.get("question_type") for q in chosen) != COUNTS:
            raise ValueError("v2 subject quotas must be exactly 18/7/4/1")
        for q in chosen:
            kind = q["question_type"]
            if type(q.get("max_points")) is not int or q["max_points"] != POINTS[kind]:
                raise ValueError("v2 question score does not match its type")
            rubric = q.get("rubric")
            if not isinstance(rubric, list) or not rubric or any(
                type(c.get("points")) is not int or c["points"] <= 0
                for c in rubric
                if isinstance(c, dict)
            ) or any(not isinstance(c, dict) for c in rubric):
                raise ValueError("v2 requires explicit positive rubric criteria")
            if sum(c["points"] for c in rubric) != q["max_points"]:
                raise ValueError("v2 rubric criteria must add up to question points")
            if kind == "fill_blank" and q.get("blanks"):
                blanks = q["blanks"]
                if not isinstance(blanks, list) or sum(b.get("points", 0) for b in blanks) != POINTS[kind]:
                    raise ValueError("v2 blank allocation must total 2 points")
        if sum(q["max_points"] for q in chosen) != POINTS_PER_SUBJECT:
            raise ValueError("v2 subject must total 75 points")
    return items


def count_required_bank():
    """Minimum raw counts; actual eligibility also needs audit/duplicate checks."""
    return {(subject, kind): count for subject in SUBJECTS for kind, count in COUNTS.items()}
