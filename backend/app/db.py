"""PostgreSQL database connection helper."""

import os
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
        yield conn
