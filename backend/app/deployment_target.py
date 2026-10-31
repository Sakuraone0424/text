"""Pinned Railway target and separately approved PostgreSQL identity; fail closed."""

import os
from uuid import UUID
from psycopg.conninfo import conninfo_to_dict

TARGET = {
    "PROJECT": "c6028313-e1f4-43cc-a63d-b7ff474a3a24",
    "ENVIRONMENT": "ca9c95e7-81b8-495e-a51b-f01607a8eeab",
    "SERVICE": "15083462-a2f4-49d0-9a72-0873208e0669",
}
QA_TARGET = {
    "PROJECT": "0ff7ff18-e746-42db-8eb2-b515306fa473",
    "ENVIRONMENT": "246e3b1c-811b-4e02-ba42-89cb3e19f2cb",
    "SERVICE": "b2cbe2fd-cc60-49ec-b52f-68f5fd35aa07",
}
QA_SOURCE_SCHEMA = "source_staging_ocr_20261030"

PROTECTED_PROJECT = "c8ace6c2-e725-4e19-b064-3c71b44d4fde"
PROTECTED_ENVIRONMENT = "54a1a3e3-9a24-4182-8441-5670d1ba9f04"
SOURCE_SCHEMA = "source_staging_real_20261026"


def on_railway():
    return os.environ.get("QUIZ_REQUIRE_RAILWAY_IDENTITY") == "1" or any(
        os.environ.get("RAILWAY_" + key)
        for key in (
            "PROJECT_ID",
            "PROJECT_NAME",
            "ENVIRONMENT_ID",
            "ENVIRONMENT_NAME",
            "SERVICE_ID",
            "DEPLOYMENT_ID",
        )
    )


def verify_isolated_target():
    if not on_railway():
        return
    if (
        os.environ.get("RAILWAY_PROJECT_ID") == PROTECTED_PROJECT
        or os.environ.get("RAILWAY_ENVIRONMENT_ID") == PROTECTED_ENVIRONMENT
        or os.environ.get("RAILWAY_PROJECT_NAME") == "xiao-ustc-quiz"
    ):
        raise PermissionError("Original project is protected")
    if os.environ.get("QUIZ_ISOLATED_TARGET_APPROVED") != "1":
        raise PermissionError("Verified isolated Railway target approval required")
    qa = os.environ.get("QUIZ_DEPLOYMENT_PROFILE") == "ustc-exam-ocr-qa"
    selected = QA_TARGET if qa else TARGET
    if qa and os.environ.get("RAILWAY_ENVIRONMENT_NAME") != "qa":
        raise PermissionError("QA deployment requires exact qa environment")
    if qa and os.environ.get("QUIZ_DAILY_SPECIFICATION") != "xiaohan-60q-150pt-v2":
        raise PermissionError("QA training requires 60-question specification")
    for kind, pinned in selected.items():
        actual = os.environ.get(f"RAILWAY_{kind}_ID", "")
        approved = os.environ.get(f"QUIZ_APPROVED_RAILWAY_{kind}_ID", "")
        try:
            if str(UUID(actual)) != pinned or str(UUID(approved)) != pinned:
                raise ValueError()
        except ValueError:
            raise PermissionError("Railway target identity mismatch: " + kind)
    if os.environ.get("QUIZ_DB_SCHEMA") != (QA_SOURCE_SCHEMA if qa else SOURCE_SCHEMA):
        raise PermissionError("Exact approved source schema required")


def verify_database_url(url):
    """Before connection: expected endpoint comes from independently checked target binding.

    Never logs or returns passwords/URLs. A matching display/database name is insufficient.
    """
    try:
        info = conninfo_to_dict(url)
    except Exception:
        raise PermissionError("Invalid database connection configuration") from None
    if any(info.get(k) for k in ("hostaddr", "service", "options")):
        raise PermissionError("Alternate database routing is not admitted")
    for field, key in [
        ("host", "HOST"),
        ("port", "PORT"),
        ("dbname", "NAME"),
        ("user", "USER"),
    ]:
        actual = info.get(field, "5432" if field == "port" else "")
        expected = os.environ.get("QUIZ_APPROVED_DB_" + key, "")
        if (
            not expected
            or actual != expected
            or (field == "host" and ("," in actual or actual.startswith("/")))
        ):
            raise PermissionError("Database endpoint identity mismatch: " + key)
    if (
        not info.get("host", "").endswith(".railway.internal")
        and info.get("sslmode") != "verify-full"
    ):
        raise PermissionError(
            "Public database transport requires verified TLS; otherwise use Railway private network"
        )
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", info.get("dbname", "")):
        raise PermissionError("Database name not admitted for restore tools")
    if not os.environ.get("QUIZ_APPROVED_DB_SYSTEM_ID", "").isdigit():
        raise PermissionError("Approved PostgreSQL system identity required")


def verify_database_connection(conn):
    """Read-only independent cluster/database/role verification before application writes.

    Target operator grants only EXECUTE on pg_control_system to the application role.
    """
    try:
        row = conn.execute(
            "SELECT current_database() AS name,session_user AS role,(SELECT system_identifier::text FROM pg_control_system()) AS system_id"
        ).fetchone()
        if (
            row["name"] != os.environ.get("QUIZ_APPROVED_DB_NAME")
            or row["role"] != os.environ.get("QUIZ_APPROVED_DB_USER")
            or row["system_id"] != os.environ.get("QUIZ_APPROVED_DB_SYSTEM_ID")
        ):
            raise PermissionError("Database cluster identity mismatch")
        if row["system_id"] in os.environ.get("QUIZ_PROTECTED_DB_SYSTEM_IDS", "").split(
            ","
        ):
            raise PermissionError("Protected PostgreSQL cluster")
    except PermissionError:
        raise
    except Exception:
        raise PermissionError("Cannot verify PostgreSQL cluster identity") from None
