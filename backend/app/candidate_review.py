"""Immutable AI-assisted draft packets and separately authenticated human decisions."""

import hashlib
import json
from uuid import UUID
from psycopg.types.json import Jsonb
from app.question_quality import Question, AXES
from app.source_policy import active_document
from app.ocr_sources import unit_sha

CHECKS = (
    *AXES,
    "ocr_excerpt",
    "answer_rubric",
    "knowledge_mapping",
    "transcription_level",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def snapshot_unit(conn, uid):
    row = conn.execute(
        "SELECT u.*,d.file_id,d.sha256,d.subject,d.metadata FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=%s",
        (uid,),
    ).fetchone()
    if not row or not active_document(conn, row["document_id"]):
        raise ValueError("Evidence is missing or superseded")
    return dict(
        unit_id=uid,
        unit_sha256=unit_sha(row),
        file_id=row["file_id"],
        source_sha256=row["sha256"],
        chapter=row["chapter"],
        section=row["section"],
        ocr_page=row["ocr_global_page"],
        text=row["text"],
        quality_flags=row["quality_flags"],
        subject=row["subject"],
        source_kind=row["metadata"].get("source_kind"),
    )


def create_packet(conn, payload):
    q = Question.model_validate(payload["question"])
    question = q.model_dump()
    analysis = payload["ai_analysis"]
    if (
        analysis.get("status") != "AI-assisted / pending human review"
        or analysis.get("independent_human_review") is not False
        or set(analysis.get("axes", {})) != set(AXES)
    ):
        raise ValueError("Explicit pending AI analysis with five dimensions required")
    if not analysis.get("uncertainties") or len(
        analysis.get("rubric_evidence", [])
    ) != len(q.rubric):
        raise ValueError("Uncertainties and evidence for every scoring point required")
    evidence = []
    for e in q.evidence:
        u = snapshot_unit(conn, e.source_unit_id)
        if (
            u["source_kind"] != "textbook"
            or u["subject"] != q.subject
            or e.excerpt not in u["text"]
            or (e.file_id, e.source_sha256, e.ocr_page, e.paragraph_id, e.chapter)
            != (
                u["file_id"],
                u["source_sha256"],
                u["ocr_page"],
                u["unit_id"],
                u["chapter"],
            )
        ):
            raise ValueError("Textbook quote, subject or version identity mismatch")
        evidence.append(u)
    allowed = {u["unit_id"] for u in evidence}
    for point in analysis["rubric_evidence"]:
        if (
            not point.get("unit_ids")
            or not set(point["unit_ids"]).issubset(allowed)
            or len(point.get("explanation", "")) < 10
        ):
            raise ValueError("Each rubric point needs explicit cited source analysis")
    style = []
    for ref in payload["style_sources"]:
        u = snapshot_unit(conn, ref["unit_id"])
        if u["source_kind"] != "exam_transcription" or u["subject"] != q.subject:
            raise ValueError("Style example must remain an in-subject transcription")
        style.append(
            {
                **u,
                "source_level": "transcription",
                "original_verified": False,
                "official_answer_verified": False,
                "year": ref.get("year"),
                "relationship": ref["relationship"],
                "frequency_eligible": False,
            }
        )
    if not style:
        raise ValueError("At least one located transcription reference required")
    snapshot = dict(
        question=question,
        ai_analysis=analysis,
        evidence_snapshot=evidence,
        style_snapshot=style,
    )
    revision = digest(snapshot)
    conn.execute(
        "SELECT pg_advisory_xact_lock(%s)",
        (int(hashlib.sha256(payload["candidate_key"].encode()).hexdigest()[:15], 16),),
    )
    old = conn.execute(
        "SELECT * FROM candidate_review_packets WHERE candidate_key=%s",
        (payload["candidate_key"],),
    ).fetchone()
    if old:
        if old["revision_sha256"] != revision:
            raise ValueError("Existing packet immutable; use a new revision key")
        link = conn.execute(
            "SELECT old_key FROM candidate_revision_links WHERE new_key=%s",
            (payload["candidate_key"],),
        ).fetchone()
        if (link["old_key"] if link else None) != payload.get("supersedes"):
            raise ValueError("Revision lineage differs")
        return old["question_id"]
    supersedes = payload.get("supersedes")
    if supersedes:
        from app.quiz_v2 import lock_bank

        lock_bank(conn)
        previous = conn.execute(
            "SELECT p.*,q.status FROM candidate_review_packets p JOIN reviewed_questions q ON q.id=p.question_id WHERE p.candidate_key=%s FOR UPDATE OF p",
            (supersedes,),
        ).fetchone()
        if (
            not previous
            or previous["status"] != "draft"
            or previous["batch_key"] != payload["batch_key"]
            or (
                previous["question_snapshot"]["subject"],
                previous["question_snapshot"]["question_type"],
            )
            != (q.subject, q.question_type)
            or conn.execute(
                "SELECT 1 FROM candidate_revision_links WHERE old_key=%s", (supersedes,)
            ).fetchone()
        ):
            raise ValueError(
                "Only an active draft of the same batch/subject/type can be revised"
            )
    qid = conn.execute(
        "INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id",
        (Jsonb(question),),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO candidate_review_packets(candidate_key,batch_key,question_id,revision_sha256,question_snapshot,ai_analysis,evidence_snapshot,style_snapshot) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            payload["candidate_key"],
            payload["batch_key"],
            qid,
            revision,
            Jsonb(question),
            Jsonb(analysis),
            Jsonb(evidence),
            Jsonb(style),
        ),
    )
    if supersedes:
        conn.execute(
            "INSERT INTO candidate_revision_links(old_key,new_key) VALUES (%s,%s)",
            (supersedes, payload["candidate_key"]),
        )
    return qid


def inspect(conn, key):
    row = conn.execute(
        "SELECT * FROM candidate_review_packets WHERE candidate_key=%s", (key,)
    ).fetchone()
    if not row:
        raise LookupError("Candidate not found")
    row["decisions"] = conn.execute(
        "SELECT * FROM candidate_human_decisions WHERE candidate_key=%s ORDER BY id",
        (key,),
    ).fetchall()
    row["last_decision_id"] = row["decisions"][-1]["id"] if row["decisions"] else 0
    link = conn.execute(
        "SELECT new_key FROM candidate_revision_links WHERE old_key=%s", (key,)
    ).fetchone()
    row["superseded_by"] = link["new_key"] if link else None
    row["status"] = (
        row["decisions"][-1]["action"] if row["decisions"] else "pending_human_review"
    )
    row["formal_published"] = (
        conn.execute(
            "SELECT status FROM reviewed_questions WHERE id=%s", (row["question_id"],)
        ).fetchone()["status"]
        == "published"
    )
    from app.question_quality import similarity

    related = []
    for other in conn.execute(
        "SELECT p.candidate_key,p.question_snapshot FROM candidate_review_packets p WHERE p.candidate_key<>%s AND NOT EXISTS(SELECT 1 FROM candidate_revision_links l WHERE l.old_key=p.candidate_key)",
        (key,),
    ).fetchall():
        question = other["question_snapshot"]
        common = sorted(
            {
                a
                for a in row["question_snapshot"]["knowledge_tags"]
                for b in question["knowledge_tags"]
                if a == b or a.startswith(b + "/") or b.startswith(a + "/")
            }
        )
        score = similarity(row["question_snapshot"]["stem"], question["stem"])
        related.append(
            dict(
                candidate_key=other["candidate_key"],
                stem=question["stem"],
                similarity=round(score, 4),
                common_tags=common,
                automated_only=True,
            )
        )
    row["related_candidates"] = sorted(
        related, key=lambda r: (bool(r["common_tags"]), r["similarity"]), reverse=True
    )[:5]
    row["style_context"] = []
    for style in row["style_snapshot"]:
        contextual = conn.execute(
            "SELECT evidence FROM evidence_review_work WHERE algorithm='evidence-work/1.6' AND kind='exam_question' AND evidence->>'unit_id'=%s",
            (str(style["unit_id"]),),
        ).fetchone()
        if contextual:
            source = contextual["evidence"]
            row["style_context"].append(
                dict(
                    anchor_unit_id=style["unit_id"],
                    unit_ids=source["unit_ids"],
                    text=source["text"],
                    concerns=source["concerns"],
                    source_level="transcription",
                    human_reviewed=False,
                    note="Same immutable source-file SHA; structural grouping remains pending, not original-paper verification",
                )
            )
    return row


def decide(conn, key, reviewer, data):
    UUID(data["request_id"])
    if (
        not reviewer.strip()
        or data.get("human_attestation") is not True
        or len(data.get("note", "").strip()) < 20
        or data["action"] not in ("approve_candidate", "return", "revise")
    ):
        raise ValueError(
            "Explicit human attestation, action and substantive note required"
        )
    if set(data.get("checks", {})) != set(CHECKS) or any(
        type(v) is not bool for v in data["checks"].values()
    ):
        raise ValueError("All review dimensions must be addressed")
    if data["action"] == "approve_candidate" and not all(data["checks"].values()):
        raise ValueError(
            "Passing candidate review requires every explicit confirmation"
        )
    conn.execute(
        "SELECT candidate_key FROM candidate_review_packets WHERE candidate_key=%s FOR UPDATE",
        (key,),
    ).fetchone()
    row = inspect(conn, key)
    prior = conn.execute(
        "SELECT * FROM candidate_human_decisions WHERE request_id=%s",
        (data["request_id"],),
    ).fetchone()
    if prior:
        if (
            prior["candidate_key"],
            prior["reviewer"],
            prior["revision_sha256"],
            prior["action"],
            prior["checks"],
            prior["note"],
        ) != (
            key,
            reviewer,
            data["revision_sha256"],
            data["action"],
            data["checks"],
            data["note"].strip(),
        ):
            raise ValueError("Idempotency key already used for a different decision")
        return prior
    if row["superseded_by"]:
        raise ValueError("This draft has a newer revision; open the latest packet")
    from app.review_notes import latest, mark_submitted

    note = latest(conn, key, reviewer)
    if (
        note["version"]
        and note["state"] == "editing"
        and data.get("expected_note_version") is None
    ):
        raise ValueError("Current unsigned opinion version is required before signing")
    if (
        data.get("expected_note_version") is not None
        and data["expected_note_version"] != note["version"]
    ):
        raise ValueError(
            "Unsigned opinion changed on another device; reload before signing"
        )
    if (
        data.get("expected_note_version") is not None
        and note["version"]
        and (
            note["note"].strip() != data["note"].strip()
            or note["checks"] != data["checks"]
        )
    ):
        raise ValueError("Save the latest opinion before signing")
    if (
        data["revision_sha256"] != row["revision_sha256"]
        or data["expected_last_decision_id"] != row["last_decision_id"]
    ):
        raise ValueError("Review revision changed; reload before deciding")
    live = conn.execute(
        "SELECT content,status FROM reviewed_questions WHERE id=%s",
        (row["question_id"],),
    ).fetchone()
    if live["content"] != row["question_snapshot"] or live["status"] != "draft":
        raise ValueError("Question changed or left draft state")
    for u in row["evidence_snapshot"] + row["style_snapshot"]:
        current = snapshot_unit(conn, u["unit_id"])
        if (
            current["source_sha256"] != u["source_sha256"]
            or current["unit_sha256"] != u["unit_sha256"]
        ):
            raise ValueError("Source version changed; review is stale")
    decision = conn.execute(
        "INSERT INTO candidate_human_decisions(candidate_key,revision_sha256,reviewer,action,checks,note,human_attestation,request_id,note_draft_id) VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s) RETURNING *",
        (
            key,
            row["revision_sha256"],
            reviewer,
            data["action"],
            Jsonb(data["checks"]),
            data["note"].strip(),
            data["request_id"],
            note.get("id"),
        ),
    ).fetchone()
    mark_submitted(conn, key, reviewer, row, decision["id"])
    return decision


def summary(conn):
    rows = conn.execute(
        "SELECT p.candidate_key,p.question_snapshot->>'subject' AS subject,p.question_snapshot->>'question_type' AS type,(SELECT action FROM candidate_human_decisions d WHERE d.candidate_key=p.candidate_key ORDER BY id DESC LIMIT 1) AS action FROM candidate_review_packets p WHERE NOT EXISTS(SELECT 1 FROM candidate_revision_links l WHERE l.old_key=p.candidate_key) ORDER BY p.candidate_key"
    ).fetchall()
    qualified = 0
    qualified_by = {}
    from app.sources import evidence_issues
    from app.exams import style_issues

    for q in conn.execute(
        "SELECT q.* FROM reviewed_questions q JOIN candidate_review_packets p ON p.question_id=q.id WHERE q.status='published'"
    ).fetchall():
        audit = conn.execute(
            "SELECT passed,content_snapshot FROM question_audits WHERE question_id=%s ORDER BY id DESC LIMIT 1",
            (q["id"],),
        ).fetchone()
        if (
            audit
            and audit["passed"]
            and audit["content_snapshot"] == q["content"]
            and not evidence_issues(conn, q["content"])
            and not style_issues(conn, q["content"])
        ):
            qualified += 1
            pair = (q["content"]["subject"], q["content"]["question_type"])
            qualified_by[pair] = qualified_by.get(pair, 0) + 1
    from app.training_spec import SUBJECTS, COUNTS

    breakdown = []
    for subject in SUBJECTS:
        for kind, required in COUNTS.items():
            group = [r for r in rows if r["subject"] == subject and r["type"] == kind]
            breakdown.append(
                dict(
                    subject=subject,
                    type=kind,
                    total=len(group),
                    approved=sum(r["action"] == "approve_candidate" for r in group),
                    returned=sum(r["action"] == "return" for r in group),
                    revision_requested=sum(r["action"] == "revise" for r in group),
                    untouched=sum(r["action"] is None for r in group),
                    formal_qualified=qualified_by.get((subject, kind), 0),
                    required=required,
                )
            )
    from app.ocr_sources import review_snapshot
    from app.source_policy import current_source_sql

    reviewed_units = conn.execute(
        "SELECT DISTINCT u.* FROM source_units u JOIN source_documents d ON d.id=u.document_id JOIN source_review_events e ON e.unit_id=u.id WHERE e.review_kind='science' AND d.metadata->>'source_kind'='textbook' AND "
        + current_source_sql()
    ).fetchall()
    source_science = sum(
        r.get("science", False) for r in review_snapshot(conn, reviewed_units).values()
    )
    return dict(
        candidates=rows,
        ai_assisted=len(rows),
        total_ai_revision_packets=conn.execute(
            "SELECT count(*) AS n FROM candidate_review_packets"
        ).fetchone()["n"],
        pending=sum(
            not r["action"] or r["action"] in ("return", "revise") for r in rows
        ),
        human_candidate_approved=sum(r["action"] == "approve_candidate" for r in rows),
        formal_qualified=qualified,
        by_subject_type=breakdown,
        textbook_science_reviewed_units=source_science,
        formal_qualification_note="Candidate decisions do not replace source, style, relation or final publication gates",
    )
