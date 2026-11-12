"""PostgreSQL database connection helper."""

import os
import re
import threading
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


def _isolated_schema(conn, verified=False):
    """The explicitly configured isolated schema (None when the connection's own search_path is used).
    `verified`: this connection's cluster identity was already checked when the pool opened it."""
    from app.deployment_target import on_railway, verify_database_connection

    if on_railway() and not verified:
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


# ---- connection pool ---------------------------------------------------------------------------------------------
# Opening a PostgreSQL connection (TCP, TLS, authentication) for every request, and a second one to look a session up, cost tens
# of milliseconds each time. On the deployed service connections are kept and reused; each one has its cluster identity checked
# ONCE, when the pool opens it. Everything the application sets on a connection is transaction-local (SET LOCAL, set_config(..., true))
# and the one session lock (the backup's) is released in a finally, so a returned connection carries nothing over.
_POOLS = {}
_POOL_LOCK = threading.Lock()


def pooling():
    """QUIZ_DB_POOL=1/0 decides; unset, the deployed service pools and local tools / tests connect per use."""
    from app.deployment_target import on_railway

    return os.environ.get("QUIZ_DB_POOL", "1" if on_railway() else "0") == "1"


def _configure(conn):
    from app.deployment_target import on_railway, verify_database_connection

    if on_railway():
        verify_database_connection(conn)
    conn.rollback()                      # (a new pooled connection must be handed over idle)


def _pool(database_url):
    pool = _POOLS.get(database_url)
    if pool is None:
        with _POOL_LOCK:
            pool = _POOLS.get(database_url)
            if pool is None:
                from psycopg_pool import ConnectionPool

                try:
                    size = max(1, min(int(os.environ.get("QUIZ_DB_POOL_SIZE", "8")), 32))
                except ValueError:
                    size = 8
                pool = ConnectionPool(
                    database_url, kwargs={"row_factory": dict_row}, min_size=1, max_size=size, timeout=15,
                    max_lifetime=1800, max_idle=300, configure=_configure, check=ConnectionPool.check_connection, open=True,
                )
                _POOLS[database_url] = pool
    return pool


def close_pools():
    """Close every pool (service shutdown, tests)."""
    with _POOL_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


def pool_stats():
    return {("pool%d" % n): p.get_stats() for n, p in enumerate(_POOLS.values())}


@contextmanager
def get_db():
    """Yield a transaction-scoped connection; commit on success, roll back on error."""
    database_url = _verified_database_url()
    if pooling():
        with _pool(database_url).connection() as conn:
            schema = _isolated_schema(conn, verified=True)
            if schema:
                conn.execute(
                    sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                )
            yield conn
        return
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
