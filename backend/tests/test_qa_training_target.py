"""New QA cannot reuse old target approvals or silently serve 28 questions."""
import pytest

from app.deployment_target import QA_TARGET, QA_SOURCE_SCHEMA, verify_isolated_target


@pytest.fixture
def qa(monkeypatch):
    monkeypatch.setenv("QUIZ_DEPLOYMENT_PROFILE", "ustc-exam-ocr-qa")
    monkeypatch.setenv("QUIZ_ISOLATED_TARGET_APPROVED", "1")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "qa")
    monkeypatch.setenv("QUIZ_DB_SCHEMA", QA_SOURCE_SCHEMA)
    monkeypatch.setenv("QUIZ_DAILY_SPECIFICATION", "xiaohan-60q-150pt-v2")
    for kind, value in QA_TARGET.items():
        monkeypatch.setenv(f"RAILWAY_{kind}_ID", value)
        monkeypatch.setenv(f"QUIZ_APPROVED_RAILWAY_{kind}_ID", value)


def test_new_qa_target_accepted(qa):
    verify_isolated_target()


@pytest.mark.parametrize("key,value", [
    ("RAILWAY_PROJECT_ID", "c6028313-e1f4-43cc-a63d-b7ff474a3a24"),
    ("RAILWAY_ENVIRONMENT_NAME", "production"),
    ("QUIZ_DB_SCHEMA", "source_staging_real_20261026"),
    ("QUIZ_DAILY_SPECIFICATION", ""),
    ("QUIZ_APPROVED_RAILWAY_SERVICE_ID", "15083462-a2f4-49d0-9a72-0873208e0669"),
])
def test_wrong_target_or_schema_rejected_before_connection(qa, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(PermissionError):
        verify_isolated_target()


def test_qa_rejects_legacy_new_paper_before_database(qa, monkeypatch):
    from fastapi import HTTPException
    from app import routes_v2
    from app.question_quality import Blueprint
    from app.training_spec import default_blueprint

    def forbidden(*args, **kwargs):
        pytest.fail("Legacy paper must be refused before touching QA data")

    monkeypatch.setattr(routes_v2, "operation", forbidden)
    with pytest.raises(HTTPException) as error:
        routes_v2.paper(Blueprint.model_validate(default_blueprint()), "owner")
    assert error.value.status_code == 409
