"""Bounded supplementation from current AI-verified knowledge, never quota padding."""

from app.training_spec_v2 import SUBJECTS, COUNTS, POINTS
from app.source_policy import current_source_sql, FILES
from app.candidate_review import snapshot_unit, create_packet
from app.question_quality import Question, AXES
from app.exam_authoring import instructions as exam_instructions, issues as exam_issues
from app.source_bibliography import from_database as source_bibliography
from app.ai_jobs import enqueue, content_sha

STARTUP_RESERVE_DAYS = 2  # 60 questions/day, 120 verified candidates per user


def select_exam_style(conn, subject, kind, concept=None):
    """Find a genuine exam-style anchor; lexical overlap is NOT science proof.

    Prefer the same topic only when it appears literally in the old question.
    Otherwise spread stylistic examples across real years so the generator does
    not mimic the identical recent exam item for every topic.
    """
    import re
    import unicodedata

    rows = conn.execute(
        "SELECT t.unit_id AS id,t.year,t.transcription FROM exam_transcript_candidates t "
        "JOIN source_documents d ON d.id=t.document_id "
        "JOIN source_units su ON su.id=t.unit_id "
        "WHERE " + current_source_sql("d", kind="exam_transcription") +
        " AND d.subject=%s AND t.question_type=%s "
        "AND t.question_number IS NOT NULL AND t.year IS NOT NULL "
        "AND NOT (su.quality_flags ?| ARRAY['corrupt_ocr','untranscribed_image','image_dependent']) "
        "AND NOT (t.concerns ?| ARRAY['corrupt_ocr','untranscribed_image','image_dependent',"
        "'question_boundary_unconfirmed','question_type_unknown','year_unassigned']) "
        "ORDER BY t.year DESC,t.id LIMIT 200",
        (subject, kind),
    ).fetchall()
    if not rows:
        return None
    relation = (concept or {}).get("relation") or {}
    terms = [
        str(relation.get(key, "")).strip() for key in ("name", "target")
    ]
    def normal(text):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())
    anchors = [normal(x) for x in terms if len(normal(x)) >= 2]
    matches = [
        sum(term in normal(row["transcription"]) for term in anchors)
        for row in rows
    ]
    best = max(matches, default=0)
    pool = [row for row, score in zip(rows, matches) if score == best]
    idx = (int((concept or {}).get("unit_id", 0)) % len(pool)) if concept else 0
    chosen = dict(pool[idx])
    chosen["lexical_topic_overlap"] = best
    chosen["scope"] = "past_exam_style_not_textbook_answer_evidence"
    return chosen

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
            if count >= required * STARTUP_RESERVE_DAYS:
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
                style = select_exam_style(conn, subject, kind, concept)
                if not style:
                    continue
                s = snapshot_unit(conn, style["id"])
                if (s["source_kind"] != "exam_transcription" or
                    set(s["quality_flags"]) & {
                        "corrupt_ocr", "untranscribed_image", "image_dependent"
                    }):
                    continue
                try:
                    book = source_bibliography(conn, u)
                except ValueError:
                    # Unknown author/edition or a changed source checksum must
                    # never be replaced by an invented bibliographic label.
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
                        style_overlap=style["lexical_topic_overlap"],
                        source_bibliography=book,
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
                relationship=(
                    "Genuine past-exam style with lexical topic overlap; not answer evidence"
                    if p.get("style_overlap", 0) else
                    "Genuine past-exam style of same subject/type; topical similarity unverified"
                ),
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
