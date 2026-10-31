"""Read-only identity discovery before binding the independent QA training API.

Outputs only cluster coordinates and aggregate counts, never DSNs or content.
No migrations, user records or provider calls are possible in this entry point.
"""
import json
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg import sql

from app.deployment_target import QA_TARGET, QA_SOURCE_SCHEMA


def inspect():
    for kind, expected in QA_TARGET.items():
        if os.environ.get(f"RAILWAY_{kind}_ID") != expected:
            raise PermissionError("qa_probe_platform_identity_mismatch")
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME") != "qa":
        raise PermissionError("qa_probe_environment_mismatch")
    url = os.environ["DATABASE_URL"]
    info = conninfo_to_dict(url)
    if (info.get("host") != os.environ.get("QA_EXPECTED_PRIVATE_DB_HOST")
            or not info.get("host", "").endswith(".railway.internal")
            or any(info.get(k) for k in ("hostaddr", "service", "options"))):
        raise PermissionError("qa_probe_private_endpoint_mismatch")
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='30s'")
        row = conn.execute(
            "SELECT current_database() AS name,session_user AS role,"
            "(SELECT system_identifier::text FROM pg_control_system()) AS system_id"
        ).fetchone()
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(
            sql.Identifier(QA_SOURCE_SCHEMA)))
        row["source_documents"] = conn.execute(
            "SELECT count(*) AS n FROM source_documents").fetchone()["n"]
        row["source_units"] = conn.execute(
            "SELECT count(*) AS n FROM source_units").fetchone()["n"]
        row["migrations"] = conn.execute(
            "SELECT count(*) AS n FROM schema_migrations").fetchone()["n"]
    return {"read_only": True, "host": info["host"],
            "port": info.get("port", "5432"), "schema": QA_SOURCE_SCHEMA, **row}


if __name__ == "__main__":
    try:
        print("QA_TRAINING_IDENTITY=" + json.dumps(inspect(), sort_keys=True), flush=True)
    except Exception as exc:
        print("QA_TRAINING_PROBE_FAILED=" + type(exc).__name__, flush=True)
        raise SystemExit(1)
