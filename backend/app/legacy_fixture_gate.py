"""Legacy synthetic reviewer path is ONLY for isolated rollback-only CI fixtures.

Never enabled for real QA, Railway or production. Not a user-facing review gate.
"""
import os
import re
from psycopg.conninfo import conninfo_to_dict


_SYNTHETIC_MOBILE_SCHEMA = re.compile(r"source_staging_mobile_[0-9a-f]{32}\Z")


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
    if not isinstance(schema, str):
        return False
    if schema.startswith("quiz_test_") or schema.startswith("quiz_live_test_"):
        return True
    # Old mobile browser tests use a source_staging_mobile_<uuid> schema
    # because source_intake.isolated_db requires a source_staging_* prefix.
    # This narrowly retains the synthetic rollback test without admitting
    # arbitrary source_staging schemas or any real QA/production database.
    if not _SYNTHETIC_MOBILE_SCHEMA.fullmatch(schema):
        return False
    try:
        expected_database = conninfo_to_dict(os.environ["TEST_DATABASE_URL"]).get("dbname")
        actual_database = conn.execute("SELECT current_database() AS name").fetchone()["name"]
    except (KeyError, TypeError, AttributeError, ValueError):
        return False
    return expected_database == actual_database == "quiz_test"
