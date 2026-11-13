"""Read-only learning advice; never revises scores or overrides FSRS scheduling."""

from pydantic import Field, StrictInt
from app.question_quality import StrictModel
from app.candidate_review import snapshot_unit
from app.ai_jobs import enqueue_rekeyed as enqueue


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


GRADED_ATTEMPTS_SQL = (
    "SELECT t.id,p.id AS paper_id FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id WHERE p.user_id=%s "
    # a paper is complete when nothing is waiting for a score (handing in the result page once is not required)
    "AND NOT EXISTS(SELECT 1 FROM answers_v2 a WHERE a.attempt_id=t.id AND a.points IS NULL "
    "AND NOT EXISTS(SELECT 1 FROM question_reports r WHERE r.paper_id=p.id AND r.question_id=a.question_id)) "
    "AND EXISTS(SELECT 1 FROM answers_v2 a WHERE a.attempt_id=t.id) "
    "AND NOT EXISTS(SELECT 1 FROM ai_jobs j WHERE j.job_key='insight:'||t.id::text||':explanation') ORDER BY t.id"
)


def plan(conn, uid):
    """Advice is written for a paper once every answer of it has a score and it lost points somewhere. Papers without a lost point
    (or whose lost answers are all reported questions) need none and do not hold up the next paper."""
    made = 0
    for attempt in conn.execute(GRADED_ATTEMPTS_SQL, (uid,)).fetchall():
        if made >= 2:
            break
        made += 1 if _plan_attempt(conn, uid, attempt) else 0


def _plan_attempt(conn, uid, attempt):
    rows = conn.execute(
        "SELECT a.*,i.snapshot FROM answers_v2 a JOIN paper_items_v2 i ON i.question_id=a.question_id AND i.paper_id=%s WHERE a.attempt_id=%s AND a.points<(i.snapshot->>'max_points')::float8 ORDER BY a.question_id LIMIT 3",
        (attempt["paper_id"], attempt["id"]),
    ).fetchall()
    if not rows:
        return False
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
    return True


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


def readiness(conn, uid):
    """Why there is, or is not, advice yet, in plain words: advice is written for a paper once ALL its answers have a score
    (and it lost points). Returns the counts and a sentence for the empty state."""
    papers = conn.execute(
        "SELECT t.id,(SELECT count(*) FROM answers_v2 a WHERE a.attempt_id=t.id AND a.points IS NULL AND NOT EXISTS("
        "SELECT 1 FROM question_reports r WHERE r.paper_id=t.paper_id AND r.question_id=a.question_id)) AS pending,"
        "(SELECT count(*) FROM answers_v2 a JOIN paper_items_v2 i ON i.paper_id=t.paper_id AND i.question_id=a.question_id "
        "WHERE a.attempt_id=t.id AND a.points<(i.snapshot->>'max_points')::float8) AS lost "
        "FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id WHERE p.user_id=%s", (uid,)).fetchall()
    jobs = conn.execute(
        "SELECT state FROM ai_jobs WHERE user_id=%s AND kind IN ('explanation','study_plan')", (uid,)).fetchall()
    submitted = len(papers)
    graded = sum(1 for p in papers if not p["pending"])
    waiting = submitted - graded
    with_lost = sum(1 for p in papers if not p["pending"] and p["lost"])
    state = "ready" if any(j["state"] == "succeeded" for j in jobs) else None
    if state:
        text = None
    elif not submitted:
        state, text = "no_paper", "交卷并判分完成 1 套后生成（还差 1 套）"
    elif not graded:
        state, text = "grading", f"已交卷 {submitted} 套，还没有一套判分完成。判分完成后自动生成（还差 {min(waiting, 1)} 套完成判分）"
    elif not with_lost:
        state, text = "no_mistakes", "已判分的卷子没有失分题，暂时不需要建议。有失分后自动生成"
    elif any(j["state"] in ("pending", "retrying", "running") for j in jobs):
        state, text = "generating", "判分已完成，正在生成，稍后刷新查看"
    elif any(j["state"] == "blocked" for j in jobs):
        state, text = "blocked", "生成没有成功，会在下次 AI 任务运行时重试"
    else:
        state, text = "queued", "判分已完成，将在下次 AI 任务运行时生成（通常几分钟内）"
    return dict(state=state, text=text, submitted=submitted, graded=graded, waiting=waiting, with_lost=with_lost, needs=dict(papers=1, all_answers_graded=True))
