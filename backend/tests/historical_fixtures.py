"""Synthetic old records for preservation tests only. Not an application seed command."""

from psycopg.types.json import Jsonb


def seed_legacy(conn):
    for subject in ("biochemistry", "cell_biology"):
        topic = conn.execute(
            "INSERT INTO topics(subject,name) VALUES (%s,%s) ON CONFLICT(subject,name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            (subject, "Synthetic historical chapter"),
        ).fetchone()
        tid = topic["id"] if isinstance(topic, dict) else topic[0]
        for i in range(36):
            conn.execute(
                "INSERT INTO questions(external_id,topic_id,stem,options,correct_answer,explanation,difficulty,source) VALUES (%s,%s,%s,%s,'A','Synthetic historical explanation',3,'Synthetic test only') ON CONFLICT(external_id) DO NOTHING",
                (
                    f"test-legacy:{subject}:{i}",
                    tid,
                    f"Synthetic historical question {subject} {i}",
                    Jsonb(dict(zip("ABCD", ["alpha", "beta", "gamma", "delta"]))),
                ),
            )
