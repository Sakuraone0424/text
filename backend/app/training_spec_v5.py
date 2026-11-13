"""Two independent daily papers (current): an objective paper and a subjective paper, 150 points each.

xiaohan-obj-fill-150-v6  (current objective paper) per subject 30 fill-in questions of 2.5 pt = 75 pt; both subjects: 60 questions,
                     150 pt; 60 minutes, scored the moment it is handed in. A fill-in has 1-3 blanks that share its 2.5 points equally.
xiaohan-obj-150-v5   (earlier objective paper, read-only) per subject 15 single choice (3 pt) + 15 fill-in (2 pt) = 75 pt.
xiaohan-subj-150-v5  per subject 4 terms (5 pt) + 2 short answers (15 pt) + 1 essay (25 pt) = 75 pt; both subjects:
                     14 questions, 150 pt; 60 minutes (QUIZ_SUBJECTIVE_PAPER_SECONDS), AI grading and manual grading.

The old xiaohan-60q-300pt-v4 mixed paper (and v2 / the 28-question paper) stay readable; nothing new is made from them.
The module-level COUNTS / POINTS / RESERVE are the daily DEMAND per subject across both papers: question supply, reserve
targets and generation planning read them.
"""

from collections import Counter
import os

OBJECTIVE_SPEC = "xiaohan-obj-fill-150-v6"
OBJECTIVE_V5_SPEC = "xiaohan-obj-150-v5"
SUBJECTIVE_SPEC = "xiaohan-subj-150-v5"
SUBJECTS = ("biochemistry", "cell_biology")
QUESTION_TYPES = ("single_choice", "fill_blank", "term", "short_answer", "essay")
OBJECTIVE_TYPES = ("fill_blank",)
SUBJECTIVE_TYPES = ("term", "short_answer", "essay")
# points of every type under the current specification (single choice is no longer on a daily paper; it keeps its 3 points for practice)
POINTS = {"single_choice": 3, "fill_blank": 2.5, "term": 5, "short_answer": 15, "essay": 25}
# daily demand per subject (both papers): single choice is not asked for any more; the two-day reserve is twice the demand
COUNTS = {"fill_blank": 30, "term": 4, "short_answer": 2, "essay": 1}
RESERVE_DAYS = 2
RESERVE = {kind: n * RESERVE_DAYS for kind, n in COUNTS.items()}
POINTS_PER_SUBJECT = 75
TOTAL_POINTS = 150
DEFAULT_DURATION_SECONDS = 3600
MAX_BLANKS = 3
MULTI_BLANK_SHARE = 1 / 3        # at most a third of a paper's fill-ins have more than one blank
# rubric criteria per question for newly written questions (their points add up to the full score)
RUBRIC_ITEMS = {"term": (2, 3), "short_answer": (4, 5), "essay": (6, 8)}
SUBJECTIVE = SUBJECTIVE_TYPES
# the scales every earlier question is converted from: v5 (single 3, fill-in 2, ...) and v4 (4, 4, 6, 12, 18)
V5_POINTS = {"single_choice": 3, "fill_blank": 2, "term": 5, "short_answer": 15, "essay": 25}
V4_POINTS = {"single_choice": 4, "fill_blank": 4, "term": 6, "short_answer": 12, "essay": 18}
V5_OBJECTIVE_COUNTS = {"single_choice": 15, "fill_blank": 15}

assert 30 * POINTS["fill_blank"] == POINTS_PER_SUBJECT
assert sum(COUNTS[k] * POINTS[k] for k in SUBJECTIVE_TYPES) == POINTS_PER_SUBJECT


def blank_points(max_points, blanks):
    """What each of `blanks` blanks is worth: the question's points shared equally (exact value; shown with two decimals)."""
    return max_points / blanks


def blanks_total(max_points, correct, blanks):
    """The score of an answer with `correct` of `blanks` blanks right. Computed from the counts, not by adding rounded per-blank
    values, so a fully correct answer is exactly the question's points and no rounding error builds up over a paper."""
    from fractions import Fraction

    if not 0 <= correct <= blanks or blanks < 1:
        raise ValueError("blank_counts_out_of_range")
    return float(Fraction(str(max_points)) * correct / blanks)


def subjective_duration_seconds():
    """Configurable: QUIZ_SUBJECTIVE_PAPER_SECONDS (60..14400), default one hour."""
    try:
        value = int(os.environ.get("QUIZ_SUBJECTIVE_PAPER_SECONDS", DEFAULT_DURATION_SECONDS))
    except ValueError:
        return DEFAULT_DURATION_SECONDS
    return value if 60 <= value <= 14400 else DEFAULT_DURATION_SECONDS


class PaperSpec:
    """One of the two daily papers; has the same surface as the older training_spec modules."""

    def __init__(self, specification, label, types, kind, configurable_duration=False, counts=None, points=None, retired=False):
        self.SPECIFICATION = specification
        self.LABEL = label
        self.KIND = kind
        self.TYPES = types
        self.SUBJECTS = SUBJECTS
        self.QUESTION_TYPES = types
        self.RETIRED = retired                 # an earlier paper: readable, never made again
        self.COUNTS = dict(counts) if counts else {k: COUNTS[k] for k in types}
        self.POINTS = dict(points) if points else dict(POINTS)
        self.RUBRIC_ITEMS = RUBRIC_ITEMS
        self.QUESTIONS_PER_SUBJECT = sum(self.COUNTS.values())
        self.POINTS_PER_SUBJECT = POINTS_PER_SUBJECT
        self.TOTAL_QUESTIONS = self.QUESTIONS_PER_SUBJECT * len(SUBJECTS)
        self.TOTAL_POINTS = TOTAL_POINTS
        self.configurable_duration = configurable_duration
        self.SUBJECTIVE = SUBJECTIVE_TYPES

    @property
    def DURATION_SECONDS(self):
        return subjective_duration_seconds() if self.configurable_duration else DEFAULT_DURATION_SECONDS

    def default_blueprint(self):
        return {
            "specification": self.SPECIFICATION,
            "quotas": {subject: dict(self.COUNTS) for subject in SUBJECTS},
            "points": {k: self.POINTS[k] for k in self.TYPES},
            "duration_seconds": self.DURATION_SECONDS,
        }

    def formal_specification(self):
        return {
            **self.default_blueprint(),
            "kind": self.KIND,
            "label": self.LABEL,
            "questions": self.TOTAL_QUESTIONS,
            "points": self.TOTAL_POINTS,
            "points_total": self.TOTAL_POINTS,
            "questions_per_subject": self.QUESTIONS_PER_SUBJECT,
            "points_per_subject": self.POINTS_PER_SUBJECT,
            "points_by_type": {k: self.POINTS[k] for k in self.TYPES},
        }

    def validate_blueprint(self, blueprint):
        if not isinstance(blueprint, dict):
            raise ValueError("blueprint must be a JSON object")
        if set(blueprint) != {"specification", "quotas", "points", "duration_seconds"}:
            raise ValueError("blueprint has missing or unknown fields")
        if blueprint["specification"] != self.SPECIFICATION:
            raise ValueError("specification ID mismatch")
        duration = blueprint["duration_seconds"]
        if type(duration) is not int or (duration != DEFAULT_DURATION_SECONDS and not (
                self.configurable_duration and 60 <= duration <= 14400)):
            raise ValueError("paper duration is not allowed for this specification")
        if blueprint["points"] != {k: self.POINTS[k] for k in self.TYPES}:
            raise ValueError("per-type scoring is immutable")
        quotas = blueprint["quotas"]
        if not isinstance(quotas, dict) or set(quotas) != set(SUBJECTS):
            raise ValueError("both subjects are required")
        for subject in SUBJECTS:
            types = quotas[subject]
            if not isinstance(types, dict) or set(types) != set(self.TYPES):
                raise ValueError("quotas must list exactly this paper's question types")
            if any(type(n) is not int or n != self.COUNTS[kind] for kind, n in types.items()):
                raise ValueError("quotas must match the specification")
        return blueprint

    def validate_items(self, items):
        if not isinstance(items, list) or len(items) != self.TOTAL_QUESTIONS:
            raise ValueError(f"paper requires exactly {self.TOTAL_QUESTIONS} questions")
        for subject in SUBJECTS:
            chosen = [q for q in items if q.get("subject") == subject]
            if len(chosen) != self.QUESTIONS_PER_SUBJECT:
                raise ValueError(f"paper requires {self.QUESTIONS_PER_SUBJECT} questions per subject")
            if Counter(q.get("question_type") for q in chosen) != self.COUNTS:
                raise ValueError("subject quotas do not match the specification")
            for q in chosen:
                kind = q["question_type"]
                if q.get("max_points") != self.POINTS[kind]:
                    raise ValueError("question score does not match its type")
                rubric = q.get("rubric")
                if not isinstance(rubric, list) or not rubric or any(not isinstance(c, dict) for c in rubric):
                    raise ValueError("explicit rubric criteria are required")
                if any(not _share(c.get("points")) for c in rubric):
                    raise ValueError("rubric points must be positive shares of the question's points")
                if abs(sum(c["points"] for c in rubric) - q["max_points"]) > 1e-6:
                    raise ValueError("rubric criteria must add up to the question's points")
                if kind == "fill_blank" and q.get("blanks"):
                    if len(q["blanks"]) > MAX_BLANKS or abs(sum(b.get("points", 0) for b in q["blanks"]) - q["max_points"]) > 1e-6:
                        raise ValueError("blank allocation must total the question's points")
            if sum(q["max_points"] for q in chosen) != self.POINTS_PER_SUBJECT:
                raise ValueError("subject must total 75 points")
        return items

    def count_required_bank(self):
        return {(subject, kind): count for subject in SUBJECTS for kind, count in self.COUNTS.items()}


def _share(value):
    """A positive part of a question's points: a whole number, a half, or an equal share between blanks (a multiple of 1/12)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
            and abs(value * 12 - round(value * 12)) < 1e-6)


OBJECTIVE = PaperSpec(OBJECTIVE_SPEC, "客观题卷", OBJECTIVE_TYPES, "objective")
OBJECTIVE_V5 = PaperSpec(OBJECTIVE_V5_SPEC, "客观题卷（旧规格）", ("single_choice", "fill_blank"), "objective_old",
                         counts=V5_OBJECTIVE_COUNTS, points=V5_POINTS, retired=True)
SUBJECTIVE_PAPER = PaperSpec(SUBJECTIVE_SPEC, "主观题卷", SUBJECTIVE_TYPES, "subjective", configurable_duration=True)
PAPERS = {OBJECTIVE_SPEC: OBJECTIVE, SUBJECTIVE_SPEC: SUBJECTIVE_PAPER}                 # the papers made every day
ALL_PAPERS = {**PAPERS, OBJECTIVE_V5_SPEC: OBJECTIVE_V5}                                # ... and the earlier one, still readable
KIND_OF = {OBJECTIVE_SPEC: "objective", OBJECTIVE_V5_SPEC: "objective_old", SUBJECTIVE_SPEC: "subjective",
           "xiaohan-60q-300pt-v4": "mixed", "xiaohan-60q-150pt-v2": "mixed", "xiaohan-150-60-v1": "mixed"}
KIND_LABEL = {"objective": "客观题卷", "objective_old": "客观题卷（旧规格）", "subjective": "主观题卷", "mixed": "旧版混合卷"}


def kind_of(specification):
    return KIND_OF.get(specification, "mixed")


def formal_specification():
    """What /training-specification reports: the two papers and the daily demand."""
    return {
        "papers": [OBJECTIVE.formal_specification(), SUBJECTIVE_PAPER.formal_specification()],
        "daily_demand_per_subject": dict(COUNTS),
        "reserve_per_subject": dict(RESERVE),
        "points_by_type": dict(POINTS),
        "max_blanks": MAX_BLANKS,
        "points_per_paper": TOTAL_POINTS,
    }
