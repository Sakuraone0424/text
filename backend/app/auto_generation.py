"""Bounded supplementation from current AI-verified knowledge, never quota padding."""

from app.training_spec import SUBJECTS, COUNTS, POINTS
from app.source_policy import current_source_sql, FILES
from app.candidate_review import snapshot_unit, create_packet
from app.question_quality import Question, AXES
from app.exam_authoring import instructions as exam_instructions, issues as exam_issues
from app.ai_jobs import enqueue, content_sha


def plan(conn, uid):
    existing = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE kind='generation' AND state IN ('pending','running','retrying')"
    ).fetchone()["n"]
    if existing >= 2:
        return
    for subject in SUBJECTS:
        for kind, required in COUNTS.items():
            count = conn.execute(
                "SELECT count(*) AS n FROM auto_question_states s JOIN reviewed_questions q ON q.id=s.question_id WHERE s.status='ready' AND q.content->>'subject'=%s AND q.content->>'question_type'=%s AND (NOT EXISTS(SELECT 1 FROM paper_items_v2 i JOIN papers_v2 p ON p.id=i.paper_id WHERE i.question_id=q.id AND p.user_id=%s) OR EXISTS(SELECT 1 FROM fsrs_cards f WHERE f.question_id=q.id AND f.user_id=%s AND f.due<=clock_timestamp()))",
                (subject, kind, uid, uid),
            ).fetchone()["n"]
            if count >= required:
                continue
            # Reuse only second-round knowledge, never the 17,128 pending proposals as approved facts.
            concepts = conn.execute(
                "SELECT k.* FROM auto_knowledge_events k JOIN source_units u ON u.id=k.unit_id JOIN source_documents d ON d.id=u.document_id WHERE "
                + current_source_sql()
                + " AND k.status='ready' AND k.relation->>'subject'=%s AND NOT EXISTS(SELECT 1 FROM ai_jobs j WHERE j.job_key='supplement:'||%s||':'||%s||':'||k.unit_id::text||':'||k.unit_sha256) ORDER BY k.id LIMIT 30",
                (subject, subject, kind),
            ).fetchall()
            for concept in concepts:
                try:
                    u = snapshot_unit(conn, concept["unit_id"])
                except ValueError:
                    continue
                if u["unit_sha256"] != concept["unit_sha256"]:
                    continue
                key = f"supplement:{subject}:{kind}:{concept['unit_id']}:{concept['unit_sha256']}"
                if conn.execute(
                    "SELECT 1 FROM ai_jobs WHERE job_key=%s", (key,)
                ).fetchone():
                    continue
                style = conn.execute(
                    "SELECT (s.value->>'unit_id')::bigint AS id FROM candidate_review_packets p CROSS JOIN LATERAL jsonb_array_elements(p.style_snapshot) s WHERE p.question_snapshot->>'subject'=%s AND p.question_snapshot->>'question_type'=%s ORDER BY p.question_id LIMIT 1",
                    (subject, kind),
                ).fetchone()
                if not style:
                    continue
                s = snapshot_unit(conn, style["id"])
                enqueue(
                    conn,
                    uid,
                    key,
                    "generation",
                    dict(
                        subject=subject,
                        question_type=kind,
                        max_points=POINTS[kind],
                        evidence=[u],
                        style=[s],
                        source_bibliography=dict(
                            file_id=u["file_id"],
                            textbook=FILES[u["file_id"]]["filename"],
                            authors="未核查（当前OCR交接未提供书目认证）",
                            edition="校订OCR文本，不声明原书物理页核验",
                        ),
                        schema=Question.model_json_schema(),
                        instructions=exam_instructions(kind, POINTS[kind]),
                    ),
                )
                return


def accept(conn, job):
    if job["state"] != "succeeded" or job["result"].get("real_api") is not True:
        raise ValueError("actual_generation_call_required")
    p = job["payload"]
    q = Question.model_validate(job["result"]["response"]["question"])
    if (q.subject, q.question_type, q.max_points) != (
        p["subject"],
        p["question_type"],
        p["max_points"],
    ):
        raise ValueError("generated_quota_mismatch")
    structural_issues = exam_issues(q)
    if structural_issues:
        raise ValueError("generated_question_quality_gate_failed:" + ",".join(structural_issues))
    allowed = {u["unit_id"] for u in p["evidence"]}
    bibliography = p["source_bibliography"]
    if any(
        (e.textbook, e.authors, e.edition)
        != (bibliography["textbook"], bibliography["authors"], bibliography["edition"])
        for e in q.evidence
    ):
        raise ValueError("invented_bibliography")
    if not {e.source_unit_id for e in q.evidence} <= allowed:
        raise ValueError("unrequested_source")
    payload = dict(
        candidate_key="auto-generated-"
        + str(job["id"])
        + "-"
        + content_sha(q.model_dump())[:12],
        batch_key="auto-supplement/1",
        question=q.model_dump(),
        style_sources=[
            dict(
                unit_id=u["unit_id"],
                year=None,
                relationship="Transcription style candidate requires independent AI scope/type review",
            )
            for u in p["style"]
        ],
        ai_analysis=dict(
            status="AI自动核验 / pending independent checks",
            independent_human_review=False,
            axes={
                a: "Generated draft; no previous AI conclusion is treated as independent proof"
                for a in AXES
            },
            uncertainties=["Requires new independent scientific and answer rounds"],
            rubric_evidence=[
                dict(
                    unit_ids=list(allowed),
                    explanation="Exact retrieved evidence attached; independent answer round must verify every rubric criterion",
                )
                for _ in q.rubric
            ],
        ),
    )
    return create_packet(conn, payload)
