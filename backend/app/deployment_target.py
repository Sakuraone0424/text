"""A display name is not an isolation boundary; admit only approved Railway UUIDs."""

import os
from uuid import UUID


def verify_isolated_target():
    railway = any(
        os.environ.get(k)
        for k in (
            "RAILWAY_PROJECT_ID",
            "RAILWAY_PROJECT_NAME",
            "RAILWAY_ENVIRONMENT_NAME",
        )
    )
    if not railway:
        return  # Existing explicitly selected local nonproduction workflow.
    if os.environ.get("RAILWAY_PROJECT_NAME") == "xiao-ustc-quiz":
        raise PermissionError("Original project is protected")
    if os.environ.get("QUIZ_ISOLATED_TARGET_APPROVED") != "1":
        raise PermissionError("Verified isolated Railway target approval required")
    for kind in ("PROJECT", "ENVIRONMENT", "SERVICE"):
        actual = os.environ.get(f"RAILWAY_{kind}_ID", "")
        approved = os.environ.get(f"QUIZ_APPROVED_RAILWAY_{kind}_ID", "")
        try:
            if str(UUID(actual)) != str(UUID(approved)):
                raise ValueError()
        except ValueError:
            raise PermissionError("Railway target identity mismatch: " + kind)
    if not os.environ.get("QUIZ_DB_SCHEMA", "").startswith("source_staging_"):
        raise PermissionError("Isolated source schema required")
