"""Real PostgreSQL migrations, using rollback-only schema isolation."""

import shutil
import pytest
import psycopg
from migrate import SQL_DIR, apply_migrations
from historical_fixtures import seed_legacy

TABLES = (
    "users",
    "topics",
    "questions",
    "papers",
    "paper_questions",
    "attempts",
    "answers",
    "review_schedule",
)


def snapshot(conn):
    return {
        table: sorted(
            (
                str(r["data"])
                for r in conn.execute(
                    f"SELECT to_jsonb(t) AS data FROM {table} t"
                ).fetchall()
            )
        )
        for table in TABLES
    }


def historical_database(conn):
    for name in ["001_initial.sql", "002_adaptive_quiz.sql"]:
        text = (SQL_DIR / name).read_text()
        conn.execute(
            "\n".join(
                l
                for l in text.splitlines()
                if l.strip().upper() not in {"BEGIN;", "COMMIT;"}
            )
        )
    seed_legacy(conn)
    uid = conn.execute(
        "INSERT INTO users(username) VALUES ('historical-owner') RETURNING id"
    ).fetchone()["id"]
    pid = conn.execute(
        "INSERT INTO papers(user_id,paper_date) VALUES (%s,'2025-01-02') RETURNING id",
        (uid,),
    ).fetchone()["id"]
    questions = conn.execute(
        "SELECT q.id FROM questions q JOIN topics t ON q.topic_id=t.id ORDER BY t.subject,q.id"
    ).fetchall()
    for pos, row in enumerate(questions[:60], 1):
        conn.execute(
            "INSERT INTO paper_questions VALUES (%s,%s,%s)", (pid, row["id"], pos)
        )
    aid = conn.execute(
        "INSERT INTO attempts(paper_id,user_id,score) VALUES (%s,%s,41) RETURNING id",
        (pid, uid),
    ).fetchone()["id"]
    for index, row in enumerate(questions[:60]):
        conn.execute(
            "INSERT INTO answers VALUES (%s,%s,%s,%s)",
            (aid, row["id"], "A", index < 41),
        )
    conn.execute(
        "INSERT INTO review_schedule(user_id,question_id,review_stage,due_date) VALUES (%s,%s,1,'2025-01-03')",
        (uid, questions[-1]["id"]),
    )
    # A user-edited old answer must survive even opt-in seeding.
    conn.execute(
        "UPDATE questions SET correct_answer='D', explanation='historical user edit' WHERE id=%s",
        (questions[0]["id"],),
    )
    return uid, pid


def test_unversioned_upgrade_and_repeat_preserve_all_historical_rows(raw_db):
    uid, pid = historical_database(raw_db)
    before = snapshot(raw_db)
    apply_migrations(raw_db)
    assert snapshot(raw_db) == before
    apply_migrations(raw_db)
    assert snapshot(raw_db) == before
    seed_legacy(raw_db)
    assert snapshot(raw_db) == before
    from app.legacy_history import load_paper, history

    assert load_paper(raw_db, uid, pid)["score"] == 41
    assert history(raw_db, uid)[0]["score"] == 41
    assert (
        raw_db.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
        == 11
    )
    assert (
        raw_db.execute("SELECT COUNT(*) AS n FROM reviewed_questions").fetchone()["n"]
        == 0
    )
    assert (
        raw_db.execute(
            "SELECT COUNT(*) AS n FROM legacy_question_publications"
        ).fetchone()["n"]
        == 0
    )


def test_failed_migration_rolls_back_ddl_and_keeps_history(raw_db, tmp_path):
    historical_database(raw_db)
    before = snapshot(raw_db)
    for path in SQL_DIR.glob("*.sql"):
        shutil.copy(path, tmp_path / path.name)
    (tmp_path / "999_failure.sql").write_text(
        "CREATE TABLE should_not_exist(id int); SELECT 1/0;"
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        apply_migrations(raw_db, tmp_path)
    assert snapshot(raw_db) == before
    assert (
        raw_db.execute("SELECT to_regclass('should_not_exist') AS t").fetchone()["t"]
        is None
    )
    assert (
        raw_db.execute("SELECT to_regclass('schema_migrations') AS t").fetchone()["t"]
        is None
    )
    apply_migrations(raw_db)
    assert snapshot(raw_db) == before


def test_applied_migration_checksum_enforced(db, tmp_path):
    for path in SQL_DIR.glob("*.sql"):
        shutil.copy(path, tmp_path / path.name)
    (tmp_path / "001_initial.sql").write_text(
        "-- tampered\n" + (tmp_path / "001_initial.sql").read_text()
    )
    with pytest.raises(RuntimeError, match="Applied migration changed"):
        apply_migrations(db, tmp_path)
    assert (
        db.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"] == 11
    )


def test_upgrade_preserves_existing_v2_answers_and_snapshots(raw_db):
    from psycopg.types.json import Jsonb

    historical_database(raw_db)
    raw_db.execute((SQL_DIR / "003_reviewed_quiz.sql").read_text())
    content = {
        "max_points": 2,
        "stem": "Historical V2 stem",
        "subject": "biochemistry",
        "question_type": "single_choice",
        "answer": "A",
        "rubric": [{"description": "Legacy scoring", "points": 2}],
        "evidence": [{"chapter": "Legacy"}],
    }
    qid = raw_db.execute(
        "INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id",
        (Jsonb(content),),
    ).fetchone()["id"]
    raw_db.execute(
        "INSERT INTO question_audits(question_id,reviewer,content_snapshot,checks,issues,duplicate_metrics,passed) VALUES (%s,'legacy',%s,'{}','[]','{}',true)",
        (qid, Jsonb(content)),
    )
    raw_db.execute(
        "UPDATE reviewed_questions SET status='published' WHERE id=%s", (qid,)
    )
    uid = raw_db.execute("SELECT id FROM users LIMIT 1").fetchone()["id"]
    pid = raw_db.execute(
        "INSERT INTO papers_v2(user_id,paper_date,blueprint) VALUES (%s,'2025-01-03','{\"historical\":true}') RETURNING id",
        (uid,),
    ).fetchone()["id"]
    raw_db.execute(
        "INSERT INTO paper_items_v2 VALUES (%s,%s,1,%s)", (pid, qid, Jsonb(content))
    )
    aid = raw_db.execute(
        "INSERT INTO attempts_v2(paper_id,finalized_at) VALUES (%s,CURRENT_TIMESTAMP) RETURNING id",
        (pid,),
    ).fetchone()["id"]
    raw_db.execute(
        "INSERT INTO answers_v2 VALUES (%s,%s,'original handwritten transcript',2,NULL,NULL)",
        (aid, qid),
    )
    before = raw_db.execute("SELECT to_jsonb(a) AS row FROM answers_v2 a").fetchone()[
        "row"
    ]
    apply_migrations(raw_db)
    apply_migrations(raw_db)
    assert (
        raw_db.execute("SELECT to_jsonb(a) AS row FROM answers_v2 a").fetchone()["row"]
        == before
    )
    assert (
        raw_db.execute("SELECT snapshot FROM paper_items_v2").fetchone()["snapshot"]
        == content
    )
    assert raw_db.execute("SELECT COUNT(*) AS n FROM fsrs_events").fetchone()["n"] == 0
