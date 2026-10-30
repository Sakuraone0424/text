"""QA-only DeepSeek Harness cron tick, never a production API/worker alias.

The standalone image may be installed and scheduled before consent. Every tick
fails closed to a NO-CALL result unless all privacy, identity, expenditure and
finite model-campaign gates pass. It never reads legacy production databases.

No private textbook text, job prompt, model answer, token or URL is logged.
The standalone service must be configured by the operator in the isolated QA
Railway project, with a pinned scope and explicit per-task budget.
"""
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
import json
import os
import re

from app.qa_ai_authorization_check import inspect as authorization
from app.source_intake import SCHEMA, isolated_db
from app.source_readiness import audit as source_audit
from app.deployment_target import QA_TARGET, QA_SOURCE_SCHEMA

SDK_VERSION = "0.2.1rc2"
SDK_DISTRIBUTION = "deepseek-harness-sdk"
REVIEW_SCOPE = re.compile(r"[0-9a-f]{64}\Z")
ALLOWED_SCOPE_KINDS = frozenset(("limited_trial", "review_campaign"))
QA_HARNESS_SERVICE = "1ea7310e-f515-4721-b1f5-63f65d626f8a"


def pinned_worker(env):
    return (
        env.get("RAILWAY_PROJECT_ID") == QA_TARGET["PROJECT"]
        and env.get("RAILWAY_ENVIRONMENT_ID") == QA_TARGET["ENVIRONMENT"]
        and env.get("RAILWAY_SERVICE_ID") == QA_HARNESS_SERVICE
        and env.get("QA_IMPORT_SCHEMA") == QA_SOURCE_SCHEMA
        and env.get("QUIZ_DB_SCHEMA") == QA_SOURCE_SCHEMA
    )


def plan(env, installed_sdk_version=None):
    """Deterministic permission gate; never opens DB or invokes provider."""
    permission = authorization(env)
    checks = permission["preflight"]
    installed = installed_sdk_version == SDK_VERSION
    kind = env.get("QA_HARNESS_SCOPE_KIND", "")
    scoped_key = (
        "DEEPSEEK_REVIEW_SCOPE_ID"
        if kind == "review_campaign"
        else "DEEPSEEK_TRIAL_SCOPE_ID"
        if kind == "limited_trial"
        else None
    )
    scope = env.get(scoped_key, "") if scoped_key else ""
    execution = (
        installed
        and pinned_worker(env)
        and env.get("QA_HARNESS_EXECUTION_ENABLED") == "1"
        and permission["ready_for_safe_model_worker_start"]
        and kind in ALLOWED_SCOPE_KINDS
        and REVIEW_SCOPE.fullmatch(scope) is not None
        and bool(env.get("SOURCE_STAGING_DATABASE_URL"))
    )
    return {
        "status": "authorized_scoped_execution_possible" if execution
                  else "installed_or_configured_but_parked_no_provider_calls",
        "harness_sdk_installed_and_version_pinned": installed,
        "harness_sdk_expected_version": SDK_VERSION,
        "qa_environment_isolated": checks["qa_environment_confirmed"],
        "qa_worker_platform_identity_pinned": pinned_worker(env),
        "qa_source_schema_and_worker_schema_identical": checks[
            "worker_uses_exact_QA_source_schema"
        ],
        "owner_api_and_private_excerpt_authorization": (
            checks["model_api_authorized_by_owner"]
            and checks["third_party_private_source_excerpt_transfer_authorized"]
        ),
        "api_key_present_not_revealed": checks[
            "model_provider_api_secret_present"
        ],
        "operator_execution_switch_on": env.get(
            "QA_HARNESS_EXECUTION_ENABLED"
        ) == "1",
        "finite_approved_campaign_scope_present": (
            kind in ALLOWED_SCOPE_KINDS
            and REVIEW_SCOPE.fullmatch(scope) is not None
        ),
        "isolated_qa_database_dsn_present": bool(
            env.get("SOURCE_STAGING_DATABASE_URL")
        ),
        "ready_to_execute_one_budgeted_campaign_job": bool(execution),
        "model_calls_without_all_gates": 0,
        "source_text_logged": False,
    }


@contextmanager
def qa_db():
    from app.deployment_target import verify_database_url, verify_database_connection

    if not pinned_worker(os.environ):
        raise PermissionError("qa_harness_platform_identity_mismatch")
    schema = os.environ.get("QA_IMPORT_SCHEMA", "")
    if not SCHEMA.fullmatch(schema):
        raise PermissionError("qa_harness_invalid_schema")
    verify_database_url(os.environ.get("SOURCE_STAGING_DATABASE_URL", ""))
    with isolated_db(schema, "staging") as conn:
        verify_database_connection(conn)
        yield conn


def execute_authorized_scope(env):
    """Only run previously owner-approved, already bound and budgeted jobs."""
    from app import ai_jobs
    from app.auto_worker import dispatch_completed
    from app.harness_gateway import execute

    kind = env["QA_HARNESS_SCOPE_KIND"]
    scope = env[
        "DEEPSEEK_REVIEW_SCOPE_ID"
        if kind == "review_campaign"
        else "DEEPSEEK_TRIAL_SCOPE_ID"
    ]
    with qa_db() as conn:
        audit = source_audit(conn)
        if not (
            audit["imported_files"] == 6
            and audit["integrity_passed_files"] == 6
            and audit["all_six_byte_integrity_checks_passed"] is True
        ):
            raise PermissionError("qa_original_six_sources_not_verified")
        # Dispatch only the approved finite campaign; no broad pending-job
        # scan, no opportunistic new task creation and no production writes.
        dispatch_completed(conn, scope_id=scope, scope_kind=kind)
    made_attempt = ai_jobs.run_once(
        qa_db, execute, scope_id=scope, scope_kind=kind
    )
    with qa_db() as conn:
        dispatch_completed(conn, scope_id=scope, scope_kind=kind)
    return {
        "status": "one_campaign_tick_processed",
        "budgeted_provider_job_claimed": bool(made_attempt),
        "private_content_logged": False,
        "scope_id_logged": False,
    }


def main():
    try:
        installed = version(SDK_DISTRIBUTION)
    except PackageNotFoundError:
        installed = None
    state = plan(os.environ, installed_sdk_version=installed)
    print(
        "QA_DEEPSEEK_HARNESS_PREFLIGHT="
        + json.dumps(state, sort_keys=True, separators=(",", ":")),
        flush=True,
    )
    if not state["ready_to_execute_one_budgeted_campaign_job"]:
        return 0  # Cron stays harmless until a finite consented campaign exists.
    result = execute_authorized_scope(os.environ)
    print(
        "QA_DEEPSEEK_HARNESS_TICK="
        + json.dumps(result, sort_keys=True, separators=(",", ":")),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Never print private requests, credentials, DB URLs or provider output.
        print("QA_DEEPSEEK_HARNESS_FAILED=" + type(error).__name__, flush=True)
        raise SystemExit(1)
