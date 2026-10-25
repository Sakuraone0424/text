"""Explicit version boundary and separate operator authorization."""

import hmac
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, StrictInt

from app.auth import require_auth
from app.db import get_db
from app.question_quality import (
    Audit,
    Blueprint,
    Question,
    StrictModel,
    Subject,
    QuestionType,
)
from app.quiz_service import ensure_user
from app import quiz_v2 as service

router = APIRouter(prefix="/v2", tags=["reviewed-28"])


def require_reviewer(request: Request):
    configured = os.environ.get("QUIZ_REVIEW_API_KEY", "")
    if not configured:
        raise HTTPException(
            503, "Review operations are disabled: QUIZ_REVIEW_API_KEY is not configured"
        )
    if not hmac.compare_digest(request.headers.get("x-review-api-key", ""), configured):
        raise HTTPException(403, "Reviewer authorization required")


def operation(fn, username=None):
    try:
        with get_db() as conn:
            return (
                fn(conn, ensure_user(conn, username))
                if username is not None
                else fn(conn)
            )
    except service.BankShortage as exc:
        raise HTTPException(
            409, {"code": "insufficient_reviewed_bank", "shortages": exc.shortages}
        ) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/questions", dependencies=[Depends(require_reviewer)])
def draft(question: Question):
    return operation(lambda c: service.create_question(c, question))


@router.get("/questions/{question_id}", dependencies=[Depends(require_reviewer)])
def inspect_question(question_id: int):
    def read(c):
        question = c.execute(
            "SELECT * FROM reviewed_questions WHERE id=%s", (question_id,)
        ).fetchone()
        if not question:
            raise LookupError("Question not found")
        question["audits"] = c.execute(
            "SELECT * FROM question_audits WHERE question_id=%s ORDER BY id",
            (question_id,),
        ).fetchall()
        return question

    return operation(read)


@router.post("/questions/{question_id}/audit", dependencies=[Depends(require_reviewer)])
def audit(question_id: int, payload: Audit):
    return operation(lambda c: service.review_question(c, question_id, payload))


@router.post(
    "/questions/{question_id}/publish", dependencies=[Depends(require_reviewer)]
)
def publish(question_id: int):
    return operation(lambda c: service.publish_question(c, question_id))


@router.post(
    "/questions/{question_id}/withdraw", dependencies=[Depends(require_reviewer)]
)
def withdraw(question_id: int):
    def change(c):
        service.lock_bank(c)
        row = c.execute(
            "UPDATE reviewed_questions SET status='withdrawn' WHERE id=%s RETURNING id,status",
            (question_id,),
        ).fetchone()
        if not row:
            raise LookupError("Question not found")
        return row

    return operation(change)


@router.post("/papers")
def paper(blueprint: Blueprint, username: str = Depends(require_auth)):
    return operation(lambda c, u: service.create_paper(c, u, blueprint), username)


@router.get("/papers/{paper_id}")
def read_paper(paper_id: int, username: str = Depends(require_auth)):
    return operation(lambda c, u: service.load_paper(c, u, paper_id), username)


class Submission(StrictModel):
    answers: dict[int, str | dict] = Field(default_factory=dict, max_length=28)
    expected_version: StrictInt = Field(default=0, ge=0)


@router.post("/papers/{paper_id}/submit")
def submit(paper_id: int, payload: Submission, username: str = Depends(require_auth)):
    return operation(
        lambda c, u: service.submit(
            c, u, paper_id, payload.answers, payload.expected_version
        ),
        username,
    )


@router.get("/papers/{paper_id}/result")
def result(paper_id: int, username: str = Depends(require_auth)):
    return operation(lambda c, u: service.result(c, u, paper_id), username)


ErrorCategory = Literal[
    "concept_unclear",
    "memory_omission",
    "misread_question",
    "mechanism_error",
    "incomplete_expression",
    "calculation_error",
    "weak_chapter_links",
]


class Grade(StrictModel):
    error_categories: list[ErrorCategory] = Field(default_factory=list, max_length=7)
    concept_errors: list[str] | None = Field(default=None, max_length=30)
    imprecisions: list[str] | None = Field(default=None, max_length=30)
    suggestions: list[str] | None = Field(default=None, max_length=30)
    criterion_points: list[StrictInt] = Field(min_length=1, max_length=50)
    feedback: str = Field(min_length=1, max_length=5000)
    reviewer: str = Field(min_length=1, max_length=200)


@router.post(
    "/papers/{paper_id}/answers/{question_id}/grade",
    dependencies=[Depends(require_reviewer)],
)
def grade(paper_id: int, question_id: int, payload: Grade):
    return operation(
        lambda c: service.grade(
            c,
            paper_id,
            question_id,
            payload.criterion_points,
            payload.feedback,
            payload.reviewer,
            details={
                "concept_errors": payload.concept_errors,
                "error_categories": payload.error_categories,
                "imprecisions": payload.imprecisions,
                "suggestions": payload.suggestions,
            },
        )
    )


@router.get("/review/due")
def reviews(username: str = Depends(require_auth)):
    return operation(lambda c, u: {"items": service.due_reviews(c, u)}, username)


@router.post("/review/{attempt_id}/{question_id}/{stage}/complete")
def complete_review(
    attempt_id: int, question_id: int, stage: int, username: str = Depends(require_auth)
):
    def complete(c, u):
        row = c.execute(
            "UPDATE reviews_v2 r SET completed_at=NOW() FROM attempts_v2 a JOIN papers_v2 p ON p.id=a.paper_id "
            "WHERE r.attempt_id=a.id AND p.user_id=%s AND r.attempt_id=%s AND r.question_id=%s "
            "AND r.review_stage=%s AND r.due_date<=%s RETURNING r.question_id",
            (u, attempt_id, question_id, stage, service.today_cn()),
        ).fetchone()
        if not row:
            raise LookupError("Due review not found")
        return {"completed": True}

    return operation(complete, username)


@router.post(
    "/questions/{question_id}/export-legacy", dependencies=[Depends(require_reviewer)]
)
def export_legacy(question_id: int):
    return operation(lambda c: service.export_legacy(c, question_id))


@router.get("/quality/report", dependencies=[Depends(require_reviewer)])
def quality_report():
    def report(c):
        groups = c.execute(
            "SELECT content->>'subject' AS subject, content->>'question_type' AS question_type, status, COUNT(*) AS count "
            "FROM reviewed_questions GROUP BY 1,2,3 ORDER BY 1,2,3"
        ).fetchall()
        counts = c.execute(
            "SELECT (SELECT COUNT(*) FROM questions) AS legacy_questions, "
            "(SELECT COUNT(*) FROM legacy_question_publications lp JOIN reviewed_questions q "
            "ON q.id=lp.reviewed_question_id WHERE q.status='published') AS approved_legacy_questions, "
            "(SELECT COUNT(*) FROM question_audits WHERE NOT passed) AS failed_audits"
        ).fetchone()
        return {
            **counts,
            "groups": groups,
            "duplicate_threshold": 0.85,
            "scientific_verification": "Expert textbook verification required; not established by automatic checks",
        }

    return operation(report)


@router.post("/papers/{paper_id}/start")
def start_training(paper_id: int, username: str = Depends(require_auth)):
    from app.training_sessions import start

    def begin(c, u):
        start(c, u, paper_id)
        return service.load_paper(c, u, paper_id)

    return operation(begin, username)


class Draft(StrictModel):
    expected_version: StrictInt = Field(ge=0)
    value: dict


@router.put("/papers/{paper_id}/drafts/{question_id}")
def save_draft(
    paper_id: int,
    question_id: int,
    payload: Draft,
    username: str = Depends(require_auth),
):
    from app.training_sessions import save

    return operation(
        lambda c, u: save(
            c, u, paper_id, question_id, payload.value, payload.expected_version
        ),
        username,
    )


@router.post("/papers/{paper_id}/images/{question_id}")
async def upload_image(
    paper_id: int,
    question_id: int,
    request: Request,
    expected_version: int,
    username: str = Depends(require_auth),
):
    from app.images import verify_image, store_image

    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(413, "Image exceeds 8 MiB")
    try:
        media = verify_image(bytes(raw))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return operation(
        lambda c, u: store_image(
            c, u, paper_id, question_id, bytes(raw), media, expected_version
        ),
        username,
    )


@router.get("/images/{image_id}")
def read_image(image_id: int, username: str = Depends(require_auth)):
    from fastapi import Response

    def read(c, u):
        row = c.execute(
            "SELECT i.* FROM answer_images i JOIN papers_v2 p ON p.id=i.paper_id WHERE i.id=%s AND p.user_id=%s",
            (image_id, u),
        ).fetchone()
        if not row:
            raise LookupError("Image not found")
        return Response(
            bytes(row["content"]),
            media_type=row["media_type"],
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    return operation(read, username)


class Transcription(StrictModel):
    text: str = Field(max_length=20000)
    confidence: float = Field(ge=0, le=1)
    confirmed: bool = Field(strict=True)
    reviewer: str = Field(min_length=1, max_length=200)


@router.post(
    "/images/{image_id}/transcription", dependencies=[Depends(require_reviewer)]
)
def transcription(image_id: int, payload: Transcription):
    def write(c):
        if not c.execute(
            "SELECT 1 FROM answer_images WHERE id=%s", (image_id,)
        ).fetchone():
            raise LookupError("Image not found")
        c.execute(
            "INSERT INTO handwriting_events(image_id,record) VALUES (%s,%s)",
            (
                image_id,
                __import__("psycopg.types.json", fromlist=["Jsonb"]).Jsonb(
                    payload.model_dump()
                ),
            ),
        )
        c.execute(
            "INSERT INTO handwriting_transcriptions(image_id,transcription,confidence,confirmed,reviewer) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT(image_id) DO UPDATE SET transcription=EXCLUDED.transcription,confidence=EXCLUDED.confidence,confirmed=EXCLUDED.confirmed,reviewer=EXCLUDED.reviewer",
            (
                image_id,
                payload.text,
                payload.confidence,
                payload.confirmed,
                payload.reviewer,
            ),
        )
        return {"status": "confirmed" if payload.confirmed else "pending_confirmation"}

    return operation(write)


class MemoryRating(StrictModel):
    rating: StrictInt = Field(ge=1, le=4)
    expected_version: StrictInt = Field(ge=0)
    event_key: str = Field(min_length=1, max_length=200)


@router.get("/memory/due")
def memory_due(username: str = Depends(require_auth)):
    from app.memory import due

    return operation(lambda c, u: {"items": due(c, u)}, username)


@router.post("/memory/{question_id}/review")
def memory_review(
    question_id: int, payload: MemoryRating, username: str = Depends(require_auth)
):
    from app.memory import review

    return operation(
        lambda c, u: review(
            c,
            u,
            question_id,
            payload.rating,
            payload.event_key,
            payload.expected_version,
        ),
        username,
    )


class Consent(StrictModel):
    enabled: bool = Field(strict=True)


@router.put("/archive/consent")
def archive_consent(payload: Consent, username: str = Depends(require_auth)):
    def consent(c, u):
        c.execute(
            "INSERT INTO archive_consent VALUES (%s,%s) ON CONFLICT(user_id) DO UPDATE SET enabled=EXCLUDED.enabled",
            (u, payload.enabled),
        )
        if payload.enabled:
            from app.archiving import enqueue

            for row in c.execute(
                "SELECT a.paper_id FROM attempts_v2 a JOIN papers_v2 p ON p.id=a.paper_id WHERE p.user_id=%s",
                (u,),
            ).fetchall():
                enqueue(c, u, row["paper_id"])
        return {
            "consented": payload.enabled,
            "configured": __import__("os").environ.get("NOTION_SYNC_ENABLED") == "1",
        }

    return operation(consent, username)


@router.get("/archive/status")
def archive_status(username: str = Depends(require_auth)):
    return operation(
        lambda c, u: {
            "configured": os.environ.get("NOTION_SYNC_ENABLED") == "1"
            and bool(os.environ.get("NOTION_TOKEN"))
            and bool(os.environ.get("NOTION_DATABASE_ID")),
            "consented": bool(
                (
                    c.execute(
                        "SELECT enabled FROM archive_consent WHERE user_id=%s", (u,)
                    ).fetchone()
                    or {}
                ).get("enabled")
            ),
            "jobs": c.execute(
                "SELECT j.id,j.state,j.attempts,j.error,j.verified_at FROM archive_jobs j "
                "JOIN attempts_v2 a ON a.id=j.attempt_id JOIN papers_v2 p ON p.id=a.paper_id WHERE p.user_id=%s ORDER BY j.id",
                (u,),
            ).fetchall(),
        },
        username,
    )


@router.get("/sources/search", dependencies=[Depends(require_reviewer)])
def source_search(q: str):
    from app.sources import search

    return operation(lambda c: {"units": search(c, q)})


class SourceVerification(StrictModel):
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)


@router.post("/sources/{unit_id}/verify", dependencies=[Depends(require_reviewer)])
def verify_source(unit_id: int, payload: SourceVerification):
    from app.sources import verify_unit

    def verify(c):
        verify_unit(c, unit_id, payload.reviewer, payload.note)
        return {"verified": True}

    return operation(verify)


@router.get("/exams/coverage", dependencies=[Depends(require_reviewer)])
def exam_coverage():
    from app.exams import coverage

    return operation(lambda c: {"years": coverage(c)})


@router.get("/exams/frequencies", dependencies=[Depends(require_reviewer)])
def exam_frequencies():
    from app.exams import frequencies

    return operation(lambda c: {"frequencies": frequencies(c)})


class ExamRecord(StrictModel):
    source_unit_id: StrictInt = Field(ge=1)
    original_question_id: str = Field(min_length=1, max_length=200)
    subject: Subject
    year: StrictInt = Field(ge=1900, le=2100)
    question_type: QuestionType
    stem: str = Field(min_length=1, max_length=20000)
    options: dict[str, str] = Field(default_factory=dict)
    knowledge_tags: list[str] = Field(default_factory=list, max_length=100)
    category: str = Field(min_length=1, max_length=100)
    answer: str | None = Field(default=None, max_length=20000)
    answer_kind: Literal[
        "official", "source_reference", "personal_reference", "missing"
    ] = "missing"
    rubric: list[dict] = Field(default_factory=list, max_length=100)
    textbook_units: list[StrictInt] = Field(default_factory=list, max_length=100)
    original_verified: bool = Field(default=False, strict=True)
    complete_paper: bool = Field(default=False, strict=True)
    official_answer_evidence_unit: StrictInt | None = Field(default=None, ge=1)


@router.post("/exams", dependencies=[Depends(require_reviewer)])
def import_exam(payload: ExamRecord):
    from app.exams import add

    return operation(lambda c: add(c, payload.model_dump()))


@router.get("/stats/weekly")
def weekly_stats(username: str = Depends(require_auth)):
    return operation(
        lambda c, u: {
            "chapters": c.execute(
                "SELECT i.snapshot->>'subject' AS subject,i.snapshot->'evidence'->0->>'chapter' AS chapter, "
                "SUM(a.points) AS graded_points,SUM((i.snapshot->>'max_points')::int) FILTER(WHERE a.points IS NOT NULL) AS graded_max_points, "
                "COUNT(*) FILTER(WHERE a.points IS NULL) AS pending,COUNT(*) AS answers "
                "FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id "
                "JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id "
                "WHERE p.user_id=%s AND t.submitted_at>=clock_timestamp()-INTERVAL '7 days' GROUP BY 1,2 ORDER BY 1,2",
                (u,),
            ).fetchall()
        },
        username,
    )


@router.get("/automation/status")
def automation_status(username: str = Depends(require_auth)):
    return operation(
        lambda c, u: {
            "daily_preparation_enabled": os.environ.get("QUIZ_DAILY_PREP_ENABLED")
            == "1",
            "timezone": "Asia/Shanghai",
            "scheduled_hour": 20,
            "jobs": c.execute(
                "SELECT job_key,state,attempts,paper_id,error FROM automation_jobs WHERE user_id=%s ORDER BY target_date DESC LIMIT 30",
                (u,),
            ).fetchall(),
        },
        username,
    )


class KnowledgeTags(StrictModel):
    tags: list[str] = Field(max_length=100)


@router.post("/sources/{unit_id}/knowledge", dependencies=[Depends(require_reviewer)])
def link_knowledge(unit_id: int, payload: KnowledgeTags):
    from app.sources import annotate

    def write(c):
        annotate(c, unit_id, payload.tags)
        return {"linked": True}

    return operation(write)


@router.get("/memory/{question_id}/solution")
def review_solution(question_id: int, username: str = Depends(require_auth)):
    def read(c, u):
        row = c.execute(
            "SELECT q.content FROM fsrs_cards f JOIN reviewed_questions q ON q.id=f.question_id WHERE f.user_id=%s AND f.question_id=%s",
            (u, question_id),
        ).fetchone()
        if not row:
            raise LookupError("Review card not found")
        return {
            "answer": row["content"]["answer"],
            "rubric": row["content"]["rubric"],
            "evidence": row["content"]["evidence"],
        }

    return operation(read, username)


class Reflection(StrictModel):
    event_key: str = Field(min_length=1, max_length=200)
    category: ErrorCategory
    note: str = Field(min_length=1, max_length=2000)


@router.post("/papers/{paper_id}/answers/{question_id}/reflection")
def reflect(
    paper_id: int,
    question_id: int,
    payload: Reflection,
    username: str = Depends(require_auth),
):
    def write(c, u):
        service.owned_paper(c, u, paper_id)
        row = c.execute(
            "SELECT a.attempt_id FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id WHERE t.paper_id=%s AND a.question_id=%s",
            (paper_id, question_id),
        ).fetchone()
        if not row:
            raise LookupError("Submitted answer not found")
        old = c.execute(
            "SELECT * FROM answer_reflections WHERE attempt_id=%s AND question_id=%s AND event_key=%s",
            (row["attempt_id"], question_id, payload.event_key),
        ).fetchone()
        if old:
            if old["category"] != payload.category or old["note"] != payload.note:
                raise ValueError("Reflection event key reused with different content")
            return {"recorded": True, "replayed": True}
        c.execute(
            "INSERT INTO answer_reflections(attempt_id,question_id,event_key,category,note) VALUES (%s,%s,%s,%s,%s)",
            (
                row["attempt_id"],
                question_id,
                payload.event_key,
                payload.category,
                payload.note,
            ),
        )
        from app.archiving import enqueue

        enqueue(c, u, paper_id)
        return {"recorded": True, "replayed": False}

    return operation(write, username)
