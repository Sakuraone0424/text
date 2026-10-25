from datetime import timedelta
import pytest
import psycopg
from app import quiz_v2 as q
from app.quiz_service import (
    ensure_user,
    get_or_create_today_paper,
    submit_paper,
    today_cn,
)


def test_no_legacy_fallback_or_partial_paper(db, blueprint):
    from migrate import seed_legacy

    seed_legacy(db)
    uid = ensure_user(db, "shortage")
    with pytest.raises(q.BankShortage) as exc:
        q.create_paper(db, uid, blueprint)
    assert len(exc.value.shortages) == 10
    assert db.execute("SELECT COUNT(*) AS n FROM papers_v2").fetchone()["n"] == 0
    with pytest.raises(ValueError, match="审核发布"):
        get_or_create_today_paper(db, uid)


def test_audit_gate_immutability_and_revocation(db, question_factory, passing_audit):
    question = question_factory()
    qid = q.create_question(db, question)["id"]
    with pytest.raises(ValueError):
        q.publish_question(db, qid)
    with db.transaction():
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE reviewed_questions SET status='published' WHERE id=%s",
                    (qid,),
                )
    q.review_question(db, qid, passing_audit)
    q.publish_question(db, qid)
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute(
                'UPDATE reviewed_questions SET content=content || \'{"answer":"B"}\' WHERE id=%s',
                (qid,),
            )
    bad = passing_audit.model_copy(deep=True)
    bad.checks["scientific_correctness"].passed = False
    assert not q.review_question(db, qid, bad)["passed"]
    assert (
        db.execute(
            "SELECT status FROM reviewed_questions WHERE id=%s", (qid,)
        ).fetchone()["status"]
        == "withdrawn"
    )
    with pytest.raises(ValueError):
        q.publish_question(db, qid)


def test_duplicate_review_and_publish_race_recheck(db, question_factory, passing_audit):
    question = question_factory()
    a = q.create_question(db, question)["id"]
    b = q.create_question(db, question)["id"]
    assert q.review_question(db, a, passing_audit)["passed"]
    assert q.review_question(db, b, passing_audit)["passed"]
    q.publish_question(db, a)
    with pytest.raises(ValueError, match="Publication blocked"):
        q.publish_question(db, b)
    assert not q.review_question(db, b, passing_audit)["passed"]


def test_snapshots_no_reuse_and_owner_isolation(db, bank, blueprint):
    uid = ensure_user(db, "owner")
    stranger = ensure_user(db, "stranger")
    paper = q.create_paper(db, uid, blueprint)
    from app.training_sessions import start

    start(db, uid, paper["paper_id"])
    paper = q.load_paper(db, uid, paper["paper_id"])
    assert paper["count"] == 28
    assert q.create_paper(db, uid, blueprint)["questions"] == paper["questions"]
    assert all(
        "answer" not in i and "evidence" not in i and "rubric" not in i
        for i in paper["questions"]
    )
    with pytest.raises(LookupError):
        q.load_paper(db, stranger, paper["paper_id"])
    with pytest.raises(q.BankShortage):
        q.create_paper(db, uid, blueprint, today_cn() + timedelta(days=1))
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute(
                "UPDATE paper_items_v2 SET snapshot='{}' WHERE paper_id=%s",
                (paper["paper_id"],),
            )
    db.execute(
        "UPDATE reviewed_questions SET status='withdrawn' WHERE id=%s", (bank[0],)
    )
    assert q.load_paper(db, uid, paper["paper_id"])["questions"] == paper["questions"]


def test_mixed_grading_pending_final_and_reviews(db, bank, blueprint):
    uid = ensure_user(db, "grading")
    paper = q.create_paper(db, uid, blueprint)
    from app.training_sessions import start

    start(db, uid, paper["paper_id"])
    paper = q.load_paper(db, uid, paper["paper_id"])
    answers = {
        i["id"]: (
            "A" if i["question_type"] == "single_choice" else " fixture solution "
        )
        for i in paper["questions"]
    }
    with pytest.raises(ValueError):
        q.submit(db, uid, paper["paper_id"], {999999: "A"})
    result = q.submit(db, uid, paper["paper_id"], answers)
    assert (
        result["status"] == "pending_review"
        and result["score"] is None
        and result["pending"] == 16
    )
    assert result["graded_points"] == 30
    with pytest.raises(ValueError):
        q.submit(db, uid, paper["paper_id"], answers)
    subjective = [
        i
        for i in paper["questions"]
        if i["question_type"] in ("term", "short_answer", "essay")
    ]
    with pytest.raises(ValueError):
        q.grade(db, paper["paper_id"], subjective[0]["id"], [101], "bad", "reviewer")
    for i in subjective:
        result = q.grade(
            db,
            paper["paper_id"],
            i["id"],
            [1],
            "Rubric criterion partially satisfied",
            "reviewer",
        )
    assert (
        result["score"] == 46 and result["max_points"] == 150 and result["pending"] == 0
    )
    assert db.execute("SELECT COUNT(*) AS n FROM fsrs_cards").fetchone()["n"] == 16
    with pytest.raises(ValueError):
        q.grade(
            db, paper["paper_id"], subjective[0]["id"], [2], "overwrite", "reviewer"
        )
    assert (
        db.execute("SELECT finalized_at FROM attempts_v2").fetchone()["finalized_at"]
        is not None
    )


def test_legacy_export_idempotent_and_unapproved_excluded(
    db, question_factory, passing_audit
):
    from migrate import seed_legacy

    seed_legacy(db)
    for subject in ["biochemistry", "cell_biology"]:
        for _ in range(30):
            qid = q.create_question(db, question_factory(subject))["id"]
            q.review_question(db, qid, passing_audit)
            first = q.export_legacy(db, qid)
            assert q.export_legacy(db, qid) == first
    uid = ensure_user(db, "legacy-approved")
    paper = get_or_create_today_paper(db, uid)
    assert paper["count"] == 60 and len(paper["questions"]) == 60
    approved = {
        r["question_id"]
        for r in db.execute(
            "SELECT question_id FROM legacy_question_publications"
        ).fetchall()
    }
    assert {r["id"] for r in paper["questions"]} == approved
    result = submit_paper(
        db, uid, {r["id"]: "A" for r in paper["questions"]}, paper["paper_id"]
    )
    assert result["total"] == 60 and result["score"] == 60


def test_audits_are_append_only(db, question_factory, passing_audit):
    qid = q.create_question(db, question_factory())["id"]
    q.review_question(db, qid, passing_audit)
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute("UPDATE question_audits SET passed=false")
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute("DELETE FROM question_audits")


def test_completed_grade_cannot_be_overwritten(db, bank, blueprint):
    uid = ensure_user(db, "immutable-grade")
    paper = q.create_paper(db, uid, blueprint)
    from app.training_sessions import start

    start(db, uid, paper["paper_id"])
    paper = q.load_paper(db, uid, paper["paper_id"])
    answers = {
        i["id"]: "A" if i["question_type"] == "single_choice" else "fixture solution"
        for i in paper["questions"]
    }
    q.submit(db, uid, paper["paper_id"], answers)
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute("UPDATE answers_v2 SET points=0 WHERE points IS NOT NULL")
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute("DELETE FROM answers_v2")


def test_paper_identity_cannot_change(db, bank, blueprint):
    uid = ensure_user(db, "paper-identity")
    paper = q.create_paper(db, uid, blueprint)
    from app.training_sessions import start

    start(db, uid, paper["paper_id"])
    paper = q.load_paper(db, uid, paper["paper_id"])
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute(
                "UPDATE papers_v2 SET blueprint='{}' WHERE id=%s", (paper["paper_id"],)
            )
