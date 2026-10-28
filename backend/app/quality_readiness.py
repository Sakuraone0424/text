"""Read-only evidence preparation. Never schedules, approves, or calls a model."""

import json
from app.auto_quality import evidence, hard_issues, review_payload, POLICY, ready
from app.ai_jobs import content_sha


def inspect(conn, qid):
    q, refs, style = evidence(conn, qid)
    checksum = content_sha(q)
    materials = {
        phase: review_payload(phase, qid, checksum, q, refs, style)
        for phase in ("science", "answer")
    }
    sizes = {
        phase: len(json.dumps(p, ensure_ascii=False, sort_keys=True).encode())
        for phase, p in materials.items()
    }
    events = conn.execute(
        "SELECT phase,passed,job_id FROM auto_quality_events WHERE question_id=%s AND content_sha256=%s AND policy_version=%s ORDER BY id",
        (qid, checksum, POLICY),
    ).fetchall()
    return dict(
        question_id=qid,
        content_sha256=checksum,
        evidence_units=len(refs),
        rubric_items=len(q["rubric"]),
        rubric_sum=sum(r["points"] for r in q["rubric"]),
        mapping_tags=q["knowledge_tags"],
        hard_issues=hard_issues(conn, qid, q),
        payload_bytes=sizes,
        within_enqueue_limit=all(n <= 24000 for n in sizes.values()),
        stored_events=events,
        formal_ready=ready(conn, qid),
        required_independent_jobs=["science_review", "answer_review"],
        materials=materials,
        human_decision_replaces_rounds=False,
    )
