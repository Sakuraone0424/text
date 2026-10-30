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
    assert [kind for subject, kind, _ in result[4:]] == [
        "short_answer", "term", "fill_blank", "single_choice"
    ]


def test_prevent_repeated_failed_single_choice_generations_from_starving_other_kinds():
    result = balanced_generation_buckets({("biochemistry", "single_choice"): 20})
    assert result[-1][:2] == ("biochemistry", "single_choice")


def test_nine_point_mechanism_uses_same_book_clean_neighbor_evidence(monkeypatch):
    from app import auto_generation as generation

    class FakeConn:
        def execute(self, sql, args):
            assert "document_id=anchor.document_id" in sql
            assert "u.quality_flags='[]'::jsonb" in sql
            assert args == (1,)
            return self
        def fetchall(self):
            return [{"id": 2}, {"id": 3}, {"id": 4}]

    primary = dict(unit_id=1, chapter="Chapter 4", file_id="book",
                   source_sha256="a" * 64, subject="biochemistry",
                   source_kind="textbook", quality_flags=[], text="A" * 120)
    neighbors = {
        2: {**primary, "unit_id": 2, "text": "B" * 140},
        3: {**primary, "unit_id": 3, "quality_flags": ["ocr_uncertain"], "text": "C" * 140},
        4: {**primary, "unit_id": 4, "text": "D" * 140},
    }
    monkeypatch.setattr(generation, "snapshot_unit", lambda conn, ident: neighbors[ident])
    monkeypatch.setattr(generation, "pinned_source_bytes", lambda f, sha: True)
    result = generation.bounded_textbook_evidence(FakeConn(), primary, "short_answer")
    assert [x["unit_id"] for x in result] == [1, 2, 4]
    assert generation.bounded_textbook_evidence(None, primary, "fill_blank") == [primary]
    assert generation.bounded_textbook_evidence(None, primary, "single_choice") == [primary]
