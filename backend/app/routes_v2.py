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
from app.common import ensure_user
from app.dynamic_model import Policy
from app import quiz_v2 as service

router = APIRouter(prefix="/v2", tags=["reviewed-training"])


@router.get("/training-specification")
def training_specification():
    # Local isolated regression fixtures explicitly exercise pre-upgrade papers.
    # Never expose this legacy preview in a deployed production environment.
    if (
        os.environ.get("QUIZ_QA_LEGACY_FIXTURE") == "1"
        and os.environ.get("QUIZ_DB_SCHEMA", "").startswith("quiz_live_test_")
    ):
        from app.training_spec import formal_specification
    else:
        from app.training_spec_v2 import formal_specification
    return formal_specification()


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
    except service.PreparationPaused as exc:
        raise HTTPException(503, str(exc)) from exc
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
    answers: dict[int, str | dict] = Field(default_factory=dict, max_length=60)
    expected_version: StrictInt = Field(default=0, ge=0)


@router.post("/papers/{paper_id}/submit")
def submit(paper_id: int, payload: Submission, username: str = Depends(require_auth)):
    def finalize_and_queue(c, u):
        result = service.submit(c, u, paper_id, payload.answers, payload.expected_version)
        if result.get("pending"):
            from app.grading_activity import try_queue
            result["grading_queue"] = try_queue(c, u, paper_id)
        return result

    return operation(finalize_and_queue, username)


@router.get("/papers/{paper_id}/result")
def result(paper_id: int, username: str = Depends(require_auth)):
    return operation(lambda c, u: service.result(c, u, paper_id), username)


@router.get("/papers/{paper_id}/verification")
def question_verification(paper_id: int, username: str = Depends(require_auth)):
    """Only the owner of a submitted exam may view evidence audit disclosures."""
    from app.question_verification import report

    return operation(lambda c, u: report(c, u, paper_id), username)


@router.get("/papers/{paper_id}/grading-activity")
def grading_activity(paper_id: int, username: str = Depends(require_auth)):
    """Read-only progress for the authenticated owner; no AI task is started."""
    from app.grading_activity import progress

    return operation(lambda c, u: progress(c, u, paper_id), username)


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


@router.get("/quality/report", dependencies=[Depends(require_reviewer)])
def quality_report():
    def report(c):
        groups = c.execute(
            "SELECT content->>'subject' AS subject, content->>'question_type' AS question_type, status, COUNT(*) AS count "
            "FROM reviewed_questions GROUP BY 1,2,3 ORDER BY 1,2,3"
        ).fetchall()
        counts = c.execute(
            "SELECT (SELECT COUNT(*) FROM questions) AS historical_questions,(SELECT COUNT(*) FROM question_audits WHERE NOT passed) AS failed_audits"
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

    return operation(lambda c: {"units": search(c, q, collection_only=True)})


class SourceVerification(StrictModel):
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)


@router.post("/sources/{unit_id}/verify", dependencies=[Depends(require_reviewer)])
def verify_source(unit_id: int, payload: SourceVerification):
    from app.sources import verify_unit

    def verify(c):
        result = verify_unit(c, unit_id, payload.reviewer, payload.note)
        return result if isinstance(result, dict) else {"verified": True}

    return operation(verify)


@router.get("/exams/coverage", dependencies=[Depends(require_reviewer)])
def exam_coverage():
    from app.ocr_reports import annual_report

    return operation(lambda c: {"years": annual_report(c)})


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
        question = row["content"]
        options = question.get("options") or {}
        answer = question["answer"]
        return {
            "answer": answer,
            "question_type": question["question_type"],
            "options": options,
            "answer_display": (
                f"{answer}. {options[answer]}"
                if question["question_type"] == "single_choice" and answer in options
                else answer
            ),
            "rubric": question["rubric"],
            "evidence": question["evidence"],
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


class ScopeRegistration(StrictModel):
    scope_key: str = Field(min_length=1, max_length=200)
    subject: Subject
    paper_code: str = Field(min_length=1, max_length=100)
    syllabus: str = Field(min_length=1, max_length=500)
    source_unit_id: StrictInt = Field(ge=1)
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)


@router.post("/exams/scopes", dependencies=[Depends(require_reviewer)])
def register_exam_scope(payload: ScopeRegistration):
    from app.exams import register_scope

    return operation(lambda c: register_scope(c, payload.model_dump()))


class ExamClassification(StrictModel):
    scope_key: str = Field(min_length=1, max_length=200)
    source_kind: Literal["real_exam", "simulation", "transcription", "unclassified"]
    mapping_verified: bool = Field(strict=True)
    tag_contributions: dict[str, float] = Field(default_factory=dict)
    primary_tags: list[str] = Field(default_factory=list, max_length=30)
    cognitive_level: Literal["core", "mechanism", "application"] | None = None
    canonical_item_id: str | None = Field(default=None, max_length=200)
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)


@router.post(
    "/exams/{record_id}/classification", dependencies=[Depends(require_reviewer)]
)
def classify_exam(record_id: int, payload: ExamClassification):
    from app.exams import classify

    return operation(
        lambda c: classify(
            c,
            record_id,
            payload.model_dump(exclude={"reviewer", "note"}),
            payload.reviewer,
            payload.note,
        )
    )


class CompleteSample(StrictModel):
    scope_key: str = Field(min_length=1, max_length=200)
    year: StrictInt = Field(ge=1900, le=2100)
    source_unit_id: StrictInt = Field(ge=1)
    expected_questions: StrictInt = Field(ge=1, le=500)
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)


@router.post("/exams/samples", dependencies=[Depends(require_reviewer)])
def register_complete_sample(payload: CompleteSample):
    from app.exams import register_sample

    return operation(lambda c: register_sample(c, payload.model_dump()))


class PolicyRegistration(StrictModel):
    username: str = Field(min_length=1, max_length=80)
    config: Policy
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)


@router.post("/selection/policies", dependencies=[Depends(require_reviewer)])
def register_policy(payload: PolicyRegistration):
    from psycopg.types.json import Jsonb

    def save(c):
        for subject, key in payload.config.scopes.items():
            if (
                key
                and not c.execute(
                    "SELECT 1 FROM exam_scopes WHERE scope_key=%s AND subject=%s",
                    (key, subject),
                ).fetchone()
            ):
                raise ValueError("Policy scope is not reviewed for this subject")
        uid = ensure_user(c, payload.username)
        pid = c.execute(
            "INSERT INTO selection_policies(config,reviewer,note) VALUES (%s,%s,%s) RETURNING id",
            (
                Jsonb(payload.config.model_dump(mode="json")),
                payload.reviewer,
                payload.note,
            ),
        ).fetchone()["id"]
        c.execute(
            "INSERT INTO selection_policy_assignments(user_id,policy_id) VALUES (%s,%s)",
            (uid, pid),
        )
        return {"policy_id": pid}

    return operation(save)


@router.get("/selection/policies", dependencies=[Depends(require_reviewer)])
def policy_history():
    return operation(
        lambda c: {
            "policies": c.execute(
                "SELECT p.*,a.user_id,a.assigned_at FROM selection_policies p JOIN selection_policy_assignments a ON a.policy_id=p.id ORDER BY a.id"
            ).fetchall()
        }
    )


@router.get("/selection/runs/{paper_id}", dependencies=[Depends(require_reviewer)])
def internal_selection_run(paper_id: int):
    def read(c):
        row = c.execute(
            "SELECT * FROM selection_runs WHERE paper_id=%s", (paper_id,)
        ).fetchone()
        if not row:
            raise LookupError("Selection trace not found")
        return row

    return operation(read)


@router.get("/papers/{paper_id}/analysis")
def student_selection_analysis(paper_id: int, username: str = Depends(require_auth)):
    def read(c, u):
        service.owned_paper(c, u, paper_id)
        if not c.execute(
            "SELECT 1 FROM attempts_v2 WHERE paper_id=%s", (paper_id,)
        ).fetchone():
            raise ValueError("Selection analysis is available after submission only")
        row = c.execute(
            "SELECT * FROM selection_runs WHERE paper_id=%s", (paper_id,)
        ).fetchone()
        if not row:
            raise LookupError("Historical selection trace was not recorded")
        from app.selection import summary

        return summary(row)

    return operation(read, username)


class DependencyRegistration(StrictModel):
    subject: Subject
    knowledge_tag: str = Field(min_length=1, max_length=300)
    prerequisite_tag: str = Field(min_length=1, max_length=300)
    source_unit_id: StrictInt = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=2000)
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)


@router.post("/sources/dependencies", dependencies=[Depends(require_reviewer)])
def knowledge_dependency(payload: DependencyRegistration):
    def write(c):
        row = c.execute(
            "SELECT u.text,u.verified,d.subject FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=%s",
            (payload.source_unit_id,),
        ).fetchone()
        if (
            not row
            or not row["verified"]
            or row["subject"] != payload.subject
            or payload.excerpt not in row["text"]
        ):
            raise ValueError("Dependency requires verified textbook evidence")
        tags = {
            r["tag"]
            for r in c.execute(
                "SELECT tag FROM knowledge_links WHERE unit_id=%s",
                (payload.source_unit_id,),
            ).fetchall()
        }
        if not {payload.knowledge_tag, payload.prerequisite_tag}.issubset(tags):
            raise ValueError("Both knowledge points must be linked to the source")
        cycle = c.execute(
            "WITH RECURSIVE path(tag) AS (SELECT prerequisite_tag FROM knowledge_dependencies WHERE subject=%s AND knowledge_tag=%s UNION SELECT k.prerequisite_tag FROM knowledge_dependencies k JOIN path p ON p.tag=k.knowledge_tag WHERE k.subject=%s) SELECT 1 FROM path WHERE tag=%s",
            (
                payload.subject,
                payload.prerequisite_tag,
                payload.subject,
                payload.knowledge_tag,
            ),
        ).fetchone()
        if cycle or payload.knowledge_tag == payload.prerequisite_tag:
            raise ValueError("Knowledge dependency must be acyclic")
        c.execute(
            "INSERT INTO knowledge_dependencies(subject,knowledge_tag,prerequisite_tag,source_unit_id,reviewer,note) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                payload.subject,
                payload.knowledge_tag,
                payload.prerequisite_tag,
                payload.source_unit_id,
                payload.reviewer,
                payload.note,
            ),
        )
        return {"linked": True}

    return operation(write)


@router.get("/history")
def unified_read_only_history(username: str = Depends(require_auth)):
    def read(c, u):
        legacy = [
            {**r, "mode": "legacy_read_only"}
            for r in c.execute(
                "SELECT p.id AS paper_id,p.paper_date AS date,t.score,t.submitted_at FROM papers p JOIN attempts t ON t.paper_id=p.id WHERE p.user_id=%s ORDER BY p.paper_date DESC LIMIT 100",
                (u,),
            ).fetchall()
        ]
        recent = c.execute(
            "SELECT p.id AS paper_id,p.paper_date,t.id AS attempt_id,SUM(a.points) AS graded_points,COUNT(*) FILTER(WHERE a.points IS NULL) AS pending FROM papers_v2 p JOIN attempts_v2 t ON t.paper_id=p.id JOIN answers_v2 a ON a.attempt_id=t.id WHERE p.user_id=%s GROUP BY p.id,t.id ORDER BY p.paper_date DESC LIMIT 100",
            (u,),
        ).fetchall()
        return {
            "legacy": legacy,
            "training": [
                {
                    **r,
                    "mode": "reviewed_28",
                    "score": None if r["pending"] else r["graded_points"],
                }
                for r in recent
            ],
        }

    return operation(read, username)


class OCRReview(StrictModel):
    review_kind: Literal["text", "science", "uncertainty", "exam_style"]
    status: Literal["approved", "rejected"]
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)
    details: dict = Field(default_factory=dict)


@router.post("/sources/{unit_id}/reviews", dependencies=[Depends(require_reviewer)])
def review_ocr_source(unit_id: int, payload: OCRReview):
    from app.ocr_sources import review_unit

    return operation(
        lambda c: review_unit(
            c,
            unit_id,
            payload.review_kind,
            payload.status,
            payload.reviewer,
            payload.note,
            payload.details,
        )
    )


@router.get("/sources/collection-status", dependencies=[Depends(require_reviewer)])
def source_collection_status():
    from app.ocr_reports import collection_status

    return operation(collection_status)


@router.get("/sources/{document_id}/coverage", dependencies=[Depends(require_reviewer)])
def source_document_coverage(document_id: int):
    from app.ocr_reports import document_report

    return operation(lambda c: document_report(c, document_id))


class BibliographyReview(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    authors: str = Field(min_length=1, max_length=300)
    edition: str = Field(min_length=1, max_length=100)
    isbn: str | None = None
    reviewer: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=20, max_length=4000)


@router.post(
    "/sources/{document_id}/bibliography", dependencies=[Depends(require_reviewer)]
)
def review_ocr_bibliography(document_id: int, payload: BibliographyReview):
    from app.ocr_sources import review_bibliography

    data = payload.model_dump()
    return operation(
        lambda c: review_bibliography(
            c,
            document_id,
            {k: data[k] for k in ("title", "authors", "edition", "isbn")},
            payload.reviewer,
            payload.note,
        )
    )
