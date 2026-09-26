import os
from pathlib import Path

import psycopg

url = os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError("DATABASE_URL is not configured")

sql_file = Path(__file__).parent / "sql" / "001_initial.sql"
sql = sql_file.read_text(encoding="utf-8")

with psycopg.connect(url, autocommit=True) as conn:
    conn.execute(sql)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                'users', 'topics', 'questions', 'papers',
                'paper_questions', 'attempts',
                'answers', 'review_schedule'
              )
        """)
        count = cur.fetchone()[0]

if count != 8:
    raise RuntimeError(f"Expected 8 tables, found {count}")

print("Database migration successful: 8 tables verified")
