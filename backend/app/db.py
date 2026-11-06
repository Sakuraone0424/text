"""PostgreSQL database connection helper."""

import os
import re
from psycopg import sql
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict


def _verified_database_url():
    """The database URL, after the same identity checks every connection goes through."""
    from app.deployment_target import (
        verify_isolated_target,
        on_railway,
        verify_database_url,
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
    return database_url


def _isolated_schema(conn):
    """The explicitly configured isolated schema (None when the connection's own search_path is used)."""
    from app.deployment_target import on_railway, verify_database_connection

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
    return schema


@contextmanager
def get_db():
    """Yield a transaction-scoped connection; commit on success, roll back on error."""
    database_url = _verified_database_url()
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        schema = _isolated_schema(conn)
        if schema:
            conn.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
            )
        yield conn


def listen_connection():
    """A long-lived autocommit connection for LISTEN (the caller closes it). Same checks as get_db; returns (conn, schema)
    where schema is the schema this deployment's rows live in."""
    conn = psycopg.connect(_verified_database_url(), row_factory=dict_row, autocommit=True)
    try:
        schema = _isolated_schema(conn)
        if schema:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        return conn, conn.execute("SELECT current_schema() AS s").fetchone()["s"]
    except Exception:
        conn.close()
        raise
