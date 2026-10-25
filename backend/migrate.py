"""Transactional, versioned additive migrations. Seeding is explicitly opt-in."""
import argparse
import hashlib
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from app.seed_data import QUESTIONS, SEED_SOURCE

SQL_DIR = Path(__file__).parent / 'sql'


def apply_migrations(conn, sql_dir=SQL_DIR):
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(280003)')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations '
                     '(name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())')
        for path in sorted(sql_dir.glob('*.sql')):
            source = path.read_text(encoding='utf-8')
            digest = hashlib.sha256(source.encode()).hexdigest()
            row = conn.execute('SELECT sha256 FROM schema_migrations WHERE name=%s', (path.name,)).fetchone()
            if row:
                if (row['sha256'] if isinstance(row, dict) else row[0]) != digest:
                    raise RuntimeError(f'Applied migration changed: {path.name}')
                continue
            # Historical files contain standalone transaction markers. The runner
            # owns the transaction, so all migrations succeed or roll back together.
            sql = '\n'.join(line for line in source.splitlines() if line.strip().upper() not in {'BEGIN;', 'COMMIT;'})
            conn.execute(sql)
            conn.execute('INSERT INTO schema_migrations(name,sha256) VALUES (%s,%s)', (path.name, digest))


def seed_legacy(conn):
    with conn.transaction():
        for external_id, subject, topic, stem, a, b, c, d, answer, explanation in QUESTIONS:
            topic_row = conn.execute(
                'INSERT INTO topics(subject,name) VALUES (%s,%s) ON CONFLICT(subject,name) DO UPDATE SET name=EXCLUDED.name RETURNING id',
                (subject, topic),
            ).fetchone()
            topic_id = topic_row['id'] if isinstance(topic_row, dict) else topic_row[0]
            # Never rewrite a question referenced by an existing paper/grade.
            conn.execute(
                'INSERT INTO questions(external_id,topic_id,stem,options,correct_answer,explanation,difficulty,source) '
                'VALUES (%s,%s,%s,%s,%s,%s,3,%s) ON CONFLICT(external_id) DO NOTHING',
                (external_id, topic_id, stem, Jsonb(dict(zip('ABCD', (a,b,c,d)))), answer, explanation, SEED_SOURCE),
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-legacy', action='store_true', help='Insert missing old demo questions, never overwrite existing questions')
    args = parser.parse_args()
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL is not configured')
    with psycopg.connect(url) as conn:
        apply_migrations(conn)
        if args.seed_legacy:
            seed_legacy(conn)
    print('Additive migrations complete; existing papers, answers and grades preserved')


if __name__ == '__main__':
    main()
