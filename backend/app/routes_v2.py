"""Explicit version boundary and separate operator authorization."""
import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, StrictInt

from app.auth import require_auth
from app.db import get_db
from app.question_quality import Audit, Blueprint, Question, StrictModel
from app.quiz_service import ensure_user
from app import quiz_v2 as service

router = APIRouter(prefix='/v2', tags=['reviewed-28'])


def require_reviewer(request: Request):
    configured = os.environ.get('QUIZ_REVIEW_API_KEY', '')
    if not configured:
        raise HTTPException(503, 'Review operations are disabled: QUIZ_REVIEW_API_KEY is not configured')
    if not hmac.compare_digest(request.headers.get('x-review-api-key', ''), configured):
        raise HTTPException(403, 'Reviewer authorization required')


def operation(fn, username=None):
    try:
        with get_db() as conn:
            return fn(conn, ensure_user(conn, username)) if username is not None else fn(conn)
    except service.BankShortage as exc:
        raise HTTPException(409, {'code': 'insufficient_reviewed_bank', 'shortages': exc.shortages}) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/questions', dependencies=[Depends(require_reviewer)])
def draft(question: Question):
    return operation(lambda c: service.create_question(c, question))


@router.get('/questions/{question_id}', dependencies=[Depends(require_reviewer)])
def inspect_question(question_id: int):
    def read(c):
        question = c.execute('SELECT * FROM reviewed_questions WHERE id=%s', (question_id,)).fetchone()
        if not question:
            raise LookupError('Question not found')
        question['audits'] = c.execute('SELECT * FROM question_audits WHERE question_id=%s ORDER BY id', (question_id,)).fetchall()
        return question
    return operation(read)


@router.post('/questions/{question_id}/audit', dependencies=[Depends(require_reviewer)])
def audit(question_id: int, payload: Audit):
    return operation(lambda c: service.review_question(c, question_id, payload))


@router.post('/questions/{question_id}/publish', dependencies=[Depends(require_reviewer)])
def publish(question_id: int):
    return operation(lambda c: service.publish_question(c, question_id))


@router.post('/questions/{question_id}/withdraw', dependencies=[Depends(require_reviewer)])
def withdraw(question_id: int):
    def change(c):
        service.lock_bank(c)
        row = c.execute("UPDATE reviewed_questions SET status='withdrawn' WHERE id=%s RETURNING id,status", (question_id,)).fetchone()
        if not row:
            raise LookupError('Question not found')
        return row
    return operation(change)


@router.post('/papers')
def paper(blueprint: Blueprint, username: str = Depends(require_auth)):
    return operation(lambda c, u: service.create_paper(c, u, blueprint), username)


@router.get('/papers/{paper_id}')
def read_paper(paper_id: int, username: str = Depends(require_auth)):
    return operation(lambda c, u: service.load_paper(c, u, paper_id), username)


class Submission(StrictModel):
    answers: dict[int, str] = Field(min_length=28, max_length=28)


@router.post('/papers/{paper_id}/submit')
def submit(paper_id: int, payload: Submission, username: str = Depends(require_auth)):
    return operation(lambda c, u: service.submit(c, u, paper_id, payload.answers), username)


@router.get('/papers/{paper_id}/result')
def result(paper_id: int, username: str = Depends(require_auth)):
    return operation(lambda c, u: service.result(c, u, paper_id), username)


class Grade(StrictModel):
    criterion_points: list[StrictInt] = Field(min_length=1, max_length=50)
    feedback: str = Field(min_length=1, max_length=5000)
    reviewer: str = Field(min_length=1, max_length=200)


@router.post('/papers/{paper_id}/answers/{question_id}/grade', dependencies=[Depends(require_reviewer)])
def grade(paper_id: int, question_id: int, payload: Grade):
    return operation(lambda c: service.grade(c, paper_id, question_id, payload.criterion_points, payload.feedback, payload.reviewer))


@router.get('/review/due')
def reviews(username: str = Depends(require_auth)):
    return operation(lambda c, u: {'items': service.due_reviews(c, u)}, username)


@router.post('/review/{attempt_id}/{question_id}/{stage}/complete')
def complete_review(attempt_id: int, question_id: int, stage: int, username: str = Depends(require_auth)):
    def complete(c, u):
        row = c.execute(
            'UPDATE reviews_v2 r SET completed_at=NOW() FROM attempts_v2 a JOIN papers_v2 p ON p.id=a.paper_id '
            'WHERE r.attempt_id=a.id AND p.user_id=%s AND r.attempt_id=%s AND r.question_id=%s '
            'AND r.review_stage=%s AND r.due_date<=%s RETURNING r.question_id',
            (u, attempt_id, question_id, stage, service.today_cn()),
        ).fetchone()
        if not row:
            raise LookupError('Due review not found')
        return {'completed': True}
    return operation(complete, username)


@router.post('/questions/{question_id}/export-legacy', dependencies=[Depends(require_reviewer)])
def export_legacy(question_id: int):
    return operation(lambda c: service.export_legacy(c, question_id))


@router.get('/quality/report', dependencies=[Depends(require_reviewer)])
def quality_report():
    def report(c):
        groups = c.execute(
            "SELECT content->>'subject' AS subject, content->>'question_type' AS question_type, status, COUNT(*) AS count "
            'FROM reviewed_questions GROUP BY 1,2,3 ORDER BY 1,2,3'
        ).fetchall()
        counts = c.execute('SELECT (SELECT COUNT(*) FROM questions) AS legacy_questions, '
                           '(SELECT COUNT(*) FROM legacy_question_publications lp JOIN reviewed_questions q '
                           "ON q.id=lp.reviewed_question_id WHERE q.status='published') AS approved_legacy_questions, "
                           '(SELECT COUNT(*) FROM question_audits WHERE NOT passed) AS failed_audits').fetchone()
        return {**counts, 'groups': groups, 'duplicate_threshold': 0.85,
                'scientific_verification': 'Expert textbook verification required; not established by automatic checks'}
    return operation(report)
