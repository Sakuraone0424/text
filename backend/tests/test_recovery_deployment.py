"""No real Railway/restore. Synthetic receipts, real rollback DB negative checks."""

from contextlib import contextmanager
from hashlib import sha256
import json, zipfile
import pytest
from app import deployment_target as d, recovery_check as r, deployment_check as c


@pytest.fixture
def db_approval(monkeypatch):
    for key, value in dict(
        HOST="isolated.railway.internal",
        PORT="5432",
        NAME="railway",
        USER="limited_app",
        SYSTEM_ID="123456",
    ).items():
        monkeypatch.setenv("QUIZ_APPROVED_DB_" + key, value)
    return (
        "postgresql://limited_app:never-log-this@isolated.railway.internal:5432/railway"
    )


def test_database_endpoint_separate_from_project_guard(db_approval, monkeypatch):
    d.verify_database_url(db_approval)
    with pytest.raises(PermissionError):
        d.verify_database_url(
            db_approval.replace(
                "isolated.railway.internal", "original-production.internal"
            )
        )
    monkeypatch.delenv("QUIZ_APPROVED_DB_SYSTEM_ID")
    with pytest.raises(PermissionError):
        d.verify_database_url(db_approval)


@pytest.mark.parametrize(
    "override", ["hostaddr=1.2.3.4", "options=-csearch_path%3Dpublic"]
)
def test_alternate_connection_routes_rejected(db_approval, override):
    with pytest.raises(PermissionError):
        d.verify_database_url(db_approval + "?" + override)


def test_cluster_mismatch_fails_even_with_matching_database_name(db_approval):
    class Conn:
        def execute(self, _):
            return self

        def fetchone(self):
            return dict(name="railway", role="limited_app", system_id="654321")

    with pytest.raises(PermissionError, match="cluster"):
        d.verify_database_connection(Conn())


def test_independent_cluster_identity_read_real_postgres(raw_db, monkeypatch):
    row = raw_db.execute(
        "SELECT current_database() AS name,session_user AS role,(SELECT system_identifier::text FROM pg_control_system()) AS id"
    ).fetchone()
    for key, value in [
        ("NAME", row["name"]),
        ("USER", row["role"]),
        ("SYSTEM_ID", row["id"]),
    ]:
        monkeypatch.setenv("QUIZ_APPROVED_DB_" + key, value)
    d.verify_database_connection(raw_db)
    monkeypatch.setenv("QUIZ_PROTECTED_DB_SYSTEM_IDS", row["id"])
    with pytest.raises(PermissionError, match="Protected"):
        d.verify_database_connection(raw_db)


def test_restore_requires_empty_database_not_just_empty_schema(raw_db):
    raw_db.execute("CREATE TABLE synthetic_existing_grade(id int)")
    raw_db.execute("INSERT INTO synthetic_existing_grade VALUES(41)")
    with pytest.raises(ValueError, match="not_empty"):
        r.require_empty(raw_db)
    assert (
        raw_db.execute("SELECT id FROM synthetic_existing_grade").fetchone()["id"] == 41
    )


def test_private_transfer_and_restore_authorizations_separate(monkeypatch):
    monkeypatch.setenv("RECOVERY_WRITE_AUTHORIZED", "1")
    monkeypatch.delenv("RECOVERY_PRIVATE_TRANSFER_AUTHORIZED", raising=False)
    with pytest.raises(PermissionError):
        r.restore("/not-read", {}, "/not-executed")


def test_target_read_authorization_before_connection(monkeypatch):
    monkeypatch.delenv("RECOVERY_READ_AUTHORIZED", raising=False)
    with pytest.raises(PermissionError):
        with r.target_connection():
            pytest.fail("No connection allowed")


def test_package_sha_and_no_unexpected_credentials(tmp_path):
    p = tmp_path / "synthetic.zip"
    manifest = dict(schema=d.SOURCE_SCHEMA, tables={}, migrations=[])
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("database.dump", b"SYNTHETIC NOT A REAL DUMP")
        z.writestr("database-manifest.json", json.dumps(manifest))
        z.writestr("RESTORE.txt", "Synthetic fixture")
    assert r.package(p, sha256(p.read_bytes()).hexdigest()) == manifest
    with pytest.raises(ValueError, match="sha"):
        r.package(p, "0" * 64)
    with zipfile.ZipFile(p, "a") as z:
        z.writestr("secret.env", "SYNTHETIC")
    with pytest.raises(ValueError, match="members"):
        r.package(p, sha256(p.read_bytes()).hexdigest())


def test_restore_uses_target_database_atomic_command_and_separate_readback(
    tmp_path, monkeypatch, db_approval
):
    payload = b"SYNTHETIC DUMP, NO REAL RESTORE"
    p = tmp_path / "fake.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("database.dump", payload)
    for key in ["RECOVERY_WRITE_AUTHORIZED", "RECOVERY_PRIVATE_TRANSFER_AUTHORIZED"]:
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("RECOVERY_DATABASE_URL", db_approval)
    monkeypatch.setenv("PGOPTIONS", "DO NOT INHERIT")
    connections = []

    @contextmanager
    def target():
        connections.append(object())
        yield connections[-1]

    monkeypatch.setattr(r, "target_connection", target)
    monkeypatch.setattr(r, "require_empty", lambda conn: None)
    monkeypatch.setattr(
        r,
        "verify_restored",
        lambda conn, m: dict(independent_readback=len(connections) == 2),
    )
    commands = []

    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        return r.subprocess.CompletedProcess(
            argv, 0, stdout="pg_restore (PostgreSQL) 17.11"
        )

    monkeypatch.setattr(r.subprocess, "run", run)
    result = r.restore(
        p,
        dict(dump_sha256=sha256(payload).hexdigest(), dump_bytes=len(payload)),
        "/synthetic-pg17",
    )
    argv, kw = commands[-1]
    assert "--dbname" in argv and argv[argv.index("--dbname") + 1] == "railway"
    assert "--single-transaction" in argv and "--exit-on-error" in argv
    assert all(x not in argv for x in ["--clean", "--create", "never-log-this"])
    assert kw["env"]["PGPASSWORD"] == "never-log-this" and "PGOPTIONS" not in kw["env"]
    assert result["independent_readback"]


def receipt():
    return {
        **{k.lower() + "_id": v for k, v in d.TARGET.items()},
        "autodeploy_enabled": False,
        "pr_previews_enabled": False,
    }


def test_push_requires_actual_trigger_receipt_not_patch_staged_label():
    x = receipt()
    assert c.check(x, "a" * 40)["ready"]
    x["autodeploy_enabled"] = None
    assert not c.check(x, "a" * 40)["ready"]


@pytest.mark.parametrize("amount", [None, "5.01", "NaN"])
def test_deploy_cannot_pass_unknown_or_excess_workspace_cost(amount):
    x = receipt()
    x.update(workspace_usage_complete=True, workspace_projected_usd=amount)
    result = c.check(x, "a" * 40, "deploy")
    assert not result["ready"]
    assert (
        "workspace_cost_unknown" if amount is None else "workspace_cost_outside_target"
    ) in result["issues"]


def test_no_green_health_without_database_in_railway_profile(client, monkeypatch):
    monkeypatch.setenv("QUIZ_REQUIRE_RAILWAY_IDENTITY", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Force the real missing-DB guard; client fixture otherwise substitutes a populated test DB.
    from app import main
    from app.db import get_db

    monkeypatch.setattr(main, "get_db", get_db)
    assert client.get("/health").status_code == 503


def test_function_only_database_is_not_empty(raw_db):
    raw_db.execute(
        "CREATE FUNCTION preexisting_user_function() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$"
    )
    schema = raw_db.execute("SELECT current_schema() AS name").fetchone()["name"]
    assert any(
        o["nspname"] == schema and o["relname"] == "preexisting_user_function"
        for o in r.non_system_objects(raw_db)
    )
    with pytest.raises(ValueError, match="not_empty"):
        r.require_empty(raw_db)


def test_custom_type_only_database_is_not_empty(raw_db):
    raw_db.execute("CREATE TYPE preexisting_answer_state AS ENUM ('saved','submitted')")
    schema = raw_db.execute("SELECT current_schema() AS name").fetchone()["name"]
    assert any(
        o["nspname"] == schema and o["relname"] == "preexisting_answer_state"
        for o in r.non_system_objects(raw_db)
    )
    with pytest.raises(ValueError, match="not_empty"):
        r.require_empty(raw_db)


def test_deploy_receipt_needs_matching_new_sha_and_still_never_mutates():
    x = receipt()
    head = "a" * 40
    x.update(
        github_sha=head,
        staged_sha=head,
        patch_status="STAGED",
        workspace_usage_complete=True,
        workspace_projected_usd="4.99",
        ai_flags={
            k: "0"
            for k in (
                "AI_AUTOMATION_ENABLED",
                "DEEPSEEK_API_AUTHORIZED",
                "DEEPSEEK_PRIVATE_DATA_AUTHORIZED",
            )
        },
        paid_start_authorized=True,
        private_transfer_authorized=True,
        restore_authorized=True,
        target_empty_verified=True,
        backup_handoff_confirmed=True,
    )
    result = c.check(x, head, "deploy")
    assert (
        result["ready"]
        and not result["live_railway_query"]
        and not result["mutation_executed"]
    )
    x["staged_sha"] = "b" * 40
    result = c.check(x, head, "deploy")
    assert not result["ready"] and "source_sha_not_synchronized" in result["issues"]


def test_local_migration_cannot_silently_connect_remote_production_url(monkeypatch):
    from app.db import get_db

    for name in [
        "QUIZ_REQUIRE_RAILWAY_IDENTITY",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_PROJECT_NAME",
        "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_SERVICE_ID",
        "RAILWAY_DEPLOYMENT_ID",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://never-used@production.invalid/old_grades"
    )
    with pytest.raises(PermissionError, match="Remote database"):
        with get_db():
            pytest.fail("No remote connection is permitted")


def test_public_database_transport_requires_verified_tls(db_approval, monkeypatch):
    monkeypatch.setenv("QUIZ_APPROVED_DB_HOST", "public-staging.example")
    url = db_approval.replace("isolated.railway.internal", "public-staging.example")
    with pytest.raises(PermissionError, match="verified TLS"):
        d.verify_database_url(url)
    d.verify_database_url(url + "?sslmode=verify-full")


def test_empty_webhooks_and_zero_deployments_do_not_prove_triggers_disabled():
    import json
    from pathlib import Path

    observed = json.loads((Path(__file__).resolve().parents[2] / "deploy/railway-automation/observed-user-receipt.json").read_text())
    assert observed["deployment_count"] == 0
    assert set(observed["webhook_counts"].values()) == {0}
    assert observed["ai_flags"] is None
    for phase in ("push", "deploy"):
        result = c.check(observed, "b9423ac92e55486bd026dc6d9ed94476132c5977", phase)
        assert not result["ready"]
        assert "autodeploy_enabled_not_confirmed_off" in result["issues"]
        assert "pr_previews_enabled_not_confirmed_off" in result["issues"]
