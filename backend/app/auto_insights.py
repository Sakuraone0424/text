"""Read-only learning advice; never revises scores or overrides FSRS scheduling."""

from pydantic import Field, StrictInt
from app.question_quality import StrictModel
from app.candidate_review import snapshot_unit
from app.ai_jobs import enqueue


class AdviceItem(StrictModel):
    question_id: StrictInt
    knowledge_tags: list[str] = Field(min_length=1, max_length=30)
    source_unit_id: StrictInt
    excerpt: str = Field(min_length=5, max_length=2000)
    explanation: str = Field(min_length=30, max_length=5000)
    next_action: str = Field(min_length=20, max_length=2000)


class Advice(StrictModel):
    summary: str = Field(min_length=30, max_length=6000)
    items: list[AdviceItem] = Field(min_length=1, max_length=3)
    limitations: list[str] = Field(min_length=1, max_length=20)


def plan(conn, uid):
    attempt = conn.execute(
        "SELECT t.id,p.id AS paper_id FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id WHERE p.user_id=%s AND t.finalized_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM ai_jobs j WHERE j.job_key='insight:'||t.id::text||':explanation') ORDER BY t.id LIMIT 1",
        (uid,),
    ).fetchone()
    if not attempt:
        return
    rows = conn.execute(
        "SELECT a.*,i.snapshot FROM answers_v2 a JOIN paper_items_v2 i ON i.question_id=a.question_id AND i.paper_id=%s WHERE a.attempt_id=%s AND a.points<(i.snapshot->>'max_points')::int ORDER BY a.question_id LIMIT 3",
        (attempt["paper_id"], attempt["id"]),
    ).fetchall()
    if not rows:
        return
    mistakes = []
    for row in rows:
        q = row["snapshot"]
        units = [snapshot_unit(conn, e["source_unit_id"]) for e in q["evidence"]]
        mistakes.append(
            dict(
                question_id=row["question_id"],
                question=q,
                answer=row["answer"],
                points=row["points"],
                feedback=row["feedback"],
                evidence=units,
            )
        )
    for kind in ("explanation", "study_plan"):
        enqueue(
            conn,
            uid,
            f"insight:{attempt['id']}:{kind}",
            kind,
            dict(
                mistakes=mistakes,
                schema=Advice.model_json_schema(),
                purpose=kind,
                instructions="Explain actual lost rubric points and suggest evidence-bound review. Never invent history, diagnosis or knowledge tags. Cite exact supplied textbook excerpt and an existing question ID. Do not alter grades, FSRS dates or claim official/human verification. State uncertainty and limited coverage (at most three actual mistakes).",
            ),
        )


def accept(conn, job):
    if job["state"] != "succeeded" or job["result"].get("real_api") is not True:
        raise ValueError("actual_advice_call_required")
    advice = Advice.model_validate(job["result"]["response"])
    allowed = {q["question_id"]: q for q in job["payload"]["mistakes"]}
    for item in advice.items:
        q = allowed.get(item.question_id)
        if not q or not set(item.knowledge_tags) <= set(
            q["question"]["knowledge_tags"]
        ):
            raise ValueError("invented_learning_history_or_mapping")
        refs = {u["unit_id"]: u for u in q["evidence"]}
        u = snapshot_unit(conn, item.source_unit_id)
        if u != refs.get(item.source_unit_id) or item.excerpt not in u["text"]:
            raise ValueError("invented_or_stale_advice_evidence")
    return advice
