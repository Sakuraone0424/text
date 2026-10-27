"""PostgreSQL database connection helper."""

import os
import re
from psycopg import sql
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


@contextmanager
def get_db():
    """Yield a transaction-scoped connection; commit on success, roll back on error."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
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
