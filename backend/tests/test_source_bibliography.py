"""Book metadata must be supported by the exact user's cover and SHA.

Fixture texts here are short synthetic summaries, not copied textbook pages.
"""
import pytest

from app.source_bibliography import from_locked_cover, BIBLIOGRAPHY


@pytest.mark.parametrize("key", [
    "biochemistry_upper", "biochemistry_lower", "biochemistry_notes", "cell_biology"
])
def test_verified_cover_exposes_specific_authors_and_edition(key):
    spec = BIBLIOGRAPHY[key]
    excerpt = "／".join(spec["cover_tokens"])
    result = from_locked_cover(key, "fixture-sha", excerpt, expected_sha256="fixture-sha")
    assert result["authors"] and "待核查" not in result["authors"]
    assert result["edition"] and "待核查" not in result["edition"]
    assert result["verification"] == "source_cover_tokens_and_locked_sha256"


def test_cover_only_is_not_enough_without_matching_exact_source_version():
    spec = BIBLIOGRAPHY["cell_biology"]
    with pytest.raises(ValueError, match="unmatched_locked_source_sha256"):
        from_locked_cover(
            "cell_biology", "unexpected-file",
            " ".join(spec["cover_tokens"]), expected_sha256="expected-file"
        )


def test_sha_only_is_not_enough_if_cover_text_is_missing():
    with pytest.raises(ValueError, match="bibliography_cover_tokens_missing"):
        from_locked_cover(
            "biochemistry_upper", "fixture-sha", "unrelated introductory text",
            expected_sha256="fixture-sha"
        )


def test_exam_file_is_not_falsely_treated_as_a_textbook():
    with pytest.raises(ValueError, match="no_verified_cover_metadata_for_source"):
        from_locked_cover(
            "biochemistry_exams", "fixture-sha", "2017年试题",
            expected_sha256="fixture-sha"
        )
