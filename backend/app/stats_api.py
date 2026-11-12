"""Read-only statistics, mistake list and short practice endpoints (numbers only, per user)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, StrictInt

from app import learning_stats as stats, practice
from app.auth import require_auth
from app.question_quality import Points, StrictModel
from app.routes_v2 import operation

router = APIRouter(prefix="/v2", tags=["learning-stats"])


@router.get("/stats/overview")
def overview(username: str = Depends(require_auth)):
    return operation(stats.overview, username)


@router.get("/stats/history")
def history(kind: str | None = Query(default=None, pattern="^(objective|subjective|mixed)$"), username: str = Depends(require_auth)):
    return operation(lambda c, u: stats.history(c, u, kind), username)


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
    mode: str = Field(pattern="^(chapter|weak|type|retry|quick|year)$")
    subject: str | None = Field(default=None, pattern="^(biochemistry|cell_biology)$")
    chapter: str | None = Field(default=None, max_length=300)
    question_type: str | None = Field(default=None, pattern="^(single_choice|fill_blank|term|short_answer|essay)$")
    year: StrictInt | None = Field(default=None, ge=1990, le=2100)
    seconds: StrictInt | None = Field(default=None, ge=60, le=7200)           # optional time limit (quick practice)
    size: StrictInt = Field(default=10, ge=1, le=practice.MAX_SET)


@router.post("/practice")
def practice_start(data: PracticeStart, username: str = Depends(require_auth)):
    return operation(lambda c, u: practice.start(c, u, data.mode, data.subject, data.chapter, data.size, data.question_type, data.year, data.seconds), username)


@router.get("/practice/overview")
def practice_overview(username: str = Depends(require_auth)):
    from app import practice_center

    return operation(practice_center.overview, username)


@router.get("/training/recent")
def recent_training(limit: int = Query(default=5, ge=1, le=30), username: str = Depends(require_auth)):
    from app import paper_review

    return operation(lambda c, u: {"items": paper_review.recent(c, u, limit)}, username)


@router.get("/mistakes/book")
def mistake_book(username: str = Depends(require_auth)):
    from app import mistake_book as book

    return operation(book.book, username)


@router.get("/mistakes/{question_id}")
def mistake_detail(question_id: int, username: str = Depends(require_auth)):
    from app import mistake_book as book

    return operation(lambda c, u: book.detail(c, u, question_id), username)


@router.get("/papers/{paper_id}/review")
def paper_review_summary(paper_id: int, username: str = Depends(require_auth)):
    from app import paper_review

    return operation(lambda c, u: paper_review.summary(c, u, paper_id), username)


@router.get("/papers/{paper_id}/review/{question_id}")
def paper_review_detail(paper_id: int, question_id: int, username: str = Depends(require_auth)):
    from app import paper_review

    return operation(lambda c, u: paper_review.detail(c, u, paper_id, question_id), username)


@router.get("/practice/{set_id}/review")
def practice_review_summary(set_id: int, username: str = Depends(require_auth)):
    from app import paper_review

    return operation(lambda c, u: paper_review.practice_summary(c, u, set_id), username)


@router.get("/practice/{set_id}/review/{question_id}")
def practice_review_detail(set_id: int, question_id: int, username: str = Depends(require_auth)):
    from app import paper_review

    return operation(lambda c, u: paper_review.practice_detail(c, u, set_id, question_id), username)


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


class ManualScore(StrictModel):
    criterion_points: list[Points] = Field(min_length=1, max_length=50)
    note: str | None = Field(default=None, max_length=500)


@router.get("/papers/{paper_id}/manual/{question_id}")
def manual_view(paper_id: int, question_id: int, username: str = Depends(require_auth)):
    from app import manual_grading

    return operation(lambda c, u: manual_grading.view(c, u, paper_id, question_id), username)


@router.put("/papers/{paper_id}/manual/{question_id}")
def manual_save(paper_id: int, question_id: int, data: ManualScore, username: str = Depends(require_auth)):
    from app import manual_grading

    return operation(lambda c, u: manual_grading.save(c, u, paper_id, question_id, list(data.criterion_points), data.note), username)


@router.post("/papers/{paper_id}/manual/{question_id}/ai")
def manual_to_ai(paper_id: int, question_id: int, username: str = Depends(require_auth)):
    from app import manual_grading

    return operation(lambda c, u: manual_grading.hand_to_ai(c, u, paper_id, question_id), username)
