"""Bounded supplementation from current AI-verified knowledge, never quota padding."""

from app.training_spec_v2 import SUBJECTS, COUNTS, POINTS
from app.source_policy import current_source_sql, FILES
from app.candidate_review import snapshot_unit, create_packet
from app.question_quality import Question, AXES
from app.exam_authoring import instructions as exam_instructions, issues as exam_issues
from app.ai_jobs import enqueue, content_sha


def select_exam_style(conn, subject, kind):
    """Select a real supplied past-exam question without manual packet seeding.

    Preserve its year and actual text; no inferred missing exam questions.
    """
    return conn.execute(
        "SELECT t.unit_id AS id,t.year FROM exam_transcript_candidates t "
        "JOIN source_documents d ON d.id=t.document_id "
        "JOIN source_units su ON su.id=t.unit_id "
        "WHERE " + current_source_sql("d", kind="exam_transcription") +
        " AND d.subject=%s AND t.question_type=%s "
        "AND t.question_number IS NOT NULL AND t.year IS NOT NULL "
        "AND NOT (su.quality_flags ?| ARRAY['corrupt_ocr','untranscribed_image','image_dependent']) "
        "ORDER BY t.year DESC,t.id LIMIT 1",
        (subject, kind),
    ).fetchone()


def plan(conn, uid):
    existing = conn.execute(
        "SELECT count(*) AS n FROM ai_jobs WHERE kind='generation' AND state IN ('pending','running','retrying')"
    ).fetchone()["n"]
    if existing >= 2:
        return
    for subject in SUBJECTS:
        for kind, required in COUNTS.items():
            from app.auto_quality import ready
            # A merely "ready" state does not prove current science/answer
            # review, publication or v2 scoring compatibility.
            eligible = conn.execute(
                "SELECT q.id FROM auto_question_states s "
                "JOIN reviewed_questions q ON q.id=s.question_id "
                "WHERE s.status='ready' AND q.status='published' "
                "AND q.content->>'subject'=%s AND q.content->>'question_type'=%s "
                "AND q.content->>'max_points'=%s AND "
                "(NOT EXISTS(SELECT 1 FROM paper_items_v2 i JOIN papers_v2 p ON p.id=i.paper_id "
                "WHERE i.question_id=q.id AND p.user_id=%s) "
                "OR EXISTS(SELECT 1 FROM fsrs_cards f WHERE f.question_id=q.id "
                "AND f.user_id=%s AND f.due<=clock_timestamp()))",
                (subject, kind, str(POINTS[kind]), uid, uid),
            ).fetchall()
            count = sum(bool(ready(conn, row["id"])) for row in eligible)
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
                # Bootstrap directly from the user's genuine past-exam
                # transcript, rather than requiring an earlier hand-created
                # candidate packet. Question boundaries must be recognized and
                # text/image risks resolved before using an item as style.
                style = select_exam_style(conn, subject, kind)
                if not style:
                    continue
                s = snapshot_unit(conn, style["id"])
                if (s["source_kind"] != "exam_transcription" or
                    set(s["quality_flags"]) & {
                        "corrupt_ocr", "untranscribed_image", "image_dependent"
                    }):
                    continue
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
                        style_year=style["year"],
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
                year=p["style_year"],
                relationship="Exact user-provided genuine past-exam style anchor; independent scientific and answer review required",
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
