"""Fail-closed QA preflight for billable independent AI science/answer rounds.

Crucial: authorization to spend on Railway resources is NOT authorization to
send privately owned textbook quotations to a third-party model provider.
Only report boolean readiness flags. Never log a key or launch paid requests.
"""
import json
import os
from app.source_intake import SCHEMA


def inspect(env):
    checks = {
        "qa_environment_confirmed": env.get("RAILWAY_ENVIRONMENT_NAME","").lower()=="qa",
        "qa_isolated_schema_selected": bool(SCHEMA.fullmatch(env.get("QA_IMPORT_SCHEMA", ""))),
        "qa_source_upload_disabled": env.get("QA_IMPORT_ENABLED")=="0",
        "model_api_authorized_by_owner": env.get("DEEPSEEK_API_AUTHORIZED")=="1",
        "third_party_private_source_excerpt_transfer_authorized": (
            env.get("DEEPSEEK_PRIVATE_DATA_AUTHORIZED")=="1"
        ),
        "model_provider_api_secret_present": bool(env.get("DEEPSEEK_API_KEY")),
        "paid_automation_explicitly_enabled": env.get("AI_AUTOMATION_ENABLED")=="1",
        "isolated_worker_target_configured": bool(
            SCHEMA.fullmatch(env.get("QUIZ_DB_SCHEMA", ""))
        ),
        "worker_uses_exact_QA_source_schema": bool(
            SCHEMA.fullmatch(env.get("QA_IMPORT_SCHEMA", ""))
            and env.get("QUIZ_DB_SCHEMA") == env.get("QA_IMPORT_SCHEMA")
        ),
    }
    # Neither metadata presence nor consent represents a successful model call,
    # and this service intentionally never initiates an AI job.
    return {
        "status":"independent_AI_review_not_started",
        "preflight":checks,
        "ready_for_safe_model_worker_start":all(checks.values()),
        "real_model_calls_made_by_this_probe":0,
        "independent_science_reviews_completed":0,
        "independent_answer_reviews_completed":0,
        "qualified_120_bank_can_be_claimed":False,
        "no_secret_or_private_passages_logged":True,
    }


def main():
    result=inspect(os.environ)
    print("QA_AI_AUTOMATION_PRIVACY_GATE="+json.dumps(
        result,sort_keys=True,separators=(",",":")
    ),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
