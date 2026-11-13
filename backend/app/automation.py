"""Opt-in 20:00 Asia/Shanghai preparation of the two daily papers. Quality failures never mean success."""

import os
from psycopg.errors import QueryCanceled
from datetime import timedelta
from zoneinfo import ZoneInfo
from app.training_sessions import server_now
from app.question_quality import Blueprint
from app import training_spec_v2, training_spec_v4, training_spec_v5 as papers_spec
from app.quiz_v2 import create_paper, BankShortage, PreparationPaused

# specification IDs that earlier deployments set in QUIZ_DAILY_SPECIFICATION; nothing new is made from them any more
RETIRED = (training_spec_v4.SPECIFICATION, training_spec_v2.SPECIFICATION)


def daily_blueprints(env):
    """The papers prepared each evening: the objective paper and the subjective paper, independent of each other.

    An unset variable (or an ID of a retired mixed paper) means both. A v5 ID prepares only that one. Any other value fails
    closed rather than preparing an old exam. Stored papers keep their own blueprint and are never re-planned here."""
    spec = env.get("QUIZ_DAILY_SPECIFICATION", "")
    if not spec or spec in RETIRED:
        chosen = list(papers_spec.PAPERS.values())
    elif spec in papers_spec.PAPERS:
        chosen = [papers_spec.PAPERS[spec]]
    else:
        raise ValueError("unknown_daily_training_specification")
    return [Blueprint.model_validate(module.default_blueprint()) for module in chosen]


def selected_daily_blueprint(env):
    """The first of the daily papers (the objective one); see daily_blueprints for all of them."""
    return daily_blueprints(env)[0]


def verified_daily_paper_response(paper, blueprint):
    """A preexisting historical paper must never appear as a success for the new papers."""
    module = papers_spec.PAPERS.get(blueprint.specification)
    if module is None:
        return paper
    if (paper.get("mode") != "reviewed_60"
        or paper.get("blueprint", {}).get("specification") != module.SPECIFICATION
        or paper.get("count") != module.TOTAL_QUESTIONS
        or paper.get("max_points") != module.TOTAL_POINTS):
        raise ValueError("daily_paper_not_proven")
    return paper


def prepare_daily(conn):
    if os.environ.get("QUIZ_DAILY_PREP_ENABLED") != "1":
        return 0
    now = server_now(conn).astimezone(ZoneInfo("Asia/Shanghai"))
    if now.hour < 20:
        return 0
    done = 0
    for blueprint in daily_blueprints(os.environ):
        done += _prepare_paper(conn, blueprint, now)
    return done


def _prepare_paper(conn, blueprint, now):
    """One kind of paper for every user; a shortage of the other kind never holds this one back."""
    done = 0
    kind = papers_spec.kind_of(blueprint.specification)
    for user in conn.execute("SELECT id FROM users ORDER BY id LIMIT 100").fetchall():
        key = f"daily:{kind}:{user['id']}:{now.date()}"
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
