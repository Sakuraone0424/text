"""Structural checks complement, never replace, expert textbook verification."""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Literal

from app.training_spec import POINTS, DURATION_SECONDS, validate_quotas

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

Subject = Literal["biochemistry", "cell_biology"]
QuestionType = Literal["single_choice", "fill_blank", "term", "short_answer", "essay"]
TYPES = ("single_choice", "fill_blank", "term", "short_answer", "essay")
AXES = ("scientific_correctness", "exam_style", "uniqueness", "type_fit", "duplication")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Evidence(StrictModel):
    source_unit_id: int | None = Field(default=None, ge=1)
    textbook: str = Field(min_length=1, max_length=300)
    authors: str = Field(min_length=1, max_length=300)
    edition: str = Field(min_length=1, max_length=100)
    isbn: str | None = Field(default=None, min_length=10, max_length=32)
    chapter: str = Field(min_length=1, max_length=300)
    locator: str = Field(min_length=1, max_length=300)  # page/section/figure
    excerpt: str = Field(min_length=1, max_length=2000)

    @field_validator("isbn")
    @classmethod
    def valid_isbn(cls, value):
        if value is None:
            return value
        digits = re.sub(r"[-\s]", "", value).upper()
        valid = False
        if len(digits) == 13 and digits.isdigit() and digits.startswith(("978", "979")):
            valid = (
                sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits))
                % 10
                == 0
            )
        elif len(digits) == 10 and digits[:9].isdigit() and digits[-1] in "0123456789X":
            valid = (
                sum(
                    (10 - i) * (10 if c == "X" else int(c))
                    for i, c in enumerate(digits)
                )
                % 11
                == 0
            )
        if not valid:
            raise ValueError("A valid ISBN-10 or ISBN-13 checksum is required")
        return value


class Criterion(StrictModel):
    knowledge_tags: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field(min_length=1, max_length=2000)
    points: int = Field(strict=True, ge=1, le=100)


class Blank(StrictModel):
    accepted_answers: list[str] = Field(min_length=1, max_length=100)
    points: int = Field(strict=True, ge=1, le=3)

    @model_validator(mode="after")
    def nonempty(self):
        if any(not value.strip() for value in self.accepted_answers):
            raise ValueError("Blank answers cannot be empty")
        return self


class Question(StrictModel):
    subject: Subject
    question_type: QuestionType
    stem: str = Field(min_length=5, max_length=10000)
    options: dict[str, str] = Field(default_factory=dict)
    answer: str = Field(min_length=1, max_length=10000)
    accepted_answers: list[str] = Field(default_factory=list, max_length=100)
    blanks: list[Blank] = Field(default_factory=list, max_length=3)
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    rubric: list[Criterion] = Field(min_length=1, max_length=50)
    knowledge_tags: list[str] = Field(default_factory=list, max_length=30)
    difficulty: Literal["core", "mechanism", "application"] = "core"
    style_reference_ids: list[StrictInt] = Field(default_factory=list, max_length=10)
    max_points: int = Field(strict=True, ge=1, le=100)

    @model_validator(mode="after")
    def fit(self):
        if any(
            not set(c.knowledge_tags).issubset(self.knowledge_tags) for c in self.rubric
        ):
            raise ValueError("Rubric knowledge tags must belong to the question")
        if sum(c.points for c in self.rubric) != self.max_points:
            raise ValueError("Rubric points must sum to max_points")
        if self.max_points != POINTS[self.question_type]:
            raise ValueError("max_points must match the custom 150-point specification")
        if self.question_type == "single_choice":
            if set(self.options) != set("ABCD") or self.answer not in self.options:
                raise ValueError(
                    "Single choice requires A-D options and one answer key"
                )
            if any(not v.strip() for v in self.options.values()):
                raise ValueError("Empty option")
            if len({normalize(v) for v in self.options.values()}) != 4:
                raise ValueError("Options must be distinct")
        elif self.options:
            raise ValueError("Only single choice accepts options")
        if self.blanks:
            if self.question_type != "fill_blank" or self.accepted_answers:
                raise ValueError("Multi-blank questions use blanks only")
            if (
                self.stem.count("____") != len(self.blanks)
                or sum(b.points for b in self.blanks) != self.max_points
            ):
                raise ValueError(
                    "Every blank needs a matching placeholder and points summing to max_points"
                )
        elif self.question_type == "fill_blank":
            if not self.accepted_answers or any(
                not normalize(v) for v in self.accepted_answers
            ):
                raise ValueError(
                    "Fill blank requires explicit nonempty accepted answers"
                )
            if normalize_answer(self.answer) not in {
                normalize_answer(v) for v in self.accepted_answers
            }:
                raise ValueError("Canonical answer must be accepted")
            if self.stem.count("____") != 1:
                raise ValueError("Fill blank requires exactly one ____ blank")
        elif self.accepted_answers:
            raise ValueError("accepted_answers is only for fill_blank")
        return self


class Verdict(StrictModel):
    passed: bool = Field(strict=True)
    rationale: str = Field(min_length=10, max_length=4000)


class Audit(StrictModel):
    score: StrictInt = Field(default=100, ge=0, le=100)
    reviewer: str = Field(min_length=1, max_length=200)
    checks: dict[str, Verdict]
    evidence_verification: str = Field(min_length=20, max_length=5000)

    @model_validator(mode="after")
    def axes(self):
        if set(self.checks) != set(AXES):
            raise ValueError("All five review dimensions are required")
        return self


class Blueprint(StrictModel):
    points: dict[QuestionType, StrictInt] = Field(default_factory=lambda: dict(POINTS))
    duration_seconds: Literal[3600] = DURATION_SECONDS
    specification: str = Field(min_length=1, max_length=300)
    quotas: dict[Subject, dict[QuestionType, StrictInt]]

    @model_validator(mode="after")
    def total(self):
        if set(self.quotas) != {"biochemistry", "cell_biology"}:
            raise ValueError("Both subjects are required")
        if any(set(q) != set(TYPES) for q in self.quotas.values()):
            raise ValueError("Each subject must include all five question types")
        if any(
            type(n) is not int or n < 1
            for q in self.quotas.values()
            for n in q.values()
        ):
            raise ValueError("Quotas must be positive integers")
        if sum(sum(q.values()) for q in self.quotas.values()) != 28:
            raise ValueError("Blueprint must contain exactly 28 questions")
        validate_quotas(self.quotas)
        if self.points != POINTS:
            raise ValueError("Per-type points must be exactly 2/3/4/8/20")
        return self


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def normalize_answer(text: str) -> str:
    # Scientific case is meaningful (e.g. mM and mm); variants must be explicit.
    return unicodedata.normalize("NFKC", text).strip()


def stem_key(text: str) -> str:
    return "".join(c for c in normalize(text) if c.isalnum())


def similarity(stem: str, other: str) -> float:
    a, b = stem_key(stem), stem_key(other)
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def duplicate(stem: str, other: str) -> bool:
    return similarity(stem, other) >= 0.85


def duplication_metrics(stem: str, other_stems: list[str]) -> dict:
    scores = [similarity(stem, other) for other in other_stems]
    similar_count = sum(score >= 0.85 for score in scores)
    return {
        "compared_count": len(scores),
        "similar_count": similar_count,
        "match_rate": similar_count / len(scores) if scores else 0.0,
        "max_similarity": max(scores, default=0.0),
        "threshold": 0.85,
    }


def audit_issues(content: dict, audit: Audit, other_stems: list[str]) -> list[str]:
    Question.model_validate(content)
    issues = [axis for axis, verdict in audit.checks.items() if not verdict.passed]
    if audit.score < 90:
        issues.append("quality_score_below_90")
    if any(duplicate(content["stem"], stem) for stem in other_stems):
        issues.append("duplicate_stem_similarity_at_least_0.85")
    return issues
