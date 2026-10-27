"""PostgreSQL database connection helper."""

import os
import re
from psycopg import sql
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict


@contextmanager
def get_db():
    """Yield a transaction-scoped connection; commit on success, roll back on error."""
    from app.deployment_target import (
        verify_isolated_target,
        on_railway,
        verify_database_url,
        verify_database_connection,
    )

    verify_isolated_target()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    if on_railway():
        verify_database_url(database_url)
    else:
        # Local tests/source work may use explicit loopback; a local CLI must not
        # silently write a remote production URL just because Railway vars are absent.
        try:
            local = conninfo_to_dict(database_url)
        except Exception:
            raise PermissionError("Invalid local database configuration") from None
        if local.get("host") not in ("127.0.0.1", "localhost", "::1") or any(
            local.get(k) for k in ("hostaddr", "service")
        ):
            raise PermissionError(
                "Remote database requires pinned Railway identity or approved recovery tool"
            )
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        if on_railway():
            verify_database_connection(conn)
        schema = os.environ.get("QUIZ_DB_SCHEMA")
        if schema:
            if not re.fullmatch(r"source_staging_[a-zA-Z0-9_]+", schema):
                raise ValueError("Explicit isolated schema required")
            if not conn.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)
            ).fetchone():
                raise ValueError(
                    "Isolated schema missing; restore explicitly, never create a parallel bank"
                )
            conn.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
            )
        yield conn
