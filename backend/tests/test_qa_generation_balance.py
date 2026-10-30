"""The 120-real-question reserve does not starve minor quota buckets."""
from app.auto_generation import balanced_generation_buckets
from app.training_spec_v2 import SUBJECTS, COUNTS


def test_balanced_attempts_cover_all_eight_buckets():
    buckets = balanced_generation_buckets({})
    assert len(buckets) == 8
    assert {(s, k) for s, k, _ in buckets} == {
        (s, k) for s in SUBJECTS for k in COUNTS
    }
    assert buckets == balanced_generation_buckets({})


def test_per_quota_attempt_balance_does_not_count_scientific_approvals():
    attempts = {
        ("biochemistry", "single_choice"): 18,
        ("biochemistry", "fill_blank"): 7,
        ("biochemistry", "term"): 4,
        ("biochemistry", "short_answer"): 1,
    }
    result = balanced_generation_buckets(attempts)
    assert all(subject == "cell_biology" for subject, _, _ in result[:4])
    assert [kind for subject, kind, _ in result[4:]] == list(COUNTS)


def test_prevent_repeated_failed_single_choice_generations_from_starving_other_kinds():
    result = balanced_generation_buckets({("biochemistry", "single_choice"): 20})
    assert result[-1][:2] == ("biochemistry", "single_choice")
