"""Opt-in 20:00 Asia/Shanghai preparation. Quality failures never mean success."""

import os
from psycopg.errors import QueryCanceled
from datetime import timedelta
from zoneinfo import ZoneInfo
from app.training_sessions import server_now
from app.question_quality import Blueprint
from app.training_spec import COUNTS, SUBJECTS
from app.quiz_v2 import create_paper, BankShortage


def prepare_daily(conn):
    if os.environ.get("QUIZ_DAILY_PREP_ENABLED") != "1":
        return 0
    now = server_now(conn).astimezone(ZoneInfo("Asia/Shanghai"))
    if now.hour < 20:
        return 0
    blueprint = Blueprint(
        specification="xiaohan-150-60-v1", quotas={s: COUNTS for s in SUBJECTS}
    )
    done = 0
    for user in conn.execute("SELECT id FROM users ORDER BY id LIMIT 100").fetchall():
        key = f"daily:{user['id']}:{now.date()}"
        conn.execute(
            "INSERT INTO automation_jobs(job_key,user_id,target_date,state) VALUES (%s,%s,%s,'pending') ON CONFLICT DO NOTHING",
            (key, user["id"], now.date()),
        )
        job = conn.execute(
            "SELECT * FROM automation_jobs WHERE job_key=%s FOR UPDATE SKIP LOCKED",
            (key,),
        ).fetchone()
        if (
            not job
            or job["state"] == "prepared"
            or job["next_retry"] > server_now(conn)
        ):
            continue
        try:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout='30s'")
                paper = create_paper(conn, user["id"], blueprint, now.date())
            conn.execute(
                "UPDATE automation_jobs SET state='prepared',paper_id=%s,attempts=attempts+1,error=NULL WHERE job_key=%s",
                (paper["paper_id"], key),
            )
            done += 1
        except (BankShortage, ValueError, QueryCanceled):
            conn.execute(
                "UPDATE automation_jobs SET state='failed',attempts=attempts+1,next_retry=%s,error='insufficient_or_incompatible_verified_bank' WHERE job_key=%s",
                (server_now(conn) + timedelta(minutes=5), key),
            )
    return done
