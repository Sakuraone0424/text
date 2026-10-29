"""Paid model capability must be blocked without third-party source consent."""
from app.qa_ai_authorization_check import inspect


def test_railway_only_and_budget_approval_are_not_private_excerpts_consent():
    env = {
        "RAILWAY_ENVIRONMENT_NAME":"qa",
        "QA_IMPORT_SCHEMA":"source_staging_ocr_20261030",
        "QA_IMPORT_ENABLED":"0",
        "DEEPSEEK_API_AUTHORIZED":"1",
        "DEEPSEEK_API_KEY":"test_secret_never_log",
        "AI_AUTOMATION_ENABLED":"1",
        "QUIZ_DB_SCHEMA":"source_staging_ocr_20261030",
    }
    data=inspect(env)
    assert not data["ready_for_safe_model_worker_start"]
    assert not data["preflight"]["third_party_private_source_excerpt_transfer_authorized"]
    assert data["real_model_calls_made_by_this_probe"]==0
    assert "test_secret_never_log" not in str(data)
    assert not data["qualified_120_bank_can_be_claimed"]


def test_all_explicit_guards_are_required_and_do_not_start_any_jobs():
    env={
        "RAILWAY_ENVIRONMENT_NAME":"qa",
        "QA_IMPORT_SCHEMA":"source_staging_ocr_20261030",
        "QA_IMPORT_ENABLED":"0",
        "DEEPSEEK_API_AUTHORIZED":"1",
        "DEEPSEEK_PRIVATE_DATA_AUTHORIZED":"1",
        "DEEPSEEK_API_KEY":"not_a_real_credential",
        "AI_AUTOMATION_ENABLED":"1",
        "QUIZ_DB_SCHEMA":"source_staging_ocr_20261030",
    }
    data=inspect(env)
    assert data["ready_for_safe_model_worker_start"]
    assert data["real_model_calls_made_by_this_probe"]==0
    assert data["independent_science_reviews_completed"]==0
    assert data["independent_answer_reviews_completed"]==0
    assert not data["qualified_120_bank_can_be_claimed"]
    env["RAILWAY_ENVIRONMENT_NAME"]="production"
    assert not inspect(env)["ready_for_safe_model_worker_start"]
