"""The hand-written sample data of the page tests must have the shape the real code returns. A field renamed or dropped on the
server makes these fail, instead of the page tests passing against data the server no longer sends.

For each payload the page tests fake, the real function is called on synthetic rows and the two are compared field by field: every
field the fake has must exist in the real answer (the fake may not invent one), and the fields the pages read must be in both."""
from datetime import timedelta

import pytest

from app import auto_generation, auto_quality, chapters, learning_stats as stats, manual_grading, mistake_book, paper_review, practice, practice_center
from app.common import ensure_user
from stats_helpers import item, paper, question
from test_learning_stats import at, today
import test_drill_ui as drill_fakes
import test_pages_ui as page_fakes


@pytest.fixture
def uid(db):
    return ensure_user(db, "contract-student")


@pytest.fixture
def world(db, uid, monkeypatch):
    """A learner with stock, a graded paper (one right, one wrong, one written answer waiting), cards and a practice set."""
    monkeypatch.setattr(chapters, "textbook_chapters", lambda conn, subject: [("第六章 核酸", 6, "核酸"), ("第二章 细胞膜", 2, "细胞膜")])
    monkeypatch.setattr(auto_quality, "ready", lambda conn, qid: True)
    monkeypatch.setattr(auto_generation, "pinned_science_evidence", lambda content: True)
    monkeypatch.setattr(practice_center, "reserve_of", lambda subject, kind: 0)
    for n in range(12):
        qid = question(db, "biochemistry", "single_choice", 3, "6 核酸", stem=f"契约题 {n}", options={"A": "a", "B": "b"}, answer="A")
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
    t = today(db)
    made = paper(db, uid, t - timedelta(days=1), [item("biochemistry", "single_choice", 3, 3, "6 核酸"), item("biochemistry", "single_choice", 3, 0, "6 核酸"),
                                                  item("cell_biology", "short_answer", 15, None, "2 膜"), item("cell_biology", "term", 5, 2.5, "2 膜")], when=at(t - timedelta(days=1)))
    wrong = made["question_ids"][1]
    db.execute("INSERT INTO fsrs_cards(user_id,question_id,card,due,status) VALUES (%s,%s,'{}',clock_timestamp(),'mistake')", (uid, wrong))
    started = practice.start(db, uid, "quick", size=3)
    answered = practice.answer(db, uid, started["set_id"], started["questions"][0]["id"], {"text": "B"}, 4)
    return dict(paper=made, wrong=wrong, set=started, answer=answered)


def extra(fake, real, where):
    """Fields the fake has that the real answer does not."""
    return [f"{where}.{k}" for k in fake if k not in real]


def first(rows):
    return rows[0] if rows else {}


def test_practice_overview(db, uid, world):
    real, fake = practice_center.overview(db, uid), page_fakes.OVERVIEW
    problems = extra(fake, real, "overview")
    for mode in ("weak", "chapter", "type", "retry", "quick", "year"):
        problems += extra(fake[mode], real[mode], mode)
    problems += extra(first(fake["type"]["types"]), first(real["type"]["types"]), "type.types[]")
    problems += extra(first(fake["chapter"]["chapters"]), first(real["chapter"]["chapters"]), "chapter.chapters[]")
    assert problems == [], problems
    for field in ("available", "ready", "size", "reason", "short_by"):          # what the cards read
        assert field in real["quick"]


def test_recent_trainings(db, uid, world):
    real = {"items": paper_review.recent(db, uid, 5)}
    kinds = {r["kind"]: r for r in real["items"]}
    assert set(kinds) == {"paper", "practice"}
    problems = []
    for row in page_fakes.RECENT["items"]:
        problems += extra(row, kinds[row["kind"]], "recent." + row["kind"])
    assert problems == [], problems


def test_mistake_book_and_detail(db, uid, world):
    real, fake = mistake_book.book(db, uid), page_fakes.BOOK
    problems = extra(fake, real, "book") + extra(first(fake["items"]), first(real["items"]), "book.items[]")
    problems += extra(first(fake["groups"]), first(real["groups"]), "book.groups[]") + extra(first(fake["chapters"]), first(real["chapters"]), "book.chapters[]")
    problems += extra(first(fake["types"]), first(real["types"]), "book.types[]")
    detail = mistake_book.detail(db, uid, world["wrong"])
    problems += extra(page_fakes.DETAIL, detail, "detail") + extra(first(page_fakes.DETAIL["history"]), first(detail["history"]), "detail.history[]")
    assert problems == [], problems


def test_paper_review_summary_and_one_question(db, uid, world):
    real = paper_review.summary(db, uid, world["paper"]["paper_id"])
    problems = extra(page_fakes.REVIEW, real, "review") + extra(page_fakes.REVIEW_PENDING, real, "review(pending)")
    problems += extra(first(page_fakes.REVIEW["items"]), first(real["items"]), "review.items[]")
    pending_fake = next(i for i in page_fakes.REVIEW_PENDING["items"] if i["status"] == "pending")
    pending_real = next(i for i in real["items"] if i["status"] == "pending")
    problems += extra(pending_fake, pending_real, "review.items[pending]")
    problems += extra(first(page_fakes.REVIEW["type_rates"]), first(real["type_rates"]), "review.type_rates[]")
    one = paper_review.detail(db, uid, world["paper"]["paper_id"], world["wrong"])
    ignore = {"history", "due", "version", "status_card"}          # (the fake reuses the mistake detail as its base; these belong to that page only)
    problems += [p for p in extra(page_fakes.REVIEW_ITEM, one, "review.item") if p.split(".")[-1] not in ignore]
    assert problems == [], problems


def test_history_cards(db, uid, world):
    real = stats.history(db, uid)
    problems = extra(page_fakes.HISTORY, real, "history") + extra(first(page_fakes.HISTORY["papers"]), first(real["papers"]), "history.papers[]")
    assert problems == [], problems


def test_practice_set_question_and_answer(db, uid, world):
    problems = extra({k: v for k, v in drill_fakes.SET.items() if k != "questions"}, world["set"], "set")
    fake_single = next(q for q in drill_fakes.SET["questions"] if q["question_type"] == "single_choice")
    problems += extra(fake_single, world["set"]["questions"][0], "set.questions[]")
    problems += extra(drill_fakes.ANSWER, world["answer"], "answer")
    assert problems == [], problems


def test_manual_grading_form(db, uid, world):
    real = manual_grading.view(db, uid, world["paper"]["paper_id"], world["wrong"])
    fake = {"paper_id", "question_id", "question_type", "stem", "options", "your_answer", "reference", "criteria", "criterion_points", "points", "max_points",
            "graded_by", "label", "note", "ai_original", "released"}          # the fields the sheet's fake (test_drill_ui) is built from
    assert sorted(fake - set(real)) == []
    assert {"description", "points"} <= set(first(real["criteria"]))


def test_a_single_choice_mistake_says_which_option_was_picked_and_which_is_right(db, uid, world):
    detail = mistake_book.detail(db, uid, world["wrong"])
    assert detail["correct_choice"] == "A" and "your_choice" in detail


def test_a_graded_written_answer_in_the_review_carries_its_scored_criteria_where_the_page_reads_them(db, uid, world):
    term = world["paper"]["question_ids"][3]
    db.execute("ALTER TABLE answers_v2 DISABLE TRIGGER USER")
    from psycopg.types.json import Jsonb

    db.execute("UPDATE answers_v2 SET feedback=%s WHERE question_id=%s", (Jsonb({"criteria": [{"description": "得分点", "points": 2.5, "max_points": 5}], "criterion_reasons": ["只答了一半"]}), term))
    db.execute("ALTER TABLE answers_v2 ENABLE TRIGGER USER")
    one = paper_review.detail(db, uid, world["paper"]["paper_id"], term)
    assert one["scores"]["criteria"][0]["points"] == 2.5 and one["scores"]["criterion_reasons"] == ["只答了一半"]
    untouched = paper_review.detail(db, uid, world["paper"]["paper_id"], world["wrong"])
    assert untouched["scores"] is None
