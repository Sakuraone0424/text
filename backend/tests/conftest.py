"""Opt-in database tests: a new schema inside a rollback-only transaction.

No existing schema is migrated, dropped, truncated, or used for fixtures.
"""

import os
import uuid
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient

from app.question_quality import AXES, Audit, Blueprint, Question
from migrate import apply_migrations


@pytest.fixture
def raw_db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set TEST_DATABASE_URL to an explicitly selected development PostgreSQL database"
        )
    with psycopg.connect(url, row_factory=dict_row) as conn:
        with conn.transaction(force_rollback=True):
            schema = "quiz_test_" + uuid.uuid4().hex
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
            )
            yield conn


@pytest.fixture
def db(raw_db):
    apply_migrations(raw_db)
    return raw_db


@pytest.fixture
def client(db, monkeypatch):
    from app.main import app
    from app import main, routes_v2

    @contextmanager
    def test_db():
        with db.transaction():
            yield db

    monkeypatch.setattr(main, "get_db", test_db)
    monkeypatch.setattr(routes_v2, "get_db", test_db)
    monkeypatch.setenv("QUIZ_API_KEY", "learner-test-key")
    monkeypatch.setenv("QUIZ_REVIEW_API_KEY", "operator-test-key")
    monkeypatch.setenv("QUIZ_USERNAME", "test-owner")
    monkeypatch.setenv("APP_PASSWORD", "login-test-password")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def question_factory(db):
    from app.sources import sha
    from psycopg.types.json import Jsonb

    units = {}
    for subject in ("biochemistry", "cell_biology"):
        original = b"SYNTHETIC TEST ORIGINAL - NOT A TEXTBOOK"
        doc = db.execute(
            "INSERT INTO source_documents(file_id,title,edition,subject,sha256,original_sha256,original_content,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                subject,
                "SYNTHETIC TEST FIXTURE",
                "Test edition",
                subject,
                sha(original),
                sha(original),
                original,
                Jsonb({"authors": "Test author", "isbn": "9780306406157"}),
            ),
        ).fetchone()["id"]
        units[subject] = db.execute(
            "INSERT INTO source_units(document_id,ordinal,chapter,section,text,verified,reviewer,verification_note) VALUES (%s,1,'Test chapter','','Synthetic evidence, not a textbook claim',true,'test','Synthetic verification fixture only') RETURNING id",
            (doc,),
        ).fetchone()["id"]
    styles = {}
    for subject, unit in units.items():
        for kind in ("single_choice", "fill_blank", "term", "short_answer", "essay"):
            styles[(subject, kind)] = db.execute(
                "INSERT INTO exam_records(source_unit_id,original_question_id,subject,year,question_type,stem,category,answer_kind,original_verified,canonical_key) VALUES (%s,%s,%s,2025,%s,'Synthetic exam fixture','basic_concept','missing',true,'synthetic') RETURNING id",
                (unit, subject + kind, subject, kind),
            ).fetchone()["id"]
    from app.training_spec import POINTS

    def make(subject="biochemistry", kind="single_choice", stem=None):
        # All evidence in tests is synthetic; never seed or publish these fixtures.
        content = dict(
            style_reference_ids=[styles[(subject, kind)]],
            subject=subject,
            question_type=kind,
            stem=stem
            or (
                "TEST ONLY "
                + uuid.uuid4().hex
                + (" ____" if kind == "fill_blank" else "")
            ),
            options=dict(zip("ABCD", ["alpha", "beta", "gamma", "delta"]))
            if kind == "single_choice"
            else {},
            answer="A" if kind == "single_choice" else "fixture solution",
            accepted_answers=["fixture solution"] if kind == "fill_blank" else [],
            evidence=[
                dict(
                    source_unit_id=units[subject],
                    textbook="SYNTHETIC TEST FIXTURE",
                    authors="Test author",
                    edition="Test edition",
                    isbn="9780306406157",
                    chapter="Test chapter",
                    locator="UNIT:1",
                    excerpt="Synthetic evidence, not a textbook claim",
                )
            ],
            rubric=[
                dict(description="Synthetic scoring criterion", points=POINTS[kind])
            ],
            max_points=POINTS[kind],
        )
        return Question.model_validate(content)

    return make


@pytest.fixture
def passing_audit():
    return Audit(
        reviewer="test-reviewer",
        checks={
            axis: {
                "passed": True,
                "rationale": "Synthetic fixture reviewed for this test only",
            }
            for axis in AXES
        },
        evidence_verification="Synthetic test evidence verified for automated test purposes only",
    )


@pytest.fixture
def blueprint():
    # An example fixture, not a claim about the official USTC specification.
    return Blueprint(
        specification="synthetic-test-spec",
        quotas={
            s: dict(single_choice=3, fill_blank=3, term=4, short_answer=3, essay=1)
            for s in ["biochemistry", "cell_biology"]
        },
    )


@pytest.fixture
def bank(db, question_factory, passing_audit, blueprint):
    from app.quiz_v2 import create_question, review_question, publish_question

    ids = []
    for subject, quotas in blueprint.quotas.items():
        for kind, count in quotas.items():
            for _ in range(count):
                qid = create_question(db, question_factory(subject, kind))["id"]
                assert review_question(db, qid, passing_audit)["passed"]
                publish_question(db, qid)
                ids.append(qid)
    return ids


@pytest.fixture
def running(db, bank, blueprint):
    from app import quiz_v2 as q, training_sessions as sessions
    from app.quiz_service import ensure_user

    uid = ensure_user(db, "test-owner")
    paper = q.create_paper(db, uid, blueprint)
    sessions.start(db, uid, paper["paper_id"])
    return uid, q.load_paper(db, uid, paper["paper_id"])
