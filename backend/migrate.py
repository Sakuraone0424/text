import os
from pathlib import Path

import psycopg

from app.seed_data import QUESTIONS, SEED_SOURCE

url = os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError("DATABASE_URL is not configured")

sql_dir = Path(__file__).parent / "sql"
with psycopg.connect(url, autocommit=True) as conn:
    for sql_file in sorted(sql_dir.glob("*.sql")):
        conn.execute(sql_file.read_text(encoding="utf-8"))

    for external_id, subject, topic, stem, a, b, c, d, answer, explanation in QUESTIONS:
        topic_row = conn.execute(
            """
            INSERT INTO topics (subject, name)
            VALUES (%s, %s)
            ON CONFLICT (subject, name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (subject, topic),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO questions
                (external_id, topic_id, stem, options, correct_answer, explanation, difficulty, source)
            VALUES
                (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            ON CONFLICT (external_id) DO UPDATE SET
                topic_id = EXCLUDED.topic_id,
                stem = EXCLUDED.stem,
                options = EXCLUDED.options,
                correct_answer = EXCLUDED.correct_answer,
                explanation = EXCLUDED.explanation,
                difficulty = EXCLUDED.difficulty,
                source = EXCLUDED.source
            """,
            (
                external_id,
                topic_row[0],
                stem,
                psycopg.types.json.Jsonb({"A": a, "B": b, "C": c, "D": d}),
                answer,
                explanation,
                3,
                SEED_SOURCE,
            ),
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                'users', 'topics', 'questions', 'papers',
                'paper_questions', 'attempts', 'answers', 'review_schedule'
              )
            """
        )
        table_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT t.subject, COUNT(*)
            FROM questions q
            JOIN topics t ON t.id = q.topic_id
            GROUP BY t.subject
            """
        )
        subject_counts = dict(cur.fetchall())

if table_count != 8:
    raise RuntimeError(f"Expected 8 tables, found {table_count}")
if subject_counts.get("biochemistry", 0) < 30 or subject_counts.get("cell_biology", 0) < 30:
    raise RuntimeError(f"Question bank too small: {subject_counts}")

print(f"Database migration successful: 8 tables verified; question bank {subject_counts}")
