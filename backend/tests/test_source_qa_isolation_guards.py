"""Fail-closed checks for source intake against known production connections.

Pure unit tests: no live database, private source files or paid model calls.
"""
import pytest

from app import source_intake


@pytest.mark.parametrize("runtime", ["production", "PRODUCTION", " prod "])
def test_intake_refuses_production_runtime_before_connect(monkeypatch, runtime):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", runtime)
    monkeypatch.setenv(
        "SOURCE_STAGING_DATABASE_URL", "postgresql://qa:secret@qa-db.internal:5432/qa"
    )

    def no_connection(*args, **kwargs):
        pytest.fail("Production runtime attempted a database connection")

    monkeypatch.setattr(source_intake.psycopg, "connect", no_connection)
    with pytest.raises(ValueError, match="production Railway runtime"):
        with source_intake.isolated_db("source_staging_test", "staging"):
            pytest.fail("Unsafe runtime opened the database")


@pytest.mark.parametrize("production_variable", ["DATABASE_URL", "PRODUCTION_DATABASE_URL"])
@pytest.mark.parametrize(
    "staging_url,production_url",
    [
        (
            "postgresql://qa:qa-pass@db.internal:5432/qa_database",
            "postgresql://prod:prod-pass@db.internal:5432/production_database",
        ),
        (
            "host=Db.Internal port=5432 dbname=qa user=qa",
            "postgresql://prod:pass@db.internal/production",
        ),
        (
            "postgresql://qa:pass@localhost:5432/qa",
            "postgresql:///production",
        ),
    ],
)
def test_shared_production_cluster_is_rejected_before_connect(
    monkeypatch, production_variable, staging_url, production_url
):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    for key in ("DATABASE_URL", "PRODUCTION_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SOURCE_STAGING_DATABASE_URL", staging_url)
    monkeypatch.setenv(production_variable, production_url)

    def no_connection(*args, **kwargs):
        pytest.fail("Known production endpoint attempted a database connection")

    monkeypatch.setattr(source_intake.psycopg, "connect", no_connection)
    with pytest.raises(ValueError, match="shares a PostgreSQL endpoint"):
        with source_intake.isolated_db("source_staging_test", "staging"):
            pytest.fail("Known production endpoint accepted")


def test_different_database_endpoint_reaches_only_explicit_connection(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod@production.internal/prod")
    monkeypatch.setenv(
        "SOURCE_STAGING_DATABASE_URL", "postgresql://qa@independent-qa.internal/qa"
    )
    calls = []

    def record_connection(dsn, **kwargs):
        calls.append(dsn)
        raise RuntimeError("offline_test_connection_sentinel")

    monkeypatch.setattr(source_intake.psycopg, "connect", record_connection)
    with pytest.raises(RuntimeError, match="offline_test_connection_sentinel"):
        with source_intake.isolated_db("source_staging_test", "staging"):
            pass
    assert calls == ["postgresql://qa@independent-qa.internal/qa"]


def test_malformed_target_fails_before_network(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PRODUCTION_DATABASE_URL", raising=False)
    monkeypatch.setenv("SOURCE_STAGING_DATABASE_URL", "not a valid postgres uri **")

    def no_connection(*args, **kwargs):
        pytest.fail("Malformed target attempted a database connection")

    monkeypatch.setattr(source_intake.psycopg, "connect", no_connection)
    with pytest.raises(ValueError, match="Invalid database connection configuration"):
        with source_intake.isolated_db("source_staging_test", "staging"):
            pass
