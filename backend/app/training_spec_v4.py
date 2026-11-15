"""Versioned 60-question / 300-point daily exam contract (current).

The ID is v4 on purpose: an earlier, withdrawn "V3" exists and must not be confused with it.
xiaohan-60q-150pt-v2 (training_spec_v2) and the 28-question papers remain readable and
unchanged; this module is the only specification new papers are created from.
"""

from collections import Counter

SPECIFICATION = "xiaohan-60q-300pt-v4"
SUBJECTS = ("biochemistry", "cell_biology")
QUESTION_TYPES = ("single_choice", "fill_blank", "term", "short_answer", "essay")
COUNTS = {
    "single_choice": 10,
    "fill_blank": 14,
    "term": 4,
    "short_answer": 1,
    "essay": 1,
}
POINTS = {
    "single_choice": 4,
    "fill_blank": 4,
    "term": 6,
    "short_answer": 12,
    "essay": 18,
}
DURATION_SECONDS = 3600
QUESTIONS_PER_SUBJECT = 30
POINTS_PER_SUBJECT = 150
TOTAL_QUESTIONS = 60
TOTAL_POINTS = 300
# A model answer for these types is graded against a list of independent points.
RUBRIC_ITEMS = {"essay": (4, 6)}
SUBJECTIVE = ("term", "short_answer", "essay")

assert sum(COUNTS[k] * POINTS[k] for k in COUNTS) == POINTS_PER_SUBJECT
assert sum(COUNTS.values()) == QUESTIONS_PER_SUBJECT


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
        "points": TOTAL_POINTS,
        "points_total": TOTAL_POINTS,
        "questions_per_subject": QUESTIONS_PER_SUBJECT,
        "points_per_subject": POINTS_PER_SUBJECT,
        "points_by_type": dict(POINTS),
    }


def validate_blueprint(blueprint):
    if not isinstance(blueprint, dict):
        raise ValueError("v4 blueprint must be a JSON object")
    if set(blueprint) != {"specification", "quotas", "points", "duration_seconds"}:
        raise ValueError("v4 blueprint has missing or unknown fields")
    if blueprint["specification"] != SPECIFICATION:
        raise ValueError("v4 specification ID mismatch")
    if blueprint["duration_seconds"] != DURATION_SECONDS or type(blueprint["duration_seconds"]) is not int:
        raise ValueError("v4 exam must last exactly 3600 seconds")
    if blueprint["points"] != POINTS:
        raise ValueError("v4 per-type scoring is immutable")
    quotas = blueprint["quotas"]
    if not isinstance(quotas, dict) or set(quotas) != set(SUBJECTS):
        raise ValueError("v4 requires both subjects")
    for subject in SUBJECTS:
        types = quotas[subject]
        if not isinstance(types, dict) or set(types) != set(QUESTION_TYPES):
            raise ValueError("v4 requires exactly five question types")
        if any(type(n) is not int or n != COUNTS[kind] for kind, n in types.items()):
            raise ValueError("v4 quotas must be exactly 10/14/4/1/1 per subject")
    return blueprint


def validate_items(items):
    """Validate a complete new v4 paper; never rescore historical papers."""
    if not isinstance(items, list) or len(items) != TOTAL_QUESTIONS:
        raise ValueError("v4 paper requires exactly 60 questions")
    for subject in SUBJECTS:
        chosen = [q for q in items if q.get("subject") == subject]
        if len(chosen) != QUESTIONS_PER_SUBJECT:
            raise ValueError("v4 requires 30 questions per subject")
        if Counter(q.get("question_type") for q in chosen) != COUNTS:
            raise ValueError("v4 subject quotas must be exactly 10/14/4/1/1")
        for q in chosen:
            kind = q["question_type"]
            if type(q.get("max_points")) is not int or q["max_points"] != POINTS[kind]:
                raise ValueError("v4 question score does not match its type")
            rubric = q.get("rubric")
            if not isinstance(rubric, list) or not rubric or any(not isinstance(c, dict) for c in rubric) or any(
                type(c.get("points")) is not int or c["points"] <= 0 for c in rubric
            ):
                raise ValueError("v4 requires explicit positive rubric criteria")
            if sum(c["points"] for c in rubric) != q["max_points"]:
                raise ValueError("v4 rubric criteria must add up to question points")
            low, high = RUBRIC_ITEMS.get(kind, (1, 50))
            if not low <= len(rubric) <= high:
                raise ValueError("v4 essay needs 4 to 6 scoring points")
            if kind == "fill_blank" and q.get("blanks"):
                blanks = q["blanks"]
                if not isinstance(blanks, list) or sum(b.get("points", 0) for b in blanks) != POINTS[kind]:
                    raise ValueError("v4 blank allocation must total 4 points")
        if sum(q["max_points"] for q in chosen) != POINTS_PER_SUBJECT:
            raise ValueError("v4 subject must total 150 points")
    return items


def count_required_bank():
    """Minimum raw counts (2 subjects x 5 types = 10 cells); eligibility also needs audit checks."""
    return {(subject, kind): count for subject in SUBJECTS for kind, count in COUNTS.items()}
