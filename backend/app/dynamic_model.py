"""Transparent training priorities, not estimates of future exam probability."""

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
import unicodedata
from zoneinfo import ZoneInfo

from pydantic import Field, ConfigDict, model_validator
from app.question_quality import StrictModel

ALGORITHM_VERSION = "knowledge-priority/1.0"


class Policy(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"exam": 0.55, "weakness": 0.30, "review": 0.15}
    )
    scopes: dict[str, str | None] = Field(
        default_factory=lambda: {"biochemistry": None, "cell_biology": None}
    )
    type_adjustment: float = Field(default=0.1, ge=0, le=0.3)
    cognitive_adjustment: float = Field(default=0.2, ge=0, le=0.3)
    exploration: float = Field(default=0.08, ge=0.01, le=0.5)
    mastery_prior: float = Field(default=4, ge=2, le=50)
    exam_prior: float = Field(default=4, ge=2, le=50)
    exam_year_scale: int = Field(default=5, ge=3, le=30)
    personal_scale: float = Field(default=20, ge=5, le=200)
    recency_half_life_years: float = Field(default=5, ge=3, le=20)
    performance_half_life_days: float = Field(default=30, ge=7, le=180)
    near_due_hours: float = Field(default=24, ge=1, le=168)
    coverage_strength: float = Field(default=0.6, ge=0.1, le=3)
    chapter_share: float = Field(default=0.45, ge=0.25, le=1)
    subjective_reliability: float = Field(default=0.65, ge=0.25, le=1)
    cold_start_answers: int = Field(default=10, ge=1, le=200)
    stable_answers: int = Field(default=100, ge=10, le=10000)
    stable_gap: float = Field(default=0.3, ge=0.05, le=0.49)
    pre_exam_days: int = Field(default=45, ge=1, le=120)
    coverage_days: int = Field(default=14, ge=1, le=90)
    target_date: date = date(2026, 12, 19)

    @model_validator(mode="after")
    def shape(self):
        if (
            set(self.weights) != {"exam", "weakness", "review"}
            or any(not math.isfinite(x) or x <= 0 for x in self.weights.values())
            or abs(sum(self.weights.values()) - 1) > 1e-8
        ):
            raise ValueError("Three positive reference weights must sum to one")
        if set(self.scopes) != {"biochemistry", "cell_biology"}:
            raise ValueError("An explicit scope or null is required for each subject")
        return self


def instant(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("An aware timestamp is required")
    return result.astimezone(timezone.utc)


def known_at(row, as_of, field="available_at"):
    return field not in row or row[field] is not None and instant(row[field]) <= as_of


def canonical_exam(row):
    if row.get("canonical_item_id"):
        return row["canonical_item_id"]
    # Preserve scientific case, signs and formula punctuation; do not merge mM with mm.
    clean = lambda v: " ".join(unicodedata.normalize("NFC", v).split())
    data = {
        "stem": clean(row["stem"]),
        "options": sorted(clean(x) for x in row.get("options", {}).values()),
    }
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def fractions(tags, contributions=None, primary=None):
    tags = sorted(set(tags))
    if not tags:
        return {}
    if contributions:
        if set(contributions) != set(tags) or any(
            type(x) not in (int, float) or not math.isfinite(x) or x <= 0
            for x in contributions.values()
        ):
            raise ValueError("Every knowledge tag needs a finite positive contribution")
        values = contributions
    else:
        primary = set(primary or [])
        if not primary.issubset(tags):
            raise ValueError("Primary tags must belong to the mapped tags")
        values = {tag: 2 if tag in primary else 1 for tag in tags}
    total = sum(values.values())
    return {tag: value / total for tag, value in values.items()}


def exam_statistics(records, samples, universe, as_of, policy):
    as_of = instant(as_of)
    grouped = defaultdict(list)
    excluded = []
    for row in records:
        if (
            not policy.scopes.get(row["subject"])
            or not row.get("trusted")
            or not row.get("mapping_verified")
            or row.get("source_kind") != "real_exam"
            or not known_at(row, as_of)
            or row["year"] > as_of.astimezone(ZoneInfo("Asia/Shanghai")).year
            or row.get("scope_key") != policy.scopes.get(row["subject"])
        ):
            excluded.append(
                {"id": row["id"], "reason": "unverified_out_of_scope_or_unavailable"}
            )
            continue
        grouped[(row["subject"], row["year"], canonical_exam(row))].append(row)
    effective = []
    for (subject, year, key), copies in sorted(grouped.items()):
        mappings = [
            fractions(
                r["knowledge_tags"], r.get("tag_contributions"), r.get("primary_tags")
            )
            for r in copies
        ]
        if not mappings[0] or any(m != mappings[0] for m in mappings):
            excluded.extend(
                {"id": r["id"], "reason": "unresolved_mapping_conflict"} for r in copies
            )
            continue
        representative = min(copies, key=lambda r: r["id"])
        effective.append(
            {
                **representative,
                "contributions": mappings[0],
                "record_ids": sorted(r["id"] for r in copies),
            }
        )
    full = defaultdict(set)
    for sample in samples:
        if (
            sample.get("valid")
            and known_at(sample, as_of)
            and sample["scope_key"] == policy.scopes.get(sample["subject"])
            and sample["year"] <= as_of.astimezone(ZoneInfo("Asia/Shanghai")).year
        ):
            actual = sum(
                r["subject"] == sample["subject"] and r["year"] == sample["year"]
                for r in effective
            )
            if actual == sample["expected_questions"]:
                full[sample["subject"]].add(sample["year"])
    stats = {}
    subject_meta = {}
    for subject, tags in universe.items():
        rows = [r for r in effective if r["subject"] == subject]
        years = sorted(set(r["year"] for r in rows))
        masses = Counter()
        cognitive = Counter()
        question_types = Counter()
        for r in rows:
            masses.update(r["contributions"])
            if r.get("question_type"):
                question_types[r["question_type"]] += 1
            if r.get("cognitive_level"):
                cognitive[r["cognitive_level"]] += 1
        max_mass = max(masses.values(), default=0)
        confidence = (
            len(years)
            / (len(years) + policy.exam_year_scale)
            * (0.4 + 0.6 * len(full[subject]) / max(1, len(years)))
        )
        subject_meta[subject] = {
            "verified_question_count": len(rows),
            "positive_years": years,
            "complete_mapped_years": sorted(full[subject]),
            "confidence": confidence,
            "cognitive_counts": dict(cognitive),
            "question_type_counts": dict(question_types),
            "unknown_years_are_zero": False,
        }
        for tag in sorted(tags):
            relevant = [r for r in rows if tag in r["contributions"]]
            positive = sorted(set(r["year"] for r in relevant))
            n = len(full[subject])
            success = len(set(positive) & full[subject])
            # Wilson interval describes observed complete years only, never a future forecast.
            if n:
                p = success / n
                z = 1.96
                denom = 1 + z * z / n
                center = (p + z * z / (2 * n)) / denom
                radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
                interval = [max(0, center - radius), min(1, center + radius)]
            else:
                interval = [0.0, 1.0]
            share = (
                (masses[tag] + policy.exam_prior / max(1, len(tags)))
                / (max_mass + policy.exam_prior)
                if rows
                else 0.5
            )
            presence = (success + 0.5) / (n + 1) if n else 0.5
            historical = 0.65 * share + 0.35 * presence
            recent = sum(
                r["contributions"][tag]
                * 2 ** (-(as_of.year - r["year"]) / policy.recency_half_life_years)
                for r in relevant
            )
            recent_share = recent / max(1, masses[tag])
            trend = min(0.2, max(0, recent_share - 0.5) * 0.4) * confidence
            stats[(subject, tag)] = {
                "signal": 0.5
                + math.sqrt(confidence) * (min(1, historical + trend) - 0.5),
                "unshrunk_historical_signal": historical,
                "reliability_shrinkage": math.sqrt(confidence),
                "confidence": confidence,
                "contribution_mass": masses[tag],
                "complete_year_occurrences": success,
                "complete_year_sample": n,
                "observed_year_interval": interval,
                "years": positive,
                "record_ids": sorted(i for r in relevant for i in r["record_ids"]),
                "recent_contribution": recent,
                "trend_adjustment": trend,
                "question_count": len(relevant),
                "type_contributions": {
                    kind: sum(
                        r["contributions"][tag]
                        for r in relevant
                        if r.get("question_type") == kind
                    )
                    for kind in question_types
                },
                "source_refs": [
                    {
                        "year": r["year"],
                        "record_ids": r["record_ids"],
                        "original_question_id": r.get("original_question_id"),
                        "source_unit_id": r.get("source_unit_id"),
                        "scope_key": r.get("scope_key"),
                        "question_type": r.get("question_type"),
                        "contribution": r["contributions"][tag],
                        "primary": tag in r.get("primary_tags", []),
                        "annotation_id": r.get("annotation_id"),
                    }
                    for r in relevant
                ],
            }
    return stats, subject_meta, excluded


def performance_statistics(observations, as_of, policy):
    as_of = instant(as_of)
    groups = defaultdict(list)
    seen = set()
    for row in sorted(
        observations,
        key=lambda r: (instant(r["available_at"]), r["attempt_id"], r["question_id"]),
    ):
        identity = (row["attempt_id"], row["question_id"])
        if (
            identity in seen
            or not known_at(row, as_of)
            or not row.get("answered")
            or row.get("points") is None
        ):
            continue
        seen.add(identity)
        mapping = fractions(row["tags"])
        for tag, contribution in mapping.items():
            maximum = row["max_points"]
            score = row["points"]
            criteria = row.get("criteria", [])
            linked = [c for c in criteria if tag in c.get("knowledge_tags", [])]
            if linked:
                maximum = sum(c["max_points"] for c in linked)
                score = sum(c["points"] for c in linked)
            loss = 1 - min(1, max(0, score / max(1, maximum)))
            age = max(0, (as_of - instant(row["available_at"])).total_seconds() / 86400)
            reliability = (
                policy.subjective_reliability
                if row["question_type"] not in ("single_choice", "fill_blank")
                else 1.0
            )
            weight = (
                contribution
                * reliability
                * 2 ** (-age / policy.performance_half_life_days)
            )
            groups[(row["subject"], tag)].append(
                {
                    "loss": loss,
                    "weight": weight,
                    "attempt_id": row["attempt_id"],
                    "question_id": row["question_id"],
                    "question_type": row["question_type"],
                    "available_at": instant(row["available_at"]).isoformat(),
                    "errors": row.get("error_categories", []),
                    "cognitive_level": row.get("cognitive_level"),
                    "rubric_gaps": sum(c["points"] < c["max_points"] for c in criteria),
                }
            )
    result = {}
    for key, rows in groups.items():
        effective = sum(r["weight"] for r in rows)
        gap = (
            policy.mastery_prior * 0.5 + sum(r["weight"] * r["loss"] for r in rows)
        ) / (policy.mastery_prior + effective)
        errors = Counter(e for r in rows[-5:] for e in set(r["errors"]))
        repeat = min(0.12, max(0, max(errors.values(), default=0) - 1) * 0.03)
        result[key] = {
            "gap": min(
                0.95, gap + repeat * effective / (effective + policy.mastery_prior)
            ),
            "confidence": effective / (effective + policy.mastery_prior),
            "effective_samples": effective,
            "answer_samples": len(rows),
            "recent": rows[-5:],
            "repeated_errors": dict(errors),
            "rubric_gap_count": sum(r["rubric_gaps"] for r in rows),
        }
    return result, len(seen)


def review_statistics(cards, as_of, policy):
    as_of = instant(as_of)
    result = defaultdict(list)
    for row in cards:
        if not row.get("last_review") or instant(row["last_review"]) > as_of:
            continue
        seconds = (instant(row["due"]) - as_of).total_seconds()
        horizon = policy.near_due_hours * 3600
        need = 1.0 if seconds <= 0 else 0.5 * math.exp(-seconds / horizon)
        for tag in row["tags"]:
            result[(row["subject"], tag)].append(
                {
                    "question_id": row["question_id"],
                    "need": need,
                    "due": seconds <= 0,
                    "near_due": 0 < seconds <= horizon,
                    "stability": row.get("stability"),
                    "difficulty": row.get("difficulty"),
                    "last_review": row["last_review"],
                    "next_review": row["due"],
                }
            )
    return {
        k: {"need": max(r["need"] for r in rows), "cards": rows, "confidence": 1.0}
        for k, rows in result.items()
    }


def topics(question):
    return sorted(
        set(
            question.get("knowledge_tags")
            or ["chapter:" + question["evidence"][0]["chapter"]]
        )
    )


def build_context(
    questions,
    records,
    samples,
    observations,
    cards,
    exposures,
    dependencies,
    as_of,
    policy,
):
    as_of = instant(as_of)
    universe = defaultdict(set)
    for q in questions:
        universe[q["subject"]].update(topics(q))
    exam, subject_meta, excluded = exam_statistics(
        records, samples, universe, as_of, policy
    )
    mastery, total = performance_statistics(observations, as_of, policy)
    review = review_statistics(cards, as_of, policy)
    subject_stages = {}
    subject_references = {}
    days = (
        policy.target_date - as_of.astimezone(ZoneInfo("Asia/Shanghai")).date()
    ).days
    for subject in universe:
        actual = {
            (r["attempt_id"], r["question_id"])
            for r in observations
            if r["subject"] == subject
            and known_at(r, as_of)
            and r.get("answered")
            and r.get("points") is not None
        }
        n = len(actual)
        observed = [r for (s, t), r in mastery.items() if s == subject]
        mean_gap = sum(r["gap"] for r in observed) / max(1, len(observed))
        stage = (
            "cold_start"
            if n < policy.cold_start_answers
            else "stable"
            if n >= policy.stable_answers and mean_gap < policy.stable_gap
            else "accumulating"
        )
        if (
            0 <= days <= policy.pre_exam_days
            and n >= policy.cold_start_answers
            and len(subject_meta[subject]["positive_years"]) >= 3
        ):
            stage = "pre_exam"
        reference = dict(policy.weights)
        progress = n / (n + policy.personal_scale * 4)
        delta = min(0.15, reference["exam"] * 0.4) * progress
        reference["exam"] -= delta
        reference["weakness"] += delta
        if stage == "stable":
            reference["review"] += 0.05
            reference["weakness"] = max(0.01, reference["weakness"] - 0.05)
        if stage == "pre_exam":
            reference["exam"] += 0.03
            reference["weakness"] = max(0.01, reference["weakness"] - 0.03)
        denominator = sum(reference.values())
        reference = {k: v / denominator for k, v in reference.items()}
        subject_stages[subject] = stage
        subject_references[subject] = reference
    stages = set(subject_stages.values())
    stage = (
        next(iter(stages)) if len(stages) == 1 else "mixed" if stages else "cold_start"
    )
    reference = (
        {
            k: sum(r[k] for r in subject_references.values()) / len(subject_references)
            for k in policy.weights
        }
        if subject_references
        else dict(policy.weights)
    )
    coverage = Counter()
    for row in exposures:
        if (
            known_at(row, as_of)
            and 0
            <= (as_of - instant(row["available_at"])).total_seconds()
            <= policy.coverage_days * 86400
        ):
            for tag in row["tags"]:
                coverage[(row["subject"], tag)] += 1 / max(1, len(row["tags"]))
    scored = {}
    for subject, tags in universe.items():
        scored[subject] = {}
        reference_for_subject = subject_references[subject]
        for tag in sorted(tags):
            key = (subject, tag)
            e = exam[key]
            m = mastery.get(
                key,
                {
                    "gap": 0.5,
                    "confidence": 0,
                    "answer_samples": 0,
                    "effective_samples": 0,
                    "recent": [],
                    "rubric_gap_count": 0,
                    "repeated_errors": {},
                },
            )
            r = review.get(key, {"need": 0.0, "confidence": 0.0, "cards": []})
            raw = {
                "exam": reference_for_subject["exam"] * e["confidence"],
                "weakness": reference_for_subject["weakness"] * m["confidence"],
                "review": reference_for_subject["review"] * r["confidence"],
            }
            norm = sum(raw.values())
            weights = {k: v / norm if norm else 0.0 for k, v in raw.items()}
            weighted = (
                weights["exam"] * e["signal"]
                + weights["weakness"] * m["gap"]
                + weights["review"] * r["need"]
                if norm
                else 0.5
            )
            synergy = (
                0.2 * e["signal"] * m["gap"] * min(e["confidence"], m["confidence"])
            )
            boost = 0.0
            for dep in dependencies:
                if dep["subject"] == subject and dep["prerequisite_tag"] == tag:
                    related = mastery.get((subject, dep["knowledge_tag"]), {})
                    boost = max(
                        boost,
                        0.1 * related.get("gap", 0) * related.get("confidence", 0),
                    )
            coverage_factor = 1 / (1 + 0.1 * coverage[key])
            priority = (
                policy.exploration
                + (1 - policy.exploration)
                * min(1, weighted + synergy + boost)
                * coverage_factor
            )
            scored[subject][tag] = {
                "priority": priority,
                "effective_weights": weights,
                "exam": e,
                "mastery": m,
                "review": r,
                "recent_exposures": coverage[key],
                "prerequisite_boost": boost,
                "degraded": [k for k, v in raw.items() if not v],
                "meaning": "training_priority_not_exam_probability",
            }
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "as_of": as_of.isoformat(),
        "stage": stage,
        "answer_samples": total,
        "reference_weights": reference,
        "subject_stages": subject_stages,
        "subject_reference_weights": subject_references,
        "exam_subjects": subject_meta,
        "excluded_exam_records": excluded,
        "topics": scored,
    }
