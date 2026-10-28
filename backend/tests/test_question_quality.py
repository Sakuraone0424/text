import pytest
from pydantic import ValidationError
from app.question_quality import Audit, Blueprint, Question, audit_issues, duplicate


@pytest.mark.parametrize("field", ["evidence", "rubric", "answer"])
def test_missing_required_content(question_factory, field):
    q = question_factory().model_dump()
    q[field] = [] if field != "answer" else ""
    with pytest.raises(ValidationError):
        Question.model_validate(q)


@pytest.mark.parametrize(
    "field", ["textbook", "edition", "chapter", "locator", "excerpt", "isbn", "authors"]
)
def test_evidence_requires_each_locator_component(question_factory, field):
    q = question_factory().model_dump()
    q["evidence"][0][field] = ""
    with pytest.raises(ValidationError):
        Question.model_validate(q)


@pytest.mark.parametrize(
    "kind", ["single_choice", "fill_blank", "term", "short_answer", "essay"]
)
def test_all_types_have_answer_evidence_rubric(question_factory, kind):
    from app.training_spec import POINTS

    assert question_factory(kind=kind).max_points == POINTS[kind]


def test_type_and_rubric_checks(question_factory):
    q = question_factory().model_dump()
    q["options"]["B"] = q["options"]["A"]
    with pytest.raises(ValidationError):
        Question.model_validate(q)
    q = question_factory(kind="essay").model_dump()
    q["options"] = {"A": "bad"}
    with pytest.raises(ValidationError):
        Question.model_validate(q)
    q = question_factory().model_dump()
    q["max_points"] = 3
    with pytest.raises(ValidationError):
        Question.model_validate(q)
    q = question_factory(kind="fill_blank").model_dump()
    q["accepted_answers"] = ["other"]
    with pytest.raises(ValidationError):
        Question.model_validate(q)


def test_audit_cannot_omit_dimensions(passing_audit):
    data = passing_audit.model_dump()
    del data["checks"]["scientific_correctness"]
    with pytest.raises(ValidationError):
        Audit.model_validate(data)
    data = passing_audit.model_dump()
    data["evidence_verification"] = "yes"
    with pytest.raises(ValidationError):
        Audit.model_validate(data)


def test_failed_science_or_duplicate_blocks(question_factory, passing_audit):
    q = question_factory()
    assert audit_issues(q.model_dump(), passing_audit, [q.stem])
    bad = passing_audit.model_copy(deep=True)
    bad.checks["scientific_correctness"].passed = False
    assert "scientific_correctness" in audit_issues(q.model_dump(), bad, [])
    assert duplicate("α-螺旋 的结构？", "α-螺旋的结构!")
    assert duplicate(
        "Discuss enzyme kinetics and competitive inhibition",
        "Discuss enzyme kinetics and competitive inhibitions",
    )
    assert not duplicate(
        "DNA replication initiation", "Mitochondrial membrane transport"
    )


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "4"])
def test_blueprint_rejects_invalid_quotas(blueprint, bad):
    data = blueprint.model_dump()
    data["quotas"]["biochemistry"]["single_choice"] = bad
    with pytest.raises(ValidationError):
        Blueprint.model_validate(data)


def test_blueprint_requires_28_both_subjects_all_types(blueprint):
    data = blueprint.model_dump()
    data["quotas"]["biochemistry"]["essay"] += 1
    with pytest.raises(ValidationError):
        Blueprint.model_validate(data)
    data = blueprint.model_dump()
    del data["quotas"]["cell_biology"]
    with pytest.raises(ValidationError):
        Blueprint.model_validate(data)


def test_isbn_checksum_rejects_typo(question_factory):
    data = question_factory().model_dump()
    data["evidence"][0]["isbn"] = "9780306406158"
    with pytest.raises(ValidationError, match="checksum"):
        Question.model_validate(data)


def test_scientific_answer_case_is_not_silently_folded():
    from app.question_quality import normalize_answer

    assert normalize_answer(" mM ") == "mM"
    assert normalize_answer("mM") != normalize_answer("mm")


def test_repeat_rate_is_reported():
    from app.question_quality import duplication_metrics

    result = duplication_metrics(
        "DNA replication initiation",
        ["DNA replication initiation", "Mitochondrial structure"],
    )
    assert result["compared_count"] == 2 and result["similar_count"] == 1
    assert result["match_rate"] == 0.5 and result["max_similarity"] == 1.0
