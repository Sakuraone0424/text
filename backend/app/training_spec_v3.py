"""User's newest 60-question target; additive V3, no migration of past papers.

Science/answer approval is delegated to independent automated source gates.
One coherent provisional score allocation: 15 choices x2 + 15 blanks x2
per subject, 120 points total. No manual reviewer required.
"""
from collections import Counter

SPECIFICATION = "xiaohan-60q-only-choice-fill-v3"
SUBJECTS = ("biochemistry", "cell_biology")
COUNTS = {"single_choice": 15, "fill_blank": 15}
POINTS = {"single_choice": 2, "fill_blank": 2}
TOTAL_QUESTIONS = 60
QUESTIONS_PER_SUBJECT = 30
TOTAL_POINTS = 120
DURATION_SECONDS = 3600

def default_blueprint():
    return {"specification": SPECIFICATION,
            "quotas": {subject: dict(COUNTS) for subject in SUBJECTS},
            "points": dict(POINTS),
            "duration_seconds": DURATION_SECONDS}

def formal_specification():
    return {**default_blueprint(), "questions": TOTAL_QUESTIONS,
            "questions_per_subject": QUESTIONS_PER_SUBJECT,
            "points_per_subject": 60, "points_total": TOTAL_POINTS,
            "points": TOTAL_POINTS}

def validate_blueprint(blueprint):
    if not isinstance(blueprint, dict):
        raise ValueError("blueprint_object_required")
    if set(blueprint) != {"specification", "quotas", "points", "duration_seconds"}:
        raise ValueError("unknown_blueprint_fields")
    if blueprint["specification"] != SPECIFICATION or blueprint["points"] != POINTS:
        raise ValueError("v3_signature_mismatch")
    if type(blueprint["duration_seconds"]) is not int or blueprint["duration_seconds"] != DURATION_SECONDS:
        raise ValueError("invalid_duration")
    quotas = blueprint["quotas"]
    if not isinstance(quotas, dict) or set(quotas) != set(SUBJECTS):
        raise ValueError("two_subjects_required")
    for subject in SUBJECTS:
        if type(quotas[subject]) is not dict or set(quotas[subject]) != set(COUNTS) or any(
            type(quotas[subject][k]) is not int or quotas[subject][k] != COUNTS[k] for k in COUNTS
        ):
            raise ValueError("exactly_15_single_choices_and_15_blanks_per_subject")
    return blueprint

def validate_items(items):
    if not isinstance(items, list) or len(items) != TOTAL_QUESTIONS:
        raise ValueError("exactly_60_items_required")
    for subject in SUBJECTS:
        chosen = [q for q in items if isinstance(q, dict) and q.get("subject") == subject]
        if len(chosen) != QUESTIONS_PER_SUBJECT or Counter(q.get("question_type") for q in chosen) != COUNTS:
            raise ValueError("per_subject_and_type_quota")
        for q in chosen:
            kind = q["question_type"]
            if type(q.get("max_points")) is not int or q["max_points"] != POINTS[kind]:
                raise ValueError("points_do_not_match")
            rubric = q.get("rubric")
            if not isinstance(rubric, list) or not rubric or any(
                not isinstance(p, dict) or type(p.get("points")) is not int or p["points"] < 1 for p in rubric
            ) or sum(p["points"] for p in rubric) != POINTS[kind]:
                raise ValueError("rubric_incorrect")
            if kind == "fill_blank" and q.get("blanks") and (
                not isinstance(q["blanks"], list)
                or sum(b.get("points",0) for b in q["blanks"] if isinstance(b,dict)) != POINTS[kind]
            ):
                raise ValueError("blank_points_incorrect")
    return items
