"""Transactional, versioned additive migrations. No demo seeding entry points."""

import argparse
import hashlib
import os
from pathlib import Path

import psycopg


SQL_DIR = Path(__file__).parent / "sql"


def apply_migrations(conn, sql_dir=SQL_DIR):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(280003)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
        )
        for path in sorted(sql_dir.glob("*.sql")):
            source = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(source.encode()).hexdigest()
            row = conn.execute(
                "SELECT sha256 FROM schema_migrations WHERE name=%s", (path.name,)
            ).fetchone()
            if row:
                if (row["sha256"] if isinstance(row, dict) else row[0]) != digest:
                    raise RuntimeError(f"Applied migration changed: {path.name}")
                continue
            # Historical files contain standalone transaction markers. The runner
            # owns the transaction, so all migrations succeed or roll back together.
            sql = "\n".join(
                line
                for line in source.splitlines()
                if line.strip().upper() not in {"BEGIN;", "COMMIT;"}
            )
            conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations(name,sha256) VALUES (%s,%s)",
                (path.name, digest),
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    with psycopg.connect(url) as conn:
        apply_migrations(conn)
    print("Additive migrations complete; existing papers, answers and grades preserved")


if __name__ == "__main__":
    main()
