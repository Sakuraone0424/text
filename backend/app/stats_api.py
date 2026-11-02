"""Read-only statistics, mistake list and short practice endpoints (numbers only, per user)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, StrictInt

from app import learning_stats as stats, practice
from app.auth import require_auth
from app.question_quality import StrictModel
from app.routes_v2 import operation

router = APIRouter(prefix="/v2", tags=["learning-stats"])


@router.get("/stats/overview")
def overview(username: str = Depends(require_auth)):
    return operation(stats.overview, username)


@router.get("/stats/profile")
def profile(username: str = Depends(require_auth)):
    return operation(stats.profile, username)


@router.get("/papers/{paper_id}/timing")
def paper_timing(paper_id: int, username: str = Depends(require_auth)):
    return operation(lambda c, u: stats.overtime_types(c, u, paper_id), username)


@router.get("/mistakes")
def mistakes(
    subject: str | None = Query(default=None, pattern="^(biochemistry|cell_biology)$"),
    question_type: str | None = Query(default=None, pattern="^(single_choice|fill_blank|term|short_answer|essay)$"),
    chapter: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    username: str = Depends(require_auth),
):
    return operation(lambda c, u: stats.mistakes(c, u, subject, question_type, chapter, limit, offset), username)


class PracticeStart(StrictModel):
    mode: str = Field(pattern="^(chapter|weak)$")
    subject: str | None = Field(default=None, pattern="^(biochemistry|cell_biology)$")
    chapter: str | None = Field(default=None, max_length=300)
    size: StrictInt = Field(default=10, ge=1, le=practice.MAX_SET)


@router.post("/practice")
def practice_start(data: PracticeStart, username: str = Depends(require_auth)):
    return operation(lambda c, u: practice.start(c, u, data.mode, data.subject, data.chapter, data.size), username)


class PracticeAnswer(StrictModel):
    question_id: StrictInt = Field(ge=1)
    text: str = Field(default="", max_length=2000)
    blanks: list[str] = Field(default_factory=list, max_length=3)
    seconds: StrictInt | None = Field(default=None, ge=0, le=7200)


@router.post("/practice/{set_id}/answer")
def practice_answer(set_id: int, data: PracticeAnswer, username: str = Depends(require_auth)):
    value = {"text": data.text, **({"blanks": data.blanks} if data.blanks else {})}
    return operation(lambda c, u: practice.answer(c, u, set_id, data.question_id, value, data.seconds), username)


@router.get("/practice/{set_id}")
def practice_summary(set_id: int, username: str = Depends(require_auth)):
    return operation(lambda c, u: practice.summary(c, u, set_id), username)
