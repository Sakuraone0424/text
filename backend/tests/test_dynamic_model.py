"""Controlled synthetic evidence, not imported USTC papers or real student history."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from app.dynamic_model import (
    Policy,
    build_context,
    exam_statistics,
    performance_statistics,
    review_statistics,
    fractions,
)
from app.selection import pick

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
BIO = "biochemistry"


def policy(**kwargs):
    return Policy(scopes={BIO: "test-scope", "cell_biology": None}, **kwargs)


def question(i, tag, chapter=None, level="core"):
    return {
        "id": i,
        "content": {
            "subject": BIO,
            "question_type": "single_choice",
            "knowledge_tags": [tag],
            "evidence": [{"chapter": chapter or tag}],
            "difficulty": level,
        },
    }


def record(i, tag, year, **kwargs):
    tags = tag if isinstance(tag, list) else [tag]
    return dict(
        id=i,
        subject=BIO,
        scope_key="test-scope",
        year=year,
        stem=f"Synthetic original question {i}",
        options={},
        knowledge_tags=tags,
        source_kind="real_exam",
        mapping_verified=True,
        trusted=True,
        available_at=NOW.isoformat(),
        **kwargs,
    )


def observation(i, tag, score=0, maximum=2, kind="single_choice", **kwargs):
    return dict(
        attempt_id=i,
        question_id=i,
        subject=BIO,
        tags=tag if isinstance(tag, list) else [tag],
        points=score,
        max_points=maximum,
        question_type=kind,
        answered=True,
        available_at=NOW.isoformat(),
        **kwargs,
    )


def context(records=(), obs=(), cards=(), exposures=(), dependencies=(), p=None):
    return build_context(
        [question(1, "A")["content"], question(2, "B")["content"]],
        list(records),
        [],
        list(obs),
        list(cards),
        list(exposures),
        list(dependencies),
        NOW,
        p or policy(),
    )


def test_no_evidence_is_explicit_cold_start_with_nonzero_exploration():
    result = context()
    assert result["stage"] == "cold_start"
    for row in result["topics"][BIO].values():
        assert 0 < row["priority"] < 1 and set(row["degraded"]) == {
            "exam",
            "weakness",
            "review",
        }
    assert (
        result["topics"][BIO]["A"]["priority"] == result["topics"][BIO]["B"]["priority"]
    )


def test_unreviewed_scope_or_simulated_exam_never_becomes_frequency():
    real = record(1, "A", 2024)
    for field, value in [
        ("trusted", False),
        ("mapping_verified", False),
        ("source_kind", "simulation"),
        ("scope_key", "other"),
    ]:
        bad = {**real, field: value}
        result = context([bad])
        assert result["exam_subjects"][BIO]["verified_question_count"] == 0
    result = context([real], p=Policy())
    assert result["exam_subjects"][BIO]["verified_question_count"] == 0


def test_transcripts_deduplicate_but_independent_years_remain_observations():
    first = record(1, "A", 2020)
    copy = {**first, "id": 2}
    later = {**first, "id": 3, "year": 2024}
    result = context([first, copy, later])
    data = result["topics"][BIO]["A"]["exam"]
    assert (
        data["contribution_mass"] == 2
        and data["years"] == [2020, 2024]
        and data["record_ids"] == [1, 2, 3]
    )
    assert result["exam_subjects"][BIO]["verified_question_count"] == 2


def test_scientific_signs_and_case_are_not_erased_in_exam_identity():
    rows = [record(1, "A", 2024), record(2, "A", 2024), record(3, "A", 2024)]
    rows[0]["stem"] = "Concentration is 1 mM"
    rows[1]["stem"] = "Concentration is 1 mm"
    rows[2]["stem"] = "Concentration is -1 mM"
    assert context(rows)["exam_subjects"][BIO]["verified_question_count"] == 3


def test_missing_years_are_unknown_not_negative_observations():
    rows = [record(1, "A", 2014), record(2, "B", 2024)]
    samples = [
        {
            "subject": BIO,
            "scope_key": "test-scope",
            "year": year,
            "valid": True,
            "expected_questions": 1,
            "available_at": NOW.isoformat(),
        }
        for year in (2014, 2024)
    ]
    stats, meta, _ = exam_statistics(rows, samples, {BIO: {"A", "B"}}, NOW, policy())
    assert stats[(BIO, "A")]["complete_year_sample"] == 2
    assert meta[BIO]["complete_mapped_years"] == [2014, 2024]
    assert not meta[BIO]["unknown_years_are_zero"]
    assert (
        stats[(BIO, "A")]["observed_year_interval"][0]
        < 0.5
        < stats[(BIO, "A")]["observed_year_interval"][1]
    )


def test_multi_knowledge_question_has_total_contribution_one():
    rows = [
        record(
            1, ["A", "B"], 2024, tag_contributions={"A": 3, "B": 1}, primary_tags=["A"]
        )
    ]
    result = context(rows)
    masses = [
        result["topics"][BIO][tag]["exam"]["contribution_mass"] for tag in ("A", "B")
    ]
    assert masses == [0.75, 0.25] and sum(masses) == 1
    assert sum(fractions(["A", "B", "C"], primary=["A"]).values()) == 1
    with pytest.raises(ValueError):
        fractions(["A"], {"A": 0})


def test_conflicting_duplicate_mapping_is_not_silently_double_counted():
    a = record(1, "A", 2024)
    b = {**a, "id": 2, "knowledge_tags": ["B"]}
    result = context([a, b])
    assert result["exam_subjects"][BIO]["verified_question_count"] == 0
    assert all(
        r["reason"] == "unresolved_mapping_conflict"
        for r in result["excluded_exam_records"]
    )


def test_small_recent_sample_cannot_override_long_history():
    rows = [record(i, "A", 2010 + i) for i in range(1, 11)] + [record(99, "B", 2026)]
    result = context(rows)
    a = result["topics"][BIO]["A"]["exam"]
    b = result["topics"][BIO]["B"]["exam"]
    assert a["signal"] > b["signal"]
    assert 0 <= b["trend_adjustment"] <= 0.2 * b["confidence"]


def test_exam_confidence_improves_with_verified_years_and_complete_mapping():
    sparse = context([record(1, "A", 2024)])["topics"][BIO]["A"]["exam"]["confidence"]
    rows = [record(i, "A", 2010 + i) for i in range(1, 11)]
    samples = [
        dict(
            subject=BIO,
            scope_key="test-scope",
            year=r["year"],
            valid=True,
            expected_questions=1,
            available_at=NOW.isoformat(),
        )
        for r in rows
    ]
    stats, _, _ = exam_statistics(rows, samples, {BIO: {"A", "B"}}, NOW, policy())
    assert stats[(BIO, "A")]["confidence"] > sparse


def test_one_failure_is_shrunk_and_repeated_errors_increase_gap_gradually():
    a, _ = performance_statistics([observation(1, "A")], NOW, policy())
    b, _ = performance_statistics(
        [
            observation(i, "A", error_categories=["concept_unclear"])
            for i in range(1, 8)
        ],
        NOW,
        policy(),
    )
    assert 0.5 < a[(BIO, "A")]["gap"] < 0.65
    assert a[(BIO, "A")]["gap"] < b[(BIO, "A")]["gap"] < 0.95


def test_one_essay_is_not_twenty_independent_failures():
    essay, total = performance_statistics(
        [observation(1, "A", maximum=20, kind="essay")], NOW, policy()
    )
    choice, _ = performance_statistics([observation(1, "A")], NOW, policy())
    assert total == 1 and essay[(BIO, "A")]["answer_samples"] == 1
    assert (
        essay[(BIO, "A")]["effective_samples"] < choice[(BIO, "A")]["effective_samples"]
    )


def test_rubric_losses_map_to_actual_knowledge_points():
    row = observation(
        1,
        ["A", "B"],
        score=4,
        maximum=8,
        kind="short_answer",
        criteria=[
            {"points": 0, "max_points": 4, "knowledge_tags": ["A"]},
            {"points": 4, "max_points": 4, "knowledge_tags": ["B"]},
        ],
    )
    result, _ = performance_statistics([row], NOW, policy())
    assert result[(BIO, "A")]["gap"] > 0.5 > result[(BIO, "B")]["gap"]
    assert result[(BIO, "A")]["effective_samples"] < 1


def test_continuous_success_reduces_extra_reinforcement():
    failed = [observation(i, "A") for i in range(1, 6)]
    before = context(obs=failed)["topics"][BIO]["A"]["mastery"]["gap"]
    after = context(obs=failed + [observation(i, "A", score=2) for i in range(6, 31)])[
        "topics"
    ][BIO]["A"]["mastery"]["gap"]
    assert after < before


def test_unanswered_pending_duplicate_and_future_answers_do_not_update_mastery():
    valid = observation(1, "A")
    observations = [
        valid,
        valid,
        {**observation(2, "A"), "answered": False},
        {**observation(3, "A"), "points": None},
        {**observation(4, "A"), "available_at": (NOW + timedelta(days=1)).isoformat()},
    ]
    result, total = performance_statistics(observations, NOW, policy())
    assert total == 1 and result[(BIO, "A")]["answer_samples"] == 1


def test_recent_evidence_counts_more_than_distant_evidence():
    recent = observation(1, "A")
    old = {
        **observation(2, "B"),
        "available_at": (NOW - timedelta(days=180)).isoformat(),
    }
    result, _ = performance_statistics([recent, old], NOW, policy())
    assert result[(BIO, "A")]["gap"] > result[(BIO, "B")]["gap"]


def test_due_and_near_due_are_distinct_and_future_unobserved_card_ignored():
    def card(i, tag, hours, last=None):
        return dict(
            question_id=i,
            subject=BIO,
            tags=[tag],
            due=(NOW + timedelta(hours=hours)).isoformat(),
            last_review=last or (NOW - timedelta(days=1)).isoformat(),
            stability=10,
            difficulty=4,
        )

    result = review_statistics(
        [
            card(1, "A", -1),
            card(2, "B", 12),
            card(3, "C", 48),
            card(4, "D", -1, (NOW + timedelta(days=1)).isoformat()),
        ],
        NOW,
        policy(),
    )
    assert (
        result[(BIO, "A")]["need"]
        > result[(BIO, "B")]["need"]
        > result[(BIO, "C")]["need"]
    )
    assert (
        result[(BIO, "A")]["cards"][0]["due"]
        and not result[(BIO, "B")]["cards"][0]["due"]
    )
    assert result[(BIO, "B")]["cards"][0]["near_due"] and (BIO, "D") not in result


def test_weights_adjust_with_evidence_and_learning_stage():
    rows = [record(i, "A", 2010 + i) for i in range(1, 12)]
    cold = context(rows)
    warm = context(rows, obs=[observation(i, "A") for i in range(1, 50)])
    assert cold["stage"] == "cold_start" and warm["stage"] == "accumulating"
    assert warm["reference_weights"]["weakness"] > cold["reference_weights"]["weakness"]
    assert (
        warm["topics"][BIO]["A"]["effective_weights"]
        != cold["topics"][BIO]["A"]["effective_weights"]
    )
    stable = context(rows, obs=[observation(i, "A", score=2) for i in range(1, 150)])
    assert stable["stage"] == "stable"
    assert sum(warm["topics"][BIO]["A"]["effective_weights"].values()) == pytest.approx(
        1
    )


def test_preexam_adjustment_requires_real_statistics_and_personal_data():
    p = policy(target_date=NOW.date() + timedelta(days=10))
    assert context(p=p)["stage"] == "cold_start"
    rows = [record(i, "A", 2010 + i) for i in range(1, 6)]
    result = context(rows, obs=[observation(i, "A") for i in range(1, 20)], p=p)
    assert result["stage"] == "pre_exam"


def test_verified_prerequisite_gets_bounded_support_without_hardcoded_chapter():
    obs = [observation(i, "A") for i in range(1, 15)]
    before = context(obs=obs)["topics"][BIO]["B"]["priority"]
    after = context(
        obs=obs,
        dependencies=[dict(subject=BIO, knowledge_tag="A", prerequisite_tag="B")],
    )["topics"][BIO]["B"]
    assert after["priority"] > before and 0 < after["prerequisite_boost"] < 0.1


def test_recent_coverage_reduces_priority_but_never_zero():
    exposures = [
        {"subject": BIO, "tags": ["A"], "available_at": NOW.isoformat()}
        for _ in range(100)
    ]
    result = context(exposures=exposures)
    assert (
        0
        < result["topics"][BIO]["A"]["priority"]
        < result["topics"][BIO]["B"]["priority"]
    )


def test_fixed_seed_sampling_reproduces_and_never_hardcodes_core_priority():
    pool = [question(1, "A", level="core"), question(2, "B", level="application")]
    c = context()
    first = []
    second = []
    a = pick(pool, 1, Counter(), c, policy(), "fixed", BIO, "single_choice", first)
    b = pick(
        list(reversed(pool)),
        1,
        Counter(),
        c,
        policy(),
        "fixed",
        BIO,
        "single_choice",
        second,
    )
    assert a == b and first == second
    assert (
        first[0]["candidates"][0]["base_priority"]
        == first[0]["candidates"][1]["base_priority"]
    )


def test_monte_carlo_priority_bias_with_multiple_controlled_profiles():
    rows = [record(i, "A", 2010 + i) for i in range(1, 12)] + [record(50, "B", 2024)]
    pool = [question(1, "A"), question(2, "B")]
    for obs in ([], [observation(i, "A") for i in range(1, 12)]):
        c = context(rows, obs=obs)
        chosen = [
            pick(pool, 1, Counter(), c, policy(), str(seed), BIO, "single_choice", [])[
                0
            ]["id"]
            for seed in range(600)
        ]
        assert chosen.count(1) > 350
    cold = context()
    exploratory = [
        pick(pool, 1, Counter(), cold, policy(), str(seed), BIO, "single_choice", [])[
            0
        ]["id"]
        for seed in range(600)
    ]
    assert 240 < exploratory.count(1) < 360


def test_replay_is_cutoff_safe_and_future_exam_records_are_not_used():
    past = record(1, "A", 2024)
    future = {
        **record(2, "B", 2026),
        "available_at": (NOW + timedelta(days=1)).isoformat(),
    }
    result = context([past, future])
    assert result["exam_subjects"][BIO]["verified_question_count"] == 1
    assert result["topics"][BIO]["B"]["exam"]["record_ids"] == []


@pytest.mark.parametrize(
    "override",
    [
        {"weights": {"exam": 1, "weakness": 0, "review": 0}},
        {"exploration": 0},
        {"recency_half_life_years": 1},
        {"weights": {"exam": float("nan"), "weakness": 0.3, "review": 0.15}},
    ],
)
def test_invalid_policy_or_zero_exploration_rejected(override):
    with pytest.raises(ValidationError):
        Policy(**override)


def test_subjects_calibrate_independently_without_cross_subject_mastery():
    questions = [
        question(1, "A")["content"],
        {**question(2, "B")["content"], "subject": "cell_biology"},
    ]
    result = build_context(
        questions,
        [],
        [],
        [observation(i, "A", score=2) for i in range(1, 130)],
        [],
        [],
        [],
        NOW,
        policy(),
    )
    assert result["subject_stages"] == {BIO: "stable", "cell_biology": "cold_start"}
    assert result["topics"]["cell_biology"]["B"]["mastery"]["answer_samples"] == 0
    assert (
        result["subject_reference_weights"][BIO]
        != result["subject_reference_weights"]["cell_biology"]
    )


def test_phase_calendar_uses_shanghai_not_utc_midnight():
    cutoff = datetime(
        2026, 11, 3, 16, 30, tzinfo=timezone.utc
    )  # November 4 in Shanghai, 45 days before target.
    rows = [record(i, "A", 2010 + i) for i in range(1, 6)]
    result = build_context(
        [question(1, "A")["content"]],
        rows,
        [],
        [observation(i, "A") for i in range(1, 20)],
        [],
        [],
        [],
        cutoff,
        policy(),
    )
    assert result["stage"] == "pre_exam"


def test_exam_statistics_retain_question_type_and_fractional_role():
    rows = [
        record(i, "A", 2010 + i, question_type="single_choice") for i in range(1, 6)
    ] + [record(99, ["A", "B"], 2024, question_type="essay", primary_tags=["B"])]
    result = context(rows)
    assert result["exam_subjects"][BIO]["question_type_counts"] == {
        "single_choice": 5,
        "essay": 1,
    }
    assert (
        result["topics"][BIO]["A"]["exam"]["type_contributions"]["single_choice"] == 5
    )
    assert result["topics"][BIO]["B"]["exam"]["type_contributions"][
        "essay"
    ] == pytest.approx(2 / 3)


def test_exam_identity_preserves_superscripts_and_word_boundaries():
    from app.dynamic_model import canonical_exam

    assert canonical_exam({"stem": "x²", "options": {}}) != canonical_exam(
        {"stem": "x2", "options": {}}
    )
    assert canonical_exam({"stem": "now here", "options": {}}) != canonical_exam(
        {"stem": "nowhere", "options": {}}
    )
    assert canonical_exam({"stem": "ATP  synthase", "options": {}}) == canonical_exam(
        {"stem": "ATP\nsynthase", "options": {}}
    )


def test_many_questions_in_one_year_do_not_replace_longitudinal_confidence():
    one_year = [record(i, "A", 2024) for i in range(1, 61)] + [record(99, "B", 2024)]
    result = context(one_year)
    a = result["topics"][BIO]["A"]["exam"]
    b = result["topics"][BIO]["B"]["exam"]
    assert (
        a["confidence"] < 0.1 and 0.4 < a["signal"] < 0.65 and 0.3 < b["signal"] < 0.6
    )
    assert a["reliability_shrinkage"] < 0.3
