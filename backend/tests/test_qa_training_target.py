"""New QA cannot reuse old target approvals or silently serve 28 questions."""
import pytest

from app.deployment_target import QA_SERVICES, QA_TARGET, QA_SOURCE_SCHEMA, QA_WORKER_SERVICE, verify_isolated_target


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


def test_the_two_admitted_qa_services_are_exactly_website_and_worker():
    assert QA_SERVICES == ("b2cbe2fd-cc60-49ec-b52f-68f5fd35aa07", "9295cd65-7355-4804-aa06-f10a5327846c")


def test_qa_worker_service_is_accepted_with_its_own_approval(qa, monkeypatch):
    monkeypatch.setenv("RAILWAY_SERVICE_ID", QA_WORKER_SERVICE)
    monkeypatch.setenv("QUIZ_APPROVED_RAILWAY_SERVICE_ID", QA_WORKER_SERVICE)
    verify_isolated_target()


@pytest.mark.parametrize("actual,approved", [
    ("00000000-0000-4000-8000-999999999999", "00000000-0000-4000-8000-999999999999"),  # unknown service, self-approved
    ("00000000-0000-4000-8000-999999999999", QA_WORKER_SERVICE),  # unknown service, borrowed approval
    ("15083462-a2f4-49d0-9a72-0873208e0669", "15083462-a2f4-49d0-9a72-0873208e0669"),  # old project's service
    (QA_WORKER_SERVICE, QA_TARGET["SERVICE"]),  # worker running with the website's approval
    (QA_TARGET["SERVICE"], QA_WORKER_SERVICE),  # website running with the worker's approval
    (QA_WORKER_SERVICE, ""),
    ("not-a-uuid", "not-a-uuid"),
])
def test_any_other_qa_service_is_still_refused(qa, monkeypatch, actual, approved):
    monkeypatch.setenv("RAILWAY_SERVICE_ID", actual)
    monkeypatch.setenv("QUIZ_APPROVED_RAILWAY_SERVICE_ID", approved)
    with pytest.raises(PermissionError, match="SERVICE"):
        verify_isolated_target()


def test_the_worker_service_id_is_not_admitted_in_another_project_or_environment(qa, monkeypatch):
    monkeypatch.setenv("RAILWAY_SERVICE_ID", QA_WORKER_SERVICE)
    monkeypatch.setenv("QUIZ_APPROVED_RAILWAY_SERVICE_ID", QA_WORKER_SERVICE)
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "c6028313-e1f4-43cc-a63d-b7ff474a3a24")
    with pytest.raises(PermissionError, match="PROJECT"):
        verify_isolated_target()


def test_the_worker_id_does_not_open_the_non_qa_profile(monkeypatch):
    for key, value in {"QUIZ_ISOLATED_TARGET_APPROVED": "1", "RAILWAY_ENVIRONMENT_NAME": "qa"}.items():
        monkeypatch.setenv(key, value)
    from app.deployment_target import TARGET, SOURCE_SCHEMA
    monkeypatch.setenv("QUIZ_DB_SCHEMA", SOURCE_SCHEMA)
    for kind, value in TARGET.items():
        monkeypatch.setenv(f"RAILWAY_{kind}_ID", value)
        monkeypatch.setenv(f"QUIZ_APPROVED_RAILWAY_{kind}_ID", value)
    monkeypatch.setenv("RAILWAY_SERVICE_ID", QA_WORKER_SERVICE)
    monkeypatch.setenv("QUIZ_APPROVED_RAILWAY_SERVICE_ID", QA_WORKER_SERVICE)
    with pytest.raises(PermissionError, match="SERVICE"):
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
