"""Legacy synthetic reviewer path is ONLY for isolated rollback-only CI fixtures.

Never enabled for real QA, Railway or production. Not a user-facing review gate.
"""
import os


def allowed(conn):
    if (os.environ.get("QUIZ_ENABLE_SYNTHETIC_MANUAL_FIXTURE") != "1"
        or not os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").lower() in ("qa", "production", "prod")):
        return False
    try:
        row = conn.execute("SELECT current_schema() AS current_schema").fetchone()
        schema = row["current_schema"]
    except (TypeError, KeyError, AttributeError):
        return False
    return isinstance(schema, str) and (
        schema.startswith("quiz_test_") or schema.startswith("quiz_live_test_")
    )
