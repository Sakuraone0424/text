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
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set TEST_DATABASE_URL to an explicitly selected development PostgreSQL database')
    with psycopg.connect(url, row_factory=dict_row) as conn:
        with conn.transaction(force_rollback=True):
            schema = 'quiz_test_' + uuid.uuid4().hex
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
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
    monkeypatch.setattr(main, 'get_db', test_db)
    monkeypatch.setattr(routes_v2, 'get_db', test_db)
    monkeypatch.setenv('QUIZ_API_KEY', 'learner-test-key')
    monkeypatch.setenv('QUIZ_REVIEW_API_KEY', 'operator-test-key')
    monkeypatch.setenv('QUIZ_USERNAME', 'test-owner')
    monkeypatch.setenv('APP_PASSWORD', 'login-test-password')
    monkeypatch.setenv('SESSION_SECRET', 'test-session-secret')
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def question_factory():
    def make(subject='biochemistry', kind='single_choice', stem=None):
        # All evidence in tests is synthetic; never seed or publish these fixtures.
        content = dict(subject=subject, question_type=kind,
                       stem=stem or ('TEST ONLY ' + uuid.uuid4().hex + (' ____' if kind == 'fill_blank' else '')),
                       options=dict(zip('ABCD', ['alpha','beta','gamma','delta'])) if kind == 'single_choice' else {},
                       answer='A' if kind == 'single_choice' else 'fixture solution',
                       accepted_answers=['fixture solution'] if kind == 'fill_blank' else [],
                       evidence=[dict(textbook='SYNTHETIC TEST FIXTURE', authors='Test author', edition='Test edition',
                                      isbn='9780306406157', chapter='Test chapter', locator='Test page 1', excerpt='Synthetic evidence, not a textbook claim')],
                       rubric=[dict(description='Synthetic scoring criterion', points=2)], max_points=2)
        return Question.model_validate(content)
    return make


@pytest.fixture
def passing_audit():
    return Audit(reviewer='test-reviewer', checks={axis: {'passed': True, 'rationale': 'Synthetic fixture reviewed for this test only'} for axis in AXES},
                 evidence_verification='Synthetic test evidence verified for automated test purposes only')


@pytest.fixture
def blueprint():
    # An example fixture, not a claim about the official USTC specification.
    return Blueprint(specification='synthetic-test-spec', quotas={s: dict(single_choice=4, fill_blank=4, term=2, short_answer=2, essay=2)
                                                                  for s in ['biochemistry','cell_biology']})


@pytest.fixture
def bank(db, question_factory, passing_audit, blueprint):
    from app.quiz_v2 import create_question, review_question, publish_question
    ids = []
    for subject, quotas in blueprint.quotas.items():
        for kind, count in quotas.items():
            for _ in range(count):
                qid = create_question(db, question_factory(subject,kind))['id']
                assert review_question(db,qid,passing_audit)['passed']
                publish_question(db,qid)
                ids.append(qid)
    return ids
