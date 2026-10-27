"""Evidence-bound subjective grading; unresolved answers remain pending, never zero."""

import json
from pydantic import Field, StrictBool, StrictInt
from app.question_quality import StrictModel
from app.ai_jobs import enqueue, content_sha


class Grade(StrictModel):
    criterion_points: list[StrictInt] = Field(min_length=1, max_length=50)
    blank_points: list[StrictInt] = Field(default_factory=list, max_length=3)
    reasons: list[str] = Field(min_length=1, max_length=50)
    total: StrictInt
    uncertain: StrictBool
    explanation: str = Field(min_length=20, max_length=10000)
    knowledge_tags: list[str] = Field(max_length=30)


def schedule(conn, uid, paper_id):
    rows = conn.execute(
        "SELECT a.*,i.snapshot FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id WHERE p.user_id=%s AND p.id=%s AND a.points IS NULL",
        (uid, paper_id),
    ).fetchall()
    jobs = []
    for r in rows:
        answer = json.loads(r["answer"])
        q = r["snapshot"]
        if answer.get("image_ids"):
            continue  # No unverified vision capability or guessed handwriting.
        from app.candidate_review import snapshot_unit

        sources = [snapshot_unit(conn, e["source_unit_id"]) for e in q["evidence"]]
        payload = dict(
            paper_id=paper_id,
            question_id=r["question_id"],
            attempt_id=r["attempt_id"],
            question=q,
            answer=answer,
            answer_sha256=content_sha(answer),
            evidence=sources,
            schema=Grade.model_json_schema(),
            instructions="Award every rubric criterion independently, accept scientifically equivalent expressions. Never grade by length. Explain each score and cite provided evidence. Flag unresolved scientific conflict or uncertain meaning rather than guessing a score.",
        )
        jobs.append(
            enqueue(
                conn,
                uid,
                f"grade:{r['attempt_id']}:{r['question_id']}:{content_sha(answer)}",
                "grading",
                payload,
            )
        )
    return jobs


def apply(conn, job):
    if job["state"] != "succeeded" or job["result"].get("real_api") is not True:
        raise ValueError("actual_grading_call_required")
    p = job["payload"]
    g = Grade.model_validate(job["result"]["response"])
    q = p["question"]
    row = conn.execute(
        "SELECT * FROM answers_v2 WHERE attempt_id=%s AND question_id=%s FOR UPDATE",
        (p["attempt_id"], p["question_id"]),
    ).fetchone()
    if not row or content_sha(json.loads(row["answer"])) != p["answer_sha256"]:
        raise ValueError("answer_changed")
    if row["points"] is not None:
        if (row["feedback"] or {}).get("ai_job_id") == job["id"]:
            return "already_applied"
        raise ValueError("historical_score_must_not_be_overwritten")
    if set(g.knowledge_tags) - set(q["knowledge_tags"]):
        raise ValueError("unfounded_grading_knowledge_mapping")
    if g.uncertain:
        raise ValueError("uncertain_score_requires_independent_reassessment")
    if (
        len(g.criterion_points) != len(q["rubric"])
        or len(g.reasons) != len(q["rubric"])
        or any(len(x.strip()) < 10 for x in g.reasons)
        or any(
            x < 0 or x > r["points"] for x, r in zip(g.criterion_points, q["rubric"])
        )
        or sum(g.criterion_points) != g.total
    ):
        raise ValueError("invalid_rubric_scores")
    from app.quiz_v2 import grade

    return grade(
        conn,
        p["paper_id"],
        p["question_id"],
        g.criterion_points,
        g.explanation,
        "AI自动核验",
        details=dict(
            ai_job_id=job["id"],
            model_id=job["settings_snapshot"]["model_id"],
            effort=job["settings_snapshot"]["effort"],
            grading_version="auto-grading/1",
            criterion_reasons=g.reasons,
            blank_points=g.blank_points,
            knowledge_tags=g.knowledge_tags,
        ),
    )
