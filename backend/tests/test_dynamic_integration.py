"""Real PostgreSQL and HTTP regression for the integrated dynamic selector."""

import subprocess
import sys
from copy import deepcopy
from collections import Counter
from datetime import timedelta
from pathlib import Path
import pytest
import psycopg
from app import quiz_v2 as quiz, exams, sources, selection
from app.common import ensure_user
from app.dynamic_model import Policy
from app.training_sessions import server_now, start
from psycopg.types.json import Jsonb
from test_api_v2 import LEARNER, REVIEWER


def register_mapped_exam(db, question_factory):
    q = question_factory()
    unit = q.evidence[0].source_unit_id
    sources.annotate(db, unit, ["A", "B"])
    db.execute(
        "UPDATE source_units SET text=text || ' Synthetic traceable exam statement' WHERE id=%s",
        (unit,),
    )
    scope = {
        "scope_key": "reviewed-test-scope",
        "subject": "biochemistry",
        "paper_code": "SYNTHETIC",
        "syllabus": "Synthetic compatibility evidence",
        "source_unit_id": unit,
        "reviewer": "test",
        "note": "Synthetic applicability proof for database tests only",
    }
    exams.register_scope(db, scope)
    row = exams.add(
        db,
        {
            "source_unit_id": unit,
            "original_question_id": "test-original-1",
            "subject": "biochemistry",
            "year": 2024,
            "question_type": "single_choice",
            "stem": "Synthetic traceable exam statement",
            "category": "basic_concept",
            "knowledge_tags": ["A", "B"],
            "textbook_units": [unit],
            "original_verified": True,
        },
    )
    metadata = {
        "scope_key": scope["scope_key"],
        "source_kind": "real_exam",
        "mapping_verified": True,
        "tag_contributions": {"A": 3.0, "B": 1.0},
        "primary_tags": ["A"],
        "cognitive_level": "mechanism",
        "canonical_item_id": "SYNTHETIC:2024:Q1",
    }
    exams.classify(
        db, row["id"], metadata, "test", "Synthetic mapping verification for tests only"
    )
    return unit, scope, row["id"], metadata


def test_trace_is_immutable_and_only_visible_after_submission(
    client, db, bank, blueprint
):
    paper = client.post(
        "/v2/papers", headers=LEARNER, json=blueprint.model_dump()
    ).json()
    pid = paper["paper_id"]
    assert "seed" not in paper and paper["questions"] == []
    assert client.get(f"/v2/papers/{pid}/analysis", headers=LEARNER).status_code == 409
    assert client.get(f"/v2/selection/runs/{pid}", headers=LEARNER).status_code == 403
    trace = client.get(f"/v2/selection/runs/{pid}", headers=REVIEWER).json()
    assert trace["seed"] and trace["algorithm_version"] == "knowledge-priority/1.0"
    assert len(trace["decisions"]) == 28
    assert all("candidates" in d and d["selected_id"] for d in trace["decisions"])
    client.post(f"/v2/papers/{pid}/start", headers=LEARNER)
    submitted = client.post(f"/v2/papers/{pid}/submit", headers=LEARNER, json={}).json()
    assert (
        submitted["score"] == 0
        and submitted["subject_scores"]["biochemistry"]["score"] == 0
    )
    analysis = client.get(f"/v2/papers/{pid}/analysis", headers=LEARNER).json()
    assert analysis["seed"] == trace["seed"] and len(analysis["selected"]) == 28
    assert analysis["meaning"] == "training_priority_not_exam_probability"
    assert all(
        v["recent_answer_samples"] == 0
        for item in analysis["selected"]
        for v in item["basis"].values()
    )
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute("UPDATE selection_runs SET seed='tampered'")


def test_actual_snapshot_replays_identical_draws(db, bank, blueprint):
    uid = ensure_user(db, "replay")
    pid = quiz.create_paper(db, uid, blueprint)["paper_id"]
    run = db.execute(
        "SELECT * FROM selection_runs WHERE paper_id=%s", (pid,)
    ).fetchone()
    candidates = db.execute(
        "SELECT id,content FROM reviewed_questions WHERE status='published' ORDER BY id"
    ).fetchall()
    decisions = []
    counts = Counter()
    selected = []
    policy = Policy.model_validate(run["config"])
    from app.training_spec import SUBJECTS, COUNTS

    for subject in SUBJECTS:
        for kind, n in COUNTS.items():
            pool = [
                r
                for r in candidates
                if r["content"]["subject"] == subject
                and r["content"]["question_type"] == kind
            ]
            selected.extend(
                selection.pick(
                    pool,
                    n,
                    counts,
                    run["context"],
                    policy,
                    run["seed"],
                    subject,
                    kind,
                    decisions,
                )
            )
    assert decisions == run["decisions"]
    assert [r["id"] for r in selected] == [r["selected_id"] for r in run["decisions"]]


def test_future_marks_events_and_policy_cannot_leak_into_past_snapshot(db, running):
    uid, paper = running
    pid = paper["paper_id"]
    qid = paper["questions"][0]["id"]
    cutoff = server_now(db)
    quiz.submit(db, uid, pid, {qid: "B"})
    questions = [
        r["snapshot"]
        for r in db.execute(
            "SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s", (pid,)
        ).fetchall()
    ]
    past, _, _ = selection.snapshot(db, uid, questions, cutoff)
    present, _, _ = selection.snapshot(db, uid, questions)
    assert past["answer_samples"] == 0 and present["answer_samples"] == 1
    assert not any(
        row["review"]["cards"]
        for subject in past["topics"].values()
        for row in subject.values()
    )
    assert any(
        row["review"]["cards"]
        for subject in present["topics"].values()
        for row in subject.values()
    )
    policy_id = db.execute(
        "INSERT INTO selection_policies(config,reviewer,note) VALUES (%s,'test','Synthetic policy change for tests only') RETURNING id",
        (Jsonb(Policy().model_dump(mode="json")),),
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO selection_policy_assignments(user_id,policy_id) VALUES (%s,%s)",
        (uid, policy_id),
    )
    assert selection.snapshot(db, uid, questions, cutoff)[0]["policy_id"] is None
    assert selection.snapshot(db, uid, questions)[0]["policy_id"] == policy_id


def test_scope_mapping_full_samples_and_classification_history(
    client, db, question_factory
):
    unit, scope, rid, metadata = register_mapped_exam(db, question_factory)
    rows = exams.trusted_records(db, server_now(db))
    record = next(r for r in rows if r["id"] == rid)
    assert record["trusted"] and record["mapping_verified"] and record["annotation_id"]
    stats = exams.frequencies(db)
    assert sum(r["contribution"] for r in stats) == 1
    wrong = {
        "scope_key": scope["scope_key"],
        "year": 2024,
        "source_unit_id": unit,
        "expected_questions": 2,
        "reviewer": "test",
        "note": "Synthetic complete paper evidence verification",
    }
    with pytest.raises(ValueError):
        exams.register_sample(db, wrong)
    exams.register_sample(db, {**wrong, "expected_questions": 1})
    cutoff = server_now(db)
    exams.classify(
        db,
        rid,
        {**metadata, "source_kind": "simulation"},
        "test",
        "Correcting synthetic classification for tests",
    )
    assert any(
        r["id"] == rid and r["source_kind"] == "real_exam"
        for r in exams.trusted_records(db, cutoff)
    )
    assert not exams.frequencies(db)
    assert (
        db.execute(
            "SELECT COUNT(*) AS n FROM exam_annotations WHERE record_id=%s", (rid,)
        ).fetchone()["n"]
        == 2
    )


def test_unverified_mapping_and_cross_scope_are_rejected(db, question_factory):
    unit, scope, rid, metadata = register_mapped_exam(db, question_factory)
    with pytest.raises(ValueError):
        exams.classify(
            db,
            rid,
            {**metadata, "scope_key": "not-reviewed"},
            "test",
            "Missing scope must fail a classification",
        )
    db.execute("DELETE FROM knowledge_links WHERE unit_id=%s", (unit,))
    with pytest.raises(ValueError):
        exams.classify(
            db, rid, metadata, "test", "Missing textbook links must fail mapping"
        )
    assert not exams.frequencies(db)


def test_prerequisite_links_require_verified_evidence_and_acyclic_graph(
    client, db, question_factory
):
    unit = question_factory().evidence[0].source_unit_id
    sources.annotate(db, unit, ["A", "B"])
    payload = {
        "subject": "biochemistry",
        "knowledge_tag": "A",
        "prerequisite_tag": "B",
        "source_unit_id": unit,
        "excerpt": "Synthetic evidence",
        "reviewer": "test",
        "note": "Synthetic prerequisite proof for tests only",
    }
    assert (
        client.post(
            "/v2/sources/dependencies", headers=LEARNER, json=payload
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v2/sources/dependencies", headers=REVIEWER, json=payload
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v2/sources/dependencies",
            headers=REVIEWER,
            json={**payload, "knowledge_tag": "B", "prerequisite_tag": "A"},
        ).status_code
        == 409
    )


def test_policy_registration_is_authorized_validated_and_versioned(
    client, db, question_factory
):
    unit, scope, rid, metadata = register_mapped_exam(db, question_factory)
    data = {
        "username": "test-owner",
        "config": Policy(
            scopes={"biochemistry": scope["scope_key"], "cell_biology": None}
        ).model_dump(mode="json"),
        "reviewer": "test",
        "note": "Synthetic policy application verified for tests",
    }
    assert (
        client.post("/v2/selection/policies", headers=LEARNER, json=data).status_code
        == 403
    )
    first = client.post("/v2/selection/policies", headers=REVIEWER, json=data).json()[
        "policy_id"
    ]
    second = client.post("/v2/selection/policies", headers=REVIEWER, json=data).json()[
        "policy_id"
    ]
    assert first != second
    uid = ensure_user(db, "test-owner")
    assert selection.policy_for(db, uid, server_now(db))[1] == second
    with pytest.raises(psycopg.errors.RaiseException):
        with db.transaction():
            db.execute("DELETE FROM selection_policies WHERE id=%s", (first,))
    wrong = deepcopy(data)
    wrong["config"]["scopes"]["biochemistry"] = "absent"
    assert (
        client.post("/v2/selection/policies", headers=REVIEWER, json=wrong).status_code
        == 409
    )


def test_quality_gate_precedes_high_priority_and_records_exclusion(
    db, bank, blueprint, question_factory, passing_audit
):
    q = question_factory()
    qid = quiz.create_question(db, q)["id"]
    quiz.review_question(db, qid, passing_audit)
    quiz.publish_question(db, qid)
    # The item is still published, but its immutable evidence reference becomes invalid.
    # This deliberately simulates an integrity fault; it must not be selected.
    db.execute(
        "UPDATE source_units SET verified=false WHERE id=%s",
        (q.evidence[0].source_unit_id,),
    )
    with pytest.raises(quiz.BankShortage):
        quiz.create_paper(db, ensure_user(db, "quality"), blueprint)
    assert db.execute("SELECT COUNT(*) AS n FROM selection_runs").fetchone()["n"] == 0


def test_seeding_cli_removed_without_contacting_database():
    result = subprocess.run(
        [sys.executable, "migrate.py", "--seed-legacy"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 2 and "unrecognized arguments" in result.stderr
    assert not Path(__file__).resolve().parents[1].joinpath("app/seed_data.py").exists()


def test_old_history_is_owned_read_only_and_not_regraded(client, db, monkeypatch):

    # Already-migrated schema: insert only synthetic historical data using a test helper.
    from historical_fixtures import seed_legacy

    seed_legacy(db)
    uid = ensure_user(db, "test-owner")
    pid = db.execute(
        "INSERT INTO papers(user_id,paper_date) VALUES (%s,'2020-01-01') RETURNING id",
        (uid,),
    ).fetchone()["id"]
    qid = db.execute("SELECT id FROM questions ORDER BY id LIMIT 1").fetchone()["id"]
    db.execute("INSERT INTO paper_questions VALUES (%s,%s,1)", (pid, qid))
    aid = db.execute(
        "INSERT INTO attempts(paper_id,user_id,score) VALUES (%s,%s,41) RETURNING id",
        (pid, uid),
    ).fetchone()["id"]
    db.execute("INSERT INTO answers VALUES (%s,%s,'B',false)", (aid, qid))
    response = client.get(f"/legacy/papers/{pid}", headers=LEARNER).json()
    assert (
        response["score"] == 41
        and response["answers"][0]["selected_answer"] == "B"
        and response["historical_read_only"]
    )
    assert (
        client.post(
            "/quiz/submit",
            headers=LEARNER,
            json={"paper_id": pid, "answers": {str(qid): "A"}},
        ).status_code
        == 410
    )
    assert (
        client.get("/v2/history", headers=LEARNER).json()["legacy"][0]["paper_id"]
        == pid
    )
    monkeypatch.setenv("QUIZ_USERNAME", "stranger")
    assert client.get(f"/legacy/papers/{pid}", headers=LEARNER).status_code == 404
    assert (
        db.execute("SELECT score FROM attempts WHERE id=%s", (aid,)).fetchone()["score"]
        == 41
    )


def test_learning_changes_next_real_paper_context_without_mutating_fsrs(
    db, bank, blueprint, question_factory, passing_audit, monkeypatch
):
    unit, scope, rid, metadata = register_mapped_exam(db, question_factory)
    uid = ensure_user(db, "adaptive-profile")
    config = Policy(scopes={"biochemistry": scope["scope_key"], "cell_biology": None})
    policy_id = db.execute(
        "INSERT INTO selection_policies(config,reviewer,note) VALUES (%s,'test','Synthetic controlled learner policy') RETURNING id",
        (Jsonb(config.model_dump(mode="json")),),
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO selection_policy_assignments(user_id,policy_id) VALUES (%s,%s)",
        (uid, policy_id),
    )
    for tag in ("A", "B"):
        for _ in range(5):
            question = question_factory()
            question.knowledge_tags = [tag]
            qid = quiz.create_question(db, question)["id"]
            quiz.review_question(db, qid, passing_audit)
            quiz.publish_question(db, qid)
    pool = db.execute(
        "SELECT id,content FROM reviewed_questions WHERE content->>'subject'='biochemistry' AND content->>'question_type'='single_choice' ORDER BY id"
    ).fetchall()
    full = db.execute("SELECT content FROM reviewed_questions").fetchall()
    context, policy, _ = selection.snapshot(db, uid, [r["content"] for r in full])
    seed = None
    for n in range(1000):
        selected = selection.pick(
            pool,
            3,
            Counter(),
            context,
            policy,
            str(n),
            "biochemistry",
            "single_choice",
            [],
        )
        if {"A", "B"}.issubset(
            {tag for r in selected for tag in r["content"]["knowledge_tags"]}
        ):
            seed = str(n)
            break
    assert seed is not None
    monkeypatch.setattr("secrets.token_hex", lambda n: seed)
    first = quiz.create_paper(db, uid, blueprint)
    pid = first["paper_id"]
    start(db, uid, pid)
    first = quiz.load_paper(db, uid, pid)
    items = {
        r["question_id"]: r["snapshot"]
        for r in db.execute(
            "SELECT * FROM paper_items_v2 WHERE paper_id=%s", (pid,)
        ).fetchall()
    }
    answers = {
        qid: "B"
        if "A" in item["knowledge_tags"]
        else "A"
        if item["question_type"] == "single_choice"
        else "fixture solution"
        for qid, item in items.items()
    }
    quiz.submit(db, uid, pid, answers)
    for qid, item in items.items():
        if item["question_type"] not in ("single_choice", "fill_blank"):
            quiz.grade(
                db,
                pid,
                qid,
                [item["max_points"]],
                "Synthetic full rubric evidence",
                "test",
            )
    # A fresh second pool allows the next paper to change its distribution without recycling old stems.
    for subject, quotas in blueprint.quotas.items():
        for kind, n in quotas.items():
            for _ in range(n):
                question = question_factory(subject, kind)
                qid = quiz.create_question(db, question)["id"]
                quiz.review_question(db, qid, passing_audit)
                quiz.publish_question(db, qid)
    before = db.execute(
        "SELECT question_id,card,due,version FROM fsrs_cards WHERE user_id=%s ORDER BY question_id",
        (uid,),
    ).fetchall()
    second = quiz.create_paper(
        db,
        uid,
        blueprint,
        __import__("app.common", fromlist=["today_cn"]).today_cn() + timedelta(days=1),
    )
    run = db.execute(
        "SELECT * FROM selection_runs WHERE paper_id=%s", (second["paper_id"],)
    ).fetchone()
    a = run["context"]["topics"]["biochemistry"]["A"]
    b = run["context"]["topics"]["biochemistry"]["B"]
    assert a["mastery"]["answer_samples"] >= 1 and b["mastery"]["answer_samples"] >= 1
    assert (
        a["mastery"]["gap"] > 0.5 > b["mastery"]["gap"]
        and a["priority"] > b["priority"]
    )
    assert (
        run["policy_id"] == policy_id
        and len(run["decisions"]) == 28
        and second["max_points"] == 150
    )
    assert (
        db.execute(
            "SELECT question_id,card,due,version FROM fsrs_cards WHERE user_id=%s ORDER BY question_id",
            (uid,),
        ).fetchall()
        == before
    )


def test_excluded_invalid_evidence_never_enters_the_priority_universe(
    db, bank, blueprint, question_factory, passing_audit, monkeypatch
):
    question = question_factory()
    unit = question.evidence[0].source_unit_id
    clone = db.execute(
        "INSERT INTO source_units(document_id,ordinal,chapter,section,text,verified,reviewer,verification_note) SELECT document_id,2,chapter,section,text,true,'test','Synthetic verification copy for quality gating' FROM source_units WHERE id=%s RETURNING id",
        (unit,),
    ).fetchone()["id"]
    question.evidence[0].source_unit_id = clone
    question.evidence[0].locator = "UNIT:2"
    question.knowledge_tags = ["high-priority-test"]
    sources.annotate(db, clone, ["high-priority-test"])
    qid = quiz.create_question(db, question)["id"]
    quiz.review_question(db, qid, passing_audit)
    quiz.publish_question(db, qid)
    db.execute("UPDATE source_units SET verified=false WHERE id=%s", (clone,))
    original = selection.snapshot

    def checked(conn, user, questions, as_of=None):
        assert not any("high-priority-test" in q["knowledge_tags"] for q in questions)
        return original(conn, user, questions, as_of)

    monkeypatch.setattr(selection, "snapshot", checked)
    pid = quiz.create_paper(db, ensure_user(db, "gated"), blueprint)["paper_id"]
    run = db.execute(
        "SELECT * FROM selection_runs WHERE paper_id=%s", (pid,)
    ).fetchone()
    excluded = next(c for c in run["candidates"] if c["question_id"] == qid)
    assert (
        excluded["excluded_reasons"] == ["textbook_evidence_not_valid"]
        and not excluded["selected"]
    )


def test_client_dictionary_order_cannot_change_the_seeded_draw(
    db, bank, blueprint, monkeypatch
):
    from app.question_quality import Blueprint

    monkeypatch.setattr("secrets.token_hex", lambda n: "fixed-canonical-seed")
    first = quiz.create_paper(db, ensure_user(db, "ordered-1"), blueprint)
    data = blueprint.model_dump()
    data["quotas"] = {
        s: dict(reversed(list(q.items())))
        for s, q in reversed(list(data["quotas"].items()))
    }
    second = quiz.create_paper(
        db, ensure_user(db, "ordered-2"), Blueprint.model_validate(data)
    )
    one = db.execute(
        "SELECT decisions FROM selection_runs WHERE paper_id=%s", (first["paper_id"],)
    ).fetchone()["decisions"]
    two = db.execute(
        "SELECT decisions FROM selection_runs WHERE paper_id=%s", (second["paper_id"],)
    ).fetchone()["decisions"]
    assert one == two


def test_one_formal_interface_and_no_legacy_static_resources(client):
    root = client.get("/")
    assert root.status_code == 200 and "肖涵 · 中科大考研智能答题" in root.text
    assert "60题" not in root.text and "/static/app.js" not in root.text
    assert client.get("/static/app.js").status_code == 404
    assert client.get("/static/index.html").status_code == 404
    assert (
        client.post("/v2/questions/1/export-legacy", headers=REVIEWER).status_code
        == 404
    )
    assert client.get("/v2/review/due", headers=LEARNER).status_code == 404


def test_unanswered_and_pending_records_cannot_initialize_fsrs_on_repeated_update(
    db, running
):
    from app.memory import record_failure

    uid, paper = running
    pid = paper["paper_id"]
    qid = next(i["id"] for i in paper["questions"] if i["question_type"] == "term")
    result = quiz.submit(db, uid, pid, {qid: "Pending actual answer"})
    for question in paper["questions"]:
        record_failure(db, result["attempt_id"], question["id"])
        record_failure(db, result["attempt_id"], question["id"])
    assert db.execute("SELECT COUNT(*) AS n FROM fsrs_cards").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM fsrs_events").fetchone()["n"] == 0


def test_public_contract_exposes_only_v2_training_and_schema_readiness(client):
    schema = client.get("/openapi.json").json()
    assert (
        "/quiz/today" not in schema["paths"] and "/quiz/submit" not in schema["paths"]
    )
    assert "/v2/papers" in schema["paths"] and "/history" in schema["paths"]
    health = client.get("/db/health").json()
    assert health["formal_training"] == {
        "questions": 28,
        "points": 150,
        "duration_seconds": 3600,
    }
    assert health["migration_count"] == 13


def test_preparation_pause_preserves_current_session_and_does_not_restore_legacy(
    client, db, bank, blueprint, monkeypatch
):
    pid = client.post(
        "/v2/papers", headers=LEARNER, json=blueprint.model_dump()
    ).json()["paper_id"]
    monkeypatch.setenv("QUIZ_PREPARATION_ENABLED", "0")
    assert client.post(f"/v2/papers/{pid}/start", headers=LEARNER).status_code == 200
    assert (
        client.post(f"/v2/papers/{pid}/submit", headers=LEARNER, json={}).json()[
            "score"
        ]
        == 0
    )
    monkeypatch.setenv("QUIZ_USERNAME", "new-owner")
    assert (
        client.post(
            "/v2/papers", headers=LEARNER, json=blueprint.model_dump()
        ).status_code
        == 503
    )
    assert client.get("/quiz/today", headers=LEARNER).status_code == 410


def test_complete_year_sample_cannot_hide_conflicting_transcript_mappings(
    db, question_factory
):
    unit, scope, rid, metadata = register_mapped_exam(db, question_factory)
    duplicate = exams.add(
        db,
        {
            "source_unit_id": unit,
            "original_question_id": "duplicate-copy",
            "subject": "biochemistry",
            "year": 2024,
            "question_type": "single_choice",
            "stem": "Synthetic traceable exam statement",
            "category": "basic_concept",
            "knowledge_tags": ["A", "B"],
            "textbook_units": [unit],
            "original_verified": True,
        },
    )
    exams.classify(
        db,
        duplicate["id"],
        {**metadata, "tag_contributions": {"A": 1, "B": 1}},
        "test",
        "Synthetic conflicting transcription for tests",
    )
    with pytest.raises(ValueError, match="conflict"):
        exams.register_sample(
            db,
            {
                "scope_key": scope["scope_key"],
                "year": 2024,
                "source_unit_id": unit,
                "expected_questions": 1,
                "reviewer": "test",
                "note": "Synthetic full-year verification must fail",
            },
        )
    assert db.execute("SELECT COUNT(*) AS n FROM exam_samples").fetchone()["n"] == 0
