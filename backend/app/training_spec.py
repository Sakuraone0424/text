"""Xiaohan's custom training specification; not an official USTC format."""

from collections import Counter

COUNTS = dict(single_choice=3, fill_blank=3, term=4, short_answer=3, essay=1)
POINTS = dict(single_choice=2, fill_blank=3, term=4, short_answer=8, essay=20)
SUBJECTS = ("biochemistry", "cell_biology")
DURATION_SECONDS = 3600
SPECIFICATION = "xiaohan-150-60-v1"


def default_blueprint():
    """The only current training product; legacy records are read-only history."""
    return {
        "specification": SPECIFICATION,
        "quotas": {subject: dict(COUNTS) for subject in SUBJECTS},
    }


def formal_specification():
    return {
        **default_blueprint(),
        "questions": sum(COUNTS.values()) * len(SUBJECTS),
        "points": sum(COUNTS[k] * POINTS[k] for k in COUNTS) * len(SUBJECTS),
        "questions_per_subject": sum(COUNTS.values()),
        "points_per_subject": sum(COUNTS[k] * POINTS[k] for k in COUNTS),
        "duration_seconds": DURATION_SECONDS,
        "points_by_type": dict(POINTS),
    }


def validate_quotas(quotas):
    if set(quotas) != set(SUBJECTS) or any(quotas[s] != COUNTS for s in SUBJECTS):
        raise ValueError(
            "Each subject requires exactly 3/3/4/3/1 questions (14 questions, 75 points)"
        )


def validate_items(items):
    if len(items) != 28:
        raise ValueError("Training requires exactly 28 questions")
    for subject in SUBJECTS:
        chosen = [q for q in items if q["subject"] == subject]
        if Counter(q["question_type"] for q in chosen) != COUNTS:
            raise ValueError("Invalid subject/type distribution")
        if any(q["max_points"] != POINTS[q["question_type"]] for q in chosen):
            raise ValueError(
                "Question points differ from the 150-point training specification"
            )
        if sum(q["max_points"] for q in chosen) != 75:
            raise ValueError("Each subject must total 75 points")
        if any(
            sum(c["points"] for c in q["rubric"]) != q["max_points"] for q in chosen
        ):
            raise ValueError("Rubric points do not match question maximum")
