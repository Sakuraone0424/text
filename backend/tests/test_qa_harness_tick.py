"""Installing the real SDK is not permission to spend or leak private material."""
from app.qa_harness_tick import plan, SDK_VERSION


def env():
    return {
        "RAILWAY_ENVIRONMENT_NAME": "qa",
        "QA_IMPORT_SCHEMA": "source_staging_ocr_20261030",
        "QUIZ_DB_SCHEMA": "source_staging_ocr_20261030",
        "QA_IMPORT_ENABLED": "0",
        "SOURCE_STAGING_DATABASE_URL": "postgresql://unused-test-only",
        "DEEPSEEK_API_AUTHORIZED": "1",
        "DEEPSEEK_PRIVATE_DATA_AUTHORIZED": "1",
        "DEEPSEEK_API_KEY": "canary_secret_that_must_never_be_logged",
        "AI_AUTOMATION_ENABLED": "1",
        "QA_HARNESS_EXECUTION_ENABLED": "1",
        "QA_HARNESS_SCOPE_KIND": "review_campaign",
        "DEEPSEEK_REVIEW_SCOPE_ID": "a" * 64,
    }


def test_install_only_is_parked_and_contains_no_private_secrets():
    base = env()
    del base["DEEPSEEK_PRIVATE_DATA_AUTHORIZED"]
    report = plan(base, SDK_VERSION)
    assert not report["ready_to_execute_one_budgeted_campaign_job"]
    assert report["model_calls_without_all_gates"] == 0
    assert "canary_secret" not in str(report)
    assert report["source_text_logged"] is False


def test_complete_explicit_authorization_only_opens_preflight_not_api_call():
    result = plan(env(), SDK_VERSION)
    assert result["ready_to_execute_one_budgeted_campaign_job"]
    assert result["finite_approved_campaign_scope_present"]
    assert result["status"] == "authorized_scoped_execution_possible"
    assert "a" * 64 not in str(result)
    assert "postgresql" not in str(result)
    assert result["model_calls_without_all_gates"] == 0


def test_worker_preflight_rejects_wrong_sdk_or_cross_schema_and_production():
    base = env()
    assert not plan(base, "0.2.1")["ready_to_execute_one_budgeted_campaign_job"]
    base["QUIZ_DB_SCHEMA"] = "source_staging_other"
    assert not plan(base, SDK_VERSION)["ready_to_execute_one_budgeted_campaign_job"]
    base["QUIZ_DB_SCHEMA"] = "source_staging_ocr_20261030"
    base["RAILWAY_ENVIRONMENT_NAME"] = "production"
    assert not plan(base, SDK_VERSION)["ready_to_execute_one_budgeted_campaign_job"]


def test_worker_cannot_start_without_bounded_campaign_identity_or_explicit_switch():
    base = env()
    for key, value in [
        ("QA_HARNESS_EXECUTION_ENABLED", "0"),
        ("DEEPSEEK_REVIEW_SCOPE_ID", ""),
        ("DEEPSEEK_REVIEW_SCOPE_ID", "xyz"),
        ("QA_HARNESS_SCOPE_KIND", "unlimited"),
        ("AI_AUTOMATION_ENABLED", "0"),
        ("DEEPSEEK_API_AUTHORIZED", "0"),
        ("QA_IMPORT_ENABLED", "1"),
        ("DEEPSEEK_API_KEY", ""),
    ]:
        candidate = dict(base, **{key: value})
        assert not plan(candidate, SDK_VERSION)["ready_to_execute_one_budgeted_campaign_job"]


def test_unapproved_trial_has_independent_scope_not_campaign_fallback():
    base = env()
    base["QA_HARNESS_SCOPE_KIND"] = "limited_trial"
    assert not plan(base, SDK_VERSION)["ready_to_execute_one_budgeted_campaign_job"]
    base["DEEPSEEK_TRIAL_SCOPE_ID"] = "b" * 64
    assert plan(base, SDK_VERSION)["ready_to_execute_one_budgeted_campaign_job"]
