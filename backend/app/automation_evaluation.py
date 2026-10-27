"""Reproducible evidence metrics, never model self-rating as scientific accuracy."""

from collections import Counter
from app.auto_quality import evidence, hard_issues


def evaluate(conn):
    rows = conn.execute(
        "SELECT p.question_id,p.revision_sha256 FROM candidate_review_packets p WHERE NOT EXISTS(SELECT 1 FROM candidate_revision_links l WHERE l.old_key=p.candidate_key) ORDER BY p.question_id"
    ).fetchall()
    types = Counter()
    valid = 0
    failures = []
    for row in rows:
        try:
            q, refs, style = evidence(conn, row["question_id"])
            types[q["subject"] + ":" + q["question_type"]] += 1
            issues = hard_issues(conn, row["question_id"], q)
            if issues:
                failures.append(dict(question_id=row["question_id"], issues=issues))
            else:
                valid += 1
        except ValueError as exc:
            failures.append(dict(question_id=row["question_id"], issues=[str(exc)]))
    calls = conn.execute(
        "SELECT input_tokens,output_tokens,actual_usd FROM ai_jobs WHERE attempts>0"
    ).fetchall()
    tokens = (
        0
        if not calls
        else (
            sum(r["input_tokens"] + r["output_tokens"] for r in calls)
            if all(
                r["input_tokens"] is not None and r["output_tokens"] is not None
                for r in calls
            )
            else None
        )
    )
    cost = (
        sum(r["actual_usd"] for r in calls)
        if calls and all(r["actual_usd"] is not None for r in calls)
        else None
    )
    return dict(
        candidate_count=len(rows),
        deterministic_identity_and_rubric_valid=valid,
        by_subject_type=dict(types),
        failures=failures,
        scientific_accuracy=None,
        scientific_accuracy_reason="No independent trusted reference verdicts; existing AI draft answers are not ground truth",
        real_model_comparison_performed=False,
        started_model_task_count=len(calls),
        real_api_tokens=tokens,
        real_api_cost=cost,
        model_latency=None,
    )
