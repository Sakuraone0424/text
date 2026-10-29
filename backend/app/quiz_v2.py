"""Reviewed 28-question workflow; deliberately independent of legacy records."""

import json
import os

from psycopg.types.json import Jsonb

from app.question_quality import (
    Audit,
    Blueprint,
    Question,
    audit_issues,
    duplicate,
    duplication_metrics,
)
from app.common import today_cn
from app.training_spec import COUNTS, SUBJECTS, validate_items


class PreparationPaused(ValueError):
    pass


class BankShortage(ValueError):
    def __init__(self, shortages):
        self.shortages = shortages
        super().__init__("Insufficient fresh, published questions; no legacy fallback")


def lock_bank(conn):
    # Publication and selection share a lock so duplicate checks cannot race.
    conn.execute("SELECT pg_advisory_xact_lock(280028)")


def create_question(conn, question: Question):
    return conn.execute(
        "INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id, status",
        (Jsonb(question.model_dump()),),
    ).fetchone()


def review_question(conn, question_id: int, audit: Audit):
    lock_bank(conn)
    row = conn.execute(
        "SELECT * FROM reviewed_questions WHERE id=%s FOR UPDATE", (question_id,)
    ).fetchone()
    if not row:
        raise LookupError("Question not found")
    stems = [
        r["stem"]
        for r in conn.execute(
            "SELECT content->>'stem' AS stem FROM reviewed_questions WHERE id<>%s AND status='published' "
            "UNION ALL SELECT q.stem FROM questions q WHERE NOT EXISTS "
            "(SELECT 1 FROM legacy_question_publications lp WHERE lp.question_id=q.id AND lp.reviewed_question_id=%s)",
            (question_id, question_id),
        ).fetchall()
    ]
    from app.exams import style_issues
    from app.sources import evidence_issues

    issues = (
        audit_issues(row["content"], audit, stems)
        + evidence_issues(conn, row["content"])
        + style_issues(conn, row["content"])
    )
    result = conn.execute(
        "INSERT INTO question_audits(question_id,reviewer,content_snapshot,checks,issues,duplicate_metrics,passed) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id, passed, issues, duplicate_metrics",
        (
            question_id,
            audit.reviewer,
            Jsonb(row["content"]),
            Jsonb(audit.model_dump()),
            Jsonb(issues),
            Jsonb(duplication_metrics(row["content"]["stem"], stems)),
            not issues,
        ),
    ).fetchone()
    # A failed new review withdraws a published question from future papers.
    if issues and row["status"] == "published":
        conn.execute(
            "UPDATE reviewed_questions SET status='withdrawn' WHERE id=%s",
            (question_id,),
        )
    return result


def publish_question(conn, question_id: int):
    lock_bank(conn)
    if conn.execute(
        "SELECT 1 FROM candidate_review_packets p JOIN candidate_revision_links l ON l.old_key=p.candidate_key WHERE p.question_id=%s",
        (question_id,),
    ).fetchone():
        raise ValueError("A superseded candidate cannot be published")
    row = conn.execute(
        "SELECT * FROM reviewed_questions WHERE id=%s FOR UPDATE", (question_id,)
    ).fetchone()
    if not row:
        raise LookupError("Question not found")
    audit_row = conn.execute(
        "SELECT * FROM question_audits WHERE question_id=%s ORDER BY id DESC LIMIT 1",
        (question_id,),
    ).fetchone()
    if not audit_row or not audit_row["passed"]:
        raise ValueError("A passing expert review is required")
    stems = [
        r["stem"]
        for r in conn.execute(
            "SELECT content->>'stem' AS stem FROM reviewed_questions WHERE id<>%s AND status='published' "
            "UNION ALL SELECT q.stem FROM questions q WHERE NOT EXISTS "
            "(SELECT 1 FROM legacy_question_publications lp WHERE lp.question_id=q.id AND lp.reviewed_question_id=%s)",
            (question_id, question_id),
        ).fetchall()
    ]
    from app.exams import style_issues
    from app.sources import evidence_issues

    issues = (
        audit_issues(row["content"], Audit.model_validate(audit_row["checks"]), stems)
        + evidence_issues(conn, row["content"])
        + style_issues(conn, row["content"])
    )
    if issues or audit_row["content_snapshot"] != row["content"]:
        raise ValueError(
            "Publication blocked: stale or failed audit: " + ", ".join(issues)
        )
    conn.execute(
        "UPDATE reviewed_questions SET status='published' WHERE id=%s", (question_id,)
    )
    return {"id": question_id, "status": "published"}


def create_paper(conn, user_id: int, blueprint: Blueprint, day=None):
    resume_unfinished = day is None
    day = day or today_cn()
    lock_bank(conn)
    spec = blueprint.model_dump()
    existing = conn.execute(
        "SELECT * FROM papers_v2 WHERE user_id=%s AND paper_date=%s", (user_id, day)
    ).fetchone()
    if existing:
        if existing["blueprint"] != spec:
            raise ValueError("Today already has a paper with a different blueprint")
        return load_paper(conn, user_id, existing["id"])
    if resume_unfinished:
        # Preparing a paper is not exposure or a running exam. A restored first
        # paper must remain reachable when cloud activation happens another day.
        previous = conn.execute(
            "SELECT p.id FROM papers_v2 p LEFT JOIN training_sessions s ON s.paper_id=p.id "
            "WHERE p.user_id=%s AND p.paper_date<%s AND p.blueprint=%s "
            "AND NOT EXISTS(SELECT 1 FROM attempts_v2 a WHERE a.paper_id=p.id) "
            "AND (s.paper_id IS NULL OR (s.submitted_at IS NULL AND s.deadline>clock_timestamp())) "
            "ORDER BY p.paper_date DESC,p.id DESC LIMIT 1 FOR UPDATE OF p",
            (user_id, day, Jsonb(spec)),
        ).fetchone()
        if previous:
            return load_paper(conn, user_id, previous["id"])
    if os.environ.get("QUIZ_PREPARATION_ENABLED", "1") == "0":
        raise PreparationPaused(
            "New paper preparation is temporarily paused; existing sessions remain available"
        )
    from app.selection import snapshot, pick
    from app.dynamic_model import ALGORITHM_VERSION, topics
    from collections import Counter
    import secrets

    as_of = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    candidates = conn.execute(
        "SELECT id,content FROM reviewed_questions WHERE status='published' ORDER BY id"
    ).fetchall()
    seed = secrets.token_hex(16)
    chapter_counts = Counter()
    decisions = []
    audit = []
    # Also reject semantically close stems already seen by this user; no revision recycling.
    seen = [
        r["stem"]
        for r in conn.execute(
            "SELECT i.snapshot->>'stem' AS stem FROM paper_items_v2 i JOIN papers_v2 p ON p.id=i.paper_id WHERE p.user_id=%s "
            "UNION ALL SELECT q.stem FROM paper_questions i JOIN papers p ON p.id=i.paper_id "
            "JOIN questions q ON q.id=i.question_id WHERE p.user_id=%s",
            (user_id, user_id),
        ).fetchall()
    ]
    selected, shortages = [], []
    from app.sources import evidence_issues
    from app.exams import style_issues
    from app.question_quality import Question

    quality = []
    for row in candidates:
        content = row["content"]
        reasons = []
        try:
            Question.model_validate(content)
        except ValueError:
            reasons.append("invalid_question_or_rubric")
        if not reasons:
            automatic = conn.execute(
                "SELECT status FROM auto_question_states WHERE question_id=%s",
                (row["id"],),
            ).fetchone()
            if automatic:
                from app.auto_quality import ready

                if not ready(conn, row["id"]):
                    reasons.append("automatic_quality_not_ready_or_stale")
            elif os.environ.get("QUIZ_AUTO_ONLY") == "1":
                reasons.append("automatic_ready_qualification_required")
            else:
                # Historical API compatibility; new automatic records never fall back to it.
                if evidence_issues(conn, content, verify_original=False):
                    reasons.append("textbook_evidence_not_valid")
                if style_issues(conn, content):
                    reasons.append("exam_style_source_not_valid")
        audit.append(
            {
                "question_id": row["id"],
                "subject": content.get("subject"),
                "question_type": content.get("question_type"),
                "chapter": (content.get("evidence") or [{}])[0].get("chapter"),
                "knowledge_tags": topics(content) if not reasons else [],
                "is_retest": False,
                "review_knowledge_tags": [],
                "excluded_reasons": reasons,
            }
        )
        if not reasons:
            quality.append(row)
    context, policy, due = snapshot(
        conn, user_id, [r["content"] for r in quality], as_of
    )
    eligible = []
    for row in quality:
        content = row["content"]
        item = next(r for r in audit if r["question_id"] == row["id"])
        if row["id"] not in due and any(
            duplicate(content["stem"], stem) for stem in seen
        ):
            item["excluded_reasons"].append("previously_seen_stem_without_due_review")
            continue
        item["is_retest"] = row["id"] in due
        item["review_knowledge_tags"] = [
            tag
            for tag in item["knowledge_tags"]
            if any(
                c["due"]
                for c in context["topics"][content["subject"]][tag]["review"]["cards"]
            )
        ]
        eligible.append(row)
    for subject in SUBJECTS:
        for kind, count in COUNTS.items():
            pool = []
            for row in eligible:
                content = row["content"]
                if content["subject"] != subject or content["question_type"] != kind:
                    continue
                from app.answer_cues import conflict

                if conflict(content, [r["content"] for r in selected + pool]):
                    next(r for r in audit if r["question_id"] == row["id"])[
                        "excluded_reasons"
                    ].append("mutual_answer_cue")
                    continue
                if any(
                    duplicate(content["stem"], r["content"]["stem"])
                    for r in selected + pool
                ):
                    next(r for r in audit if r["question_id"] == row["id"])[
                        "excluded_reasons"
                    ].append("duplicate_candidate_stem")
                    continue
                pool.append(row)
            if len(pool) < count:
                shortages.append(
                    {
                        "subject": subject,
                        "question_type": kind,
                        "required": count,
                        "available": len(pool),
                        "missing": count - len(pool),
                    }
                )
            selected.extend(
                pick(
                    pool,
                    count,
                    chapter_counts,
                    context,
                    policy,
                    seed,
                    subject,
                    kind,
                    decisions,
                )
            )
    if shortages:
        raise BankShortage(shortages)
    validate_items([r["content"] for r in selected])
    from app.answer_cues import conflict

    if any(
        conflict(r["content"], [s["content"] for s in selected[:i]])
        for i, r in enumerate(selected)
    ):
        raise ValueError("Mutual answer cue in paper; refuse generation")
    paper_id = conn.execute(
        "INSERT INTO papers_v2(user_id,paper_date,blueprint) VALUES (%s,%s,%s) RETURNING id",
        (user_id, day, Jsonb(spec)),
    ).fetchone()["id"]
    for pos, row in enumerate(selected, 1):
        conn.execute(
            "INSERT INTO paper_items_v2(paper_id,question_id,position,snapshot,is_retest) VALUES (%s,%s,%s,%s,%s)",
            (paper_id, row["id"], pos, Jsonb(row["content"]), row["id"] in due),
        )
    selected_ids = {r["id"] for r in selected}
    for item in audit:
        item["selected"] = item["question_id"] in selected_ids
        if not item["selected"] and not item["excluded_reasons"]:
            item["excluded_reasons"] = ["not_drawn_in_fixed_quota"]
    conn.execute(
        "INSERT INTO selection_runs(paper_id,user_id,seed,algorithm_version,policy_id,as_of,config,context,candidates,decisions) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            paper_id,
            user_id,
            seed,
            ALGORITHM_VERSION,
            context["policy_id"],
            as_of,
            Jsonb(policy.model_dump(mode="json")),
            Jsonb(context),
            Jsonb(audit),
            Jsonb(decisions),
        ),
    )
    return load_paper(conn, user_id, paper_id)


def owned_paper(conn, user_id, paper_id, *, lock=False):
    row = conn.execute(
        "SELECT * FROM papers_v2 WHERE id=%s AND user_id=%s"
        + (" FOR UPDATE" if lock else ""),
        (paper_id, user_id),
    ).fetchone()
    if not row:
        raise LookupError("Paper not found")
    return row


def load_paper(conn, user_id, paper_id):
    row = owned_paper(conn, user_id, paper_id)
    items = conn.execute(
        "SELECT * FROM paper_items_v2 WHERE paper_id=%s ORDER BY position", (paper_id,)
    ).fetchall()
    from app.training_sessions import state

    timing = state(conn, user_id, paper_id)
    run = conn.execute(
        "SELECT candidates FROM selection_runs WHERE paper_id=%s", (paper_id,)
    ).fetchone()
    review_tags = (
        {
            r["question_id"]: r["review_knowledge_tags"]
            for r in run["candidates"]
            if r.get("selected")
        }
        if run
        else {}
    )
    # No solutions, excerpts or rubrics in the unsubmitted paper response.
    questions = [
        {
            "id": i["question_id"],
            "position": i["position"],
            "blank_count": len(i["snapshot"].get("blanks", [])),
            "is_retest": i.get("is_retest", False),
            "review_knowledge_tags": review_tags.get(i["question_id"], []),
            **{
                k: i["snapshot"][k]
                for k in ("subject", "question_type", "stem", "options", "max_points")
            },
        }
        for i in items
    ]
    return {
        "paper_id": paper_id,
        "date": row["paper_date"].isoformat(),
        "mode": "reviewed_28",
        "blueprint": row["blueprint"],
        "count": len(items),
        "max_points": sum(i["snapshot"]["max_points"] for i in items),
        "duration_seconds": 3600,
        "timing": timing,
        "questions": questions if timing["status"] != "not_started" else [],
    }


def schedule_review(conn, attempt_id, question_id):
    from app.memory import record_failure

    record_failure(conn, attempt_id, question_id)


def submit(conn, user_id, paper_id, answers, expected_version=0):
    from app.training_sessions import submit_training

    return submit_training(conn, user_id, paper_id, answers, expected_version)


def grade(
    conn, paper_id, question_id, criterion_points, feedback, reviewer, details=None
):
    paper = conn.execute(
        "SELECT * FROM papers_v2 WHERE id=%s FOR UPDATE", (paper_id,)
    ).fetchone()
    if not paper:
        raise LookupError("Paper not found")
    row = conn.execute(
        "SELECT a.*, i.snapshot FROM attempts_v2 t JOIN answers_v2 a ON a.attempt_id=t.id "
        "JOIN paper_items_v2 i ON i.paper_id=t.paper_id AND i.question_id=a.question_id "
        "WHERE t.paper_id=%s AND a.question_id=%s",
        (paper_id, question_id),
    ).fetchone()
    if not row:
        raise LookupError("Submitted answer not found")
    q = row["snapshot"]
    try:
        submitted = json.loads(row["answer"])
    except ValueError:
        submitted = {"text": row["answer"]}
    if not isinstance(submitted, dict):
        submitted = {"text": row["answer"]}
    for image_id in submitted.get("image_ids", []):
        recognized = conn.execute(
            "SELECT confirmed FROM handwriting_transcriptions WHERE image_id=%s",
            (image_id,),
        ).fetchone()
        if not recognized or not recognized["confirmed"]:
            raise ValueError(
                "Handwriting is pending confirmation; do not guess or assign zero"
            )
    if (
        q["question_type"] == "single_choice"
        or (
            q["question_type"] == "fill_blank"
            and not conn.execute(
                "SELECT 1 FROM auto_question_states WHERE question_id=%s",
                (question_id,),
            ).fetchone()
        )
    ) or row["points"] is not None:
        raise ValueError("Only pending subjective answers can be graded")
    if len(criterion_points) != len(q["rubric"]) or any(
        type(p) is not int or p < 0 or p > c["points"]
        for p, c in zip(criterion_points, q["rubric"])
    ):
        raise ValueError(
            "Scores must match the snapshot rubric and stay within each criterion"
        )
    points = sum(criterion_points)
    if q["question_type"] == "fill_blank":
        blanks = (details or {}).get("blank_points", [])
        maxima = (
            [b["points"] for b in q["blanks"]] if q.get("blanks") else [q["max_points"]]
        )
        known = (row.get("feedback") or {}).get("blank_points", [0] * len(maxima))
        supplied = (
            submitted.get("blanks", [])
            if q.get("blanks")
            else [submitted.get("text", "")]
        )
        if (
            len(blanks) != len(maxima)
            or any(
                type(p) is not int
                or p < known[i]
                or p > maxima[i]
                or (p > 0 and (i >= len(supplied) or not supplied[i].strip()))
                for i, p in enumerate(blanks)
            )
            or sum(blanks) != points
        ):
            raise ValueError(
                "Semantic fill scores must match every blank and preserve exact matches"
            )
    record = {
        "criterion_points": criterion_points,
        "comment": feedback,
        "criteria": [
            {"description": c["description"], "points": p, "max_points": c["points"]}
            for p, c in zip(criterion_points, q["rubric"])
        ],
        "matched_points": [
            c["description"] for p, c in zip(criterion_points, q["rubric"]) if p > 0
        ],
        "missing_points": [
            c["description"]
            for p, c in zip(criterion_points, q["rubric"])
            if p < c["points"]
        ],
        **(details or {}),
    }
    record["handwriting"] = conn.execute(
        "SELECT t.image_id,t.transcription,t.confidence,t.confirmed,t.reviewer FROM handwriting_transcriptions t WHERE t.image_id=ANY(%s)",
        (submitted.get("image_ids", []),),
    ).fetchall()
    conn.execute(
        "INSERT INTO grading_events(attempt_id,question_id,reviewer,record) VALUES (%s,%s,%s,%s)",
        (row["attempt_id"], question_id, reviewer, Jsonb(record)),
    )
    conn.execute(
        "UPDATE answers_v2 SET points=%s,feedback=%s,graded_by=%s WHERE attempt_id=%s AND question_id=%s",
        (points, Jsonb(record), reviewer, row["attempt_id"], question_id),
    )
    if points < q["max_points"]:
        schedule_review(conn, row["attempt_id"], question_id)
    from app.archiving import enqueue

    enqueue(conn, paper["user_id"], paper_id)
    return result(conn, paper["user_id"], paper_id)


def result(conn, user_id, paper_id):
    owned_paper(conn, user_id, paper_id)
    attempt = conn.execute(
        "SELECT * FROM attempts_v2 WHERE paper_id=%s", (paper_id,)
    ).fetchone()
    if not attempt:
        raise LookupError("Paper has not been submitted")
    rows = conn.execute(
        "SELECT a.*,i.snapshot FROM answers_v2 a JOIN paper_items_v2 i ON i.question_id=a.question_id "
        "AND i.paper_id=%s WHERE a.attempt_id=%s ORDER BY i.position",
        (paper_id, attempt["id"]),
    ).fetchall()
    pending = sum(r["points"] is None for r in rows)
    if not pending and attempt["finalized_at"] is None:
        conn.execute(
            "UPDATE attempts_v2 SET finalized_at=NOW() WHERE id=%s", (attempt["id"],)
        )
    from app.selection import summary

    run = conn.execute(
        "SELECT * FROM selection_runs WHERE paper_id=%s", (paper_id,)
    ).fetchone()
    analysis = summary(run) if run else {"status": "historical_trace_not_recorded"}
    return {
        "selection_analysis": analysis,
        "subject_scores": {
            subject: {
                "score": None
                if any(
                    r["points"] is None
                    for r in rows
                    if r["snapshot"]["subject"] == subject
                )
                else sum(
                    r["points"] for r in rows if r["snapshot"]["subject"] == subject
                ),
                "graded_points": sum(
                    r["points"] or 0
                    for r in rows
                    if r["snapshot"]["subject"] == subject
                ),
                "max_points": sum(
                    r["snapshot"]["max_points"]
                    for r in rows
                    if r["snapshot"]["subject"] == subject
                ),
            }
            for subject in sorted({r["snapshot"]["subject"] for r in rows})
        },
        "paper_id": paper_id,
        "attempt_id": attempt["id"],
        "status": "pending_review" if pending else "graded",
        "pending": pending,
        "score": None if pending else sum(r["points"] for r in rows),
        "graded_points": sum(r["points"] or 0 for r in rows),
        "max_points": sum(r["snapshot"]["max_points"] for r in rows),
        "answers": [
            {
                "question_id": r["question_id"],
                "answer": r["answer"],
                "points": r["points"],
                "feedback": r["feedback"],
                "stem": r["snapshot"]["stem"],
                "subject": r["snapshot"]["subject"],
                "question_type": r["snapshot"]["question_type"],
                "max_points": r["snapshot"]["max_points"],
                "solution": r["snapshot"]["answer"],
                "options": r["snapshot"].get("options", {}),
                "solution_display": (
                    f'{r["snapshot"]["answer"]}. {r["snapshot"]["options"][r["snapshot"]["answer"]]}'
                    if r["snapshot"]["question_type"] == "single_choice"
                    and r["snapshot"]["answer"] in r["snapshot"].get("options", {})
                    else r["snapshot"]["answer"]
                ),
                "evidence": r["snapshot"]["evidence"],
                "rubric": r["snapshot"]["rubric"],
            }
            for r in rows
        ],
    }
