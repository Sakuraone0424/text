"""No Railway calls or resources: UUID permission boundary regression."""

import pytest
from app.deployment_target import verify_isolated_target, TARGET


@pytest.fixture
def target(monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_NAME", "amiable-vibrancy")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv("QUIZ_ISOLATED_TARGET_APPROVED", "1")
    monkeypatch.setenv("QUIZ_DB_SCHEMA", "source_staging_real_20261026")
    for kind, value in TARGET.items():
        monkeypatch.setenv(f"RAILWAY_{kind}_ID", value)
        monkeypatch.setenv(f"QUIZ_APPROVED_RAILWAY_{kind}_ID", value)


def test_isolated_project_can_have_production_display_name(target):
    verify_isolated_target()


@pytest.mark.parametrize("kind", ["PROJECT", "ENVIRONMENT", "SERVICE"])
def test_other_target_cannot_reuse_approval(target, monkeypatch, kind):
    monkeypatch.setenv(f"RAILWAY_{kind}_ID", "00000000-0000-4000-8000-999999999999")
    with pytest.raises(PermissionError):
        verify_isolated_target()


def test_original_project_still_protected(target, monkeypatch):
    monkeypatch.setenv("RAILWAY_PROJECT_NAME", "xiao-ustc-quiz")
    with pytest.raises(PermissionError, match="protected"):
        verify_isolated_target()


def test_names_without_uuid_approval_are_insufficient(target, monkeypatch):
    monkeypatch.delenv("QUIZ_APPROVED_RAILWAY_PROJECT_ID")
    with pytest.raises(PermissionError):
        verify_isolated_target()


def test_approval_missing_blocks_before_database_connection(target, monkeypatch):
    from app.db import get_db

    monkeypatch.setenv("QUIZ_AUTO_ONLY", "1")
    monkeypatch.setenv("QUIZ_ISOLATED_TARGET_APPROVED", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://not-to-be-contacted.invalid/db")
    with pytest.raises(PermissionError):
        with get_db():
            pytest.fail("Database must not be contacted")


def test_matching_unknown_uuid_pair_still_denied(target, monkeypatch):
    value = "00000000-0000-4000-8000-999999999999"
    monkeypatch.setenv("RAILWAY_PROJECT_ID", value)
    monkeypatch.setenv("QUIZ_APPROVED_RAILWAY_PROJECT_ID", value)
    with pytest.raises(PermissionError):
        verify_isolated_target()


def test_original_uuid_denied_even_if_display_renamed(target, monkeypatch):
    from app.deployment_target import PROTECTED_PROJECT

    monkeypatch.setenv("RAILWAY_PROJECT_ID", PROTECTED_PROJECT)
    with pytest.raises(PermissionError, match="protected"):
        verify_isolated_target()


def test_missing_platform_metadata_not_treated_as_local(monkeypatch):
    from app.deployment_target import on_railway

    monkeypatch.setenv("QUIZ_REQUIRE_RAILWAY_IDENTITY", "1")
    assert on_railway()
    with pytest.raises(PermissionError):
        verify_isolated_target()
