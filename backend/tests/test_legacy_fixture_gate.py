"""The only compatibility exception is the isolated synthetic mobile browser fixture."""
from app.legacy_fixture_gate import allowed


class FakeConnection:
    def __init__(self, schema, database="quiz_test"):
        self.schema = schema
        self.database = database

    def execute(self, query):
        self.query = query
        return self

    def fetchone(self):
        if "current_schema()" in self.query:
            return {"current_schema": self.schema}
        return {"name": self.database}


def test_exact_mobile_fixture_is_allowed_only_on_test_database(monkeypatch):
    monkeypatch.setenv("QUIZ_ENABLE_SYNTHETIC_MANUAL_FIXTURE", "1")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/quiz_test")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    schema = "source_staging_mobile_" + "a" * 32
    assert allowed(FakeConnection(schema))
    assert not allowed(FakeConnection(schema, "quiz_production"))


def test_mobile_fixture_rejects_arbitrary_staging_and_wrong_db_url(monkeypatch):
    monkeypatch.setenv("QUIZ_ENABLE_SYNTHETIC_MANUAL_FIXTURE", "1")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/quiz_test")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    assert not allowed(FakeConnection("source_staging_private_review_abcdef"))
    assert not allowed(FakeConnection("source_staging_mobile_" + "z" * 32))
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/not_quiz_test")
    assert not allowed(FakeConnection("source_staging_mobile_" + "a" * 32))


def test_mobile_fixture_rejects_real_railway_and_missing_optin(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/quiz_test")
    schema = "source_staging_mobile_" + "b" * 32
    monkeypatch.setenv("QUIZ_ENABLE_SYNTHETIC_MANUAL_FIXTURE", "0")
    assert not allowed(FakeConnection(schema))
    monkeypatch.setenv("QUIZ_ENABLE_SYNTHETIC_MANUAL_FIXTURE", "1")
    for env in ("qa", "production", "prod"):
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", env)
        assert not allowed(FakeConnection(schema))
