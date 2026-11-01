"""Opt-in 20:00 Asia/Shanghai preparation. Quality failures never mean success."""

import os
from psycopg.errors import QueryCanceled
from datetime import timedelta
from zoneinfo import ZoneInfo
from app.training_sessions import server_now
from app.question_quality import Blueprint
from app import training_spec_v2
from app.quiz_v2 import create_paper, BankShortage, PreparationPaused


def selected_daily_blueprint(env):
    """New daily papers always use the 60q/150pt v2 specification.

    An unset variable means v2. Any other value (including the retired 28q
    ID) fails closed rather than preparing an old exam. Historical 28q papers
    keep their own stored blueprint and are never re-planned here.
    """
    spec = env.get("QUIZ_DAILY_SPECIFICATION", "") or training_spec_v2.SPECIFICATION
    if spec == training_spec_v2.SPECIFICATION:
        return Blueprint.model_validate(training_spec_v2.default_blueprint())
    raise ValueError("unknown_daily_training_specification")


def verified_daily_paper_response(paper, blueprint):
    """A preexisting historical 28q paper must never appear as a 60q success."""
    if blueprint.specification != training_spec_v2.SPECIFICATION:
        return paper
    if (paper.get("mode") != "reviewed_60"
        or paper.get("blueprint", {}).get("specification") != training_spec_v2.SPECIFICATION
        or paper.get("count") != training_spec_v2.TOTAL_QUESTIONS
        or paper.get("max_points") != training_spec_v2.TOTAL_POINTS):
        raise ValueError("daily_60q_paper_not_proven")
    return paper


def prepare_daily(conn):
    if os.environ.get("QUIZ_DAILY_PREP_ENABLED") != "1":
        return 0
    now = server_now(conn).astimezone(ZoneInfo("Asia/Shanghai"))
    if now.hour < 20:
        return 0
    blueprint = selected_daily_blueprint(os.environ)
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
                paper = verified_daily_paper_response(
                    create_paper(conn, user["id"], blueprint, now.date()),
                    blueprint,
                )
            conn.execute(
                "UPDATE automation_jobs SET state='prepared',paper_id=%s,attempts=attempts+1,error=NULL WHERE job_key=%s",
                (paper["paper_id"], key),
            )
            done += 1
        except PreparationPaused:
            conn.execute(
                "UPDATE automation_jobs SET state='failed',attempts=attempts+1,next_retry=%s,error='preparation_paused' WHERE job_key=%s",
                (server_now(conn) + timedelta(minutes=5), key),
            )
        except (BankShortage, ValueError, QueryCanceled):
            conn.execute(
                "UPDATE automation_jobs SET state='failed',attempts=attempts+1,next_retry=%s,error='insufficient_or_incompatible_verified_bank' WHERE job_key=%s",
                (server_now(conn) + timedelta(minutes=5), key),
            )
    return done
