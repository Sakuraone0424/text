"""Specification xiaohan-60q-300pt-v4: 2 subjects x 5 types, 30 questions / 150 points per subject."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import auto_quality, exam_authoring, training_spec_v2 as v2, training_spec_v4 as v4
from app.question_quality import Blueprint, Question
from spec_helpers import rubric

from test_ai_automation import setup_ai  # noqa: F401  (fixture)


def paper_items():
    items = []
    for subject in v4.SUBJECTS:
        for kind, count in v4.COUNTS.items():
            for _ in range(count):
                items.append(dict(subject=subject, question_type=kind, max_points=v4.POINTS[kind],
                                  rubric=rubric(kind, v4.POINTS[kind])))
    return items


def test_the_numbers_are_the_agreed_ones():
    assert v4.SPECIFICATION == "xiaohan-60q-300pt-v4"
    assert v4.COUNTS == {"single_choice": 10, "fill_blank": 14, "term": 4, "short_answer": 1, "essay": 1}
    assert v4.POINTS == {"single_choice": 4, "fill_blank": 4, "term": 6, "short_answer": 12, "essay": 18}
    assert (v4.QUESTIONS_PER_SUBJECT, v4.POINTS_PER_SUBJECT, v4.TOTAL_QUESTIONS, v4.TOTAL_POINTS) == (30, 150, 60, 300)
    assert v4.DURATION_SECONDS == 3600
    assert sum(v4.COUNTS[k] * v4.POINTS[k] for k in v4.COUNTS) == 150
    assert len(v4.count_required_bank()) == 10  # 2 subjects x 5 types


def test_a_complete_paper_validates_and_each_kind_of_deviation_is_refused():
    items = paper_items()
    assert v4.validate_items(items) is items
    with pytest.raises(ValueError):
        v4.validate_items(items[:-1])
    swapped = deepcopy(items)
    swapped[0]["question_type"] = "term"  # wrong quota
    with pytest.raises(ValueError):
        v4.validate_items(swapped)
    old_points = deepcopy(items)
    old_points[0]["max_points"] = 2
    with pytest.raises(ValueError):
        v4.validate_items(old_points)


@pytest.mark.parametrize("count,ok", [(3, False), (4, True), (5, True), (6, True), (7, False)])
def test_an_essay_needs_four_to_six_scoring_points(count, ok):
    items = paper_items()
    essay = next(i for i in items if i["question_type"] == "essay")
    base, extra = divmod(18, count)
    essay["rubric"] = [{"description": f"p{n}", "points": base + (1 if n < extra else 0)} for n in range(count)]
    if ok:
        v4.validate_items(items)
    else:
        with pytest.raises(ValueError, match="essay"):
            v4.validate_items(items)


def question(kind="essay", points=18, items=5, **extra):
    q = dict(
        subject="biochemistry", question_type=kind, stem="试述真核生物转录起始的调控过程及其意义。", options={},
        answer="结构化参考答案" * 10, accepted_answers=[], blanks=[], difficulty="core", knowledge_tags=[],
        evidence=[dict(textbook="教材", authors="作者", edition="版次", chapter="章", locator="OCR:1:UNIT:1", excerpt="原文")],
        max_points=points,
        rubric=[dict(description=f"要点{n}", points=points // items + (1 if n < points % items else 0), knowledge_tags=[])
                for n in range(items)],
    )
    q.update(extra)
    return q


@pytest.mark.parametrize("items,ok", [(3, False), (4, True), (6, True), (7, False)])
def test_the_question_model_enforces_the_essay_rubric_size(items, ok):
    if ok:
        Question.model_validate(question(items=items))
    else:
        with pytest.raises(ValueError, match="Essay needs 4 to 6"):
            Question.model_validate(question(items=items))


def test_the_historical_essay_and_the_other_points_are_still_accepted():
    Question.model_validate(question(points=20, items=1))  # the 28-question paper's 20-point essay
    for kind, points in v4.POINTS.items():
        if kind in ("single_choice", "essay"):
            continue
        extra = dict(accepted_answers=["结构化参考答案" * 10], stem="填空 ____") if kind == "fill_blank" else {}
        Question.model_validate(question(kind, points, 1, **extra))
    with pytest.raises(ValueError):
        Question.model_validate(question("term", 5, 1))


def test_blueprints_v4_and_v2_both_validate_and_a_wrong_one_does_not():
    assert Blueprint.model_validate(v4.default_blueprint()).specification == v4.SPECIFICATION
    assert Blueprint.model_validate(v2.default_blueprint()).specification == v2.SPECIFICATION  # stays readable
    broken = v4.default_blueprint()
    broken["quotas"]["biochemistry"]["essay"] = 2
    with pytest.raises(ValueError):
        Blueprint.model_validate(broken)
    del broken["quotas"]["biochemistry"]["essay"]
    with pytest.raises(ValueError):
        Blueprint.model_validate(broken)


def test_the_writing_instructions_cover_the_essay_with_its_own_points():
    text = exam_authoring.instructions("essay", 18)
    assert "EXACTLY 18 points" in text and "four to six" in text
    assert "DOES NOT permit essays" not in text
    assert "summing to 9" not in text and "summing to 2" not in text  # no point value of an older scale


def test_old_scored_questions_are_retired_and_their_work_goes_back_to_the_queue(db, setup_ai):
    from psycopg.types.json import Jsonb

    old = Question.model_validate(question("term", 4, 2, stem="旧分值的名词"))
    now = Question.model_validate(question("term", 6, 2, stem="新分值的名词"))
    ids = {}
    for name, q in (("old", old), ("now", now)):
        ids[name] = db.execute("INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id",
                               (Jsonb(q.model_dump()),)).fetchone()["id"]
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'checking','x')", (ids[name],))
    db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload) VALUES ('rev-old',%s,'quality_review','{}',%s)",
               (setup_ai, Jsonb({"question_id": ids["old"]})))
    stale = db.execute("INSERT INTO ai_jobs(job_key,user_id,kind,settings_snapshot,payload) VALUES ('gen-old',%s,'generation','{}',%s) RETURNING id",
                       (setup_ai, Jsonb({"question_type": "short_answer", "max_points": 9}))).fetchone()["id"]
    assert auto_quality.retire_old_scoring(db) == 1
    states = {r["question_id"]: r["status"] for r in db.execute("SELECT question_id,status FROM auto_question_states").fetchall()}
    assert states[ids["old"]] == "retired" and states[ids["now"]] == "checking"
    jobs = {r["job_key"]: (r["state"], r["error_code"]) for r in db.execute("SELECT job_key,state,error_code FROM ai_jobs").fetchall()}
    assert jobs["rev-old"] == ("blocked", "superseded_scoring") and jobs["gen-old"] == ("blocked", "superseded_scoring")
    assert auto_quality.retire_old_scoring(db) == 0  # nothing left to retire
    del stale


def test_the_database_accepts_essay_past_exam_items(db):
    unit = db.execute("SELECT 1").fetchone()
    assert unit  # constraint check only: the CHECK lists every v4 type
    row = db.execute("SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conname='past_exam_items_question_type_check'").fetchone()
    assert "essay" in row["d"]


def test_health_stays_green_while_the_previous_specification_id_is_still_set(client, monkeypatch):
    monkeypatch.setattr("app.main.business_gaps", lambda: [])  # readiness of the bank is tested in test_hardening.py
    monkeypatch.setenv("QUIZ_DAILY_SPECIFICATION", v2.SPECIFICATION)
    body = client.get("/db/health").json()
    assert body["specification_env_outdated"] is True
    assert body["formal_training"]["specification"] == v4.SPECIFICATION and body["formal_training"]["points"] == 300
    monkeypatch.setenv("QUIZ_DAILY_SPECIFICATION", "nonsense")
    assert client.get("/db/health").status_code == 503
    monkeypatch.setenv("QUIZ_DAILY_SPECIFICATION", v4.SPECIFICATION)
    assert "specification_env_outdated" not in client.get("/db/health").json()
