"""Mock protocol/results and synthetic rollback data; NOT real DeepSeek acceptance."""

from copy import deepcopy
from contextlib import contextmanager
import pytest
from psycopg.types.json import Jsonb
from app import model_router as m, ai_jobs as jobs, auto_quality as quality
from app.common import ensure_user
from app.question_quality import AXES
from test_candidate_review import packet as packet_fixture


@pytest.fixture
def setup_ai(db):
    uid = ensure_user(db, "synthetic-ai-owner")
    from cny_fixtures import price
    from app.cny_billing import install_price

    install_price(db, price())
    m.refresh(
        db,
        lambda: dict(
            data=[
                dict(
                    id="deepseek-flash", name="Synthetic catalog", context_window=65536
                )
            ]
        ),
    )
    cap = dict(
        real_api_verified=True,
        max_tested_output_tokens=32768,
        efforts=["off", "high"],
        tools=False,
        vision=False,
        prices=dict(
            input_per_million="1",
            output_per_million="2",
            source="synthetic-mock-price-not-official",
        ),
    )
    db.execute("UPDATE ai_model_catalog SET compatibility=%s", (Jsonb(cap),))
    m.configure(
        db,
        uid,
        "deepseek-flash",
        "high",
        stopped=False,
        daily_cny=1,
        monthly_cny=5,
        task_cny=1,
        trial_cny=5,
    )
    return uid


@pytest.fixture
def candidate(db, tmp_path):
    from app.candidate_review import create_packet

    p = packet_fixture.__wrapped__(db, tmp_path)
    return create_packet(db, p)


def test_discovery_unavailability_cache_and_unknown_fields(db):
    first = m.refresh(db, lambda: dict(data=[dict(id="future-official-test-id")]))
    assert first["models"][0]["context_window"] is None
    assert first["models"][0]["efforts"] == [] and not first["models"][0]["compatible"]
    m.refresh(db, lambda: dict(data=[dict(id="second-official-test-id")]))
    assert not next(
        x for x in m.catalog(db)["models"] if x["id"] == "future-official-test-id"
    )["available"]
    stale = m.refresh(db, lambda: dict(data=[]))
    assert stale["stale"] and len(stale["models"]) == 2
    assert not stale["auto_switch_performed"]


@pytest.mark.parametrize("effort", ["low", "max", "imaginary"])
def test_unsupported_effort_rejected(db, setup_ai, effort):
    with pytest.raises(ValueError):
        m.configure(db, setup_ai, "deepseek-flash", effort)


def test_off_explicit_disable_and_frozen_task_settings(db, setup_ai):
    assert m.wire_thinking("off") == {"thinking": {"type": "disabled"}}
    original = jobs.enqueue(db, setup_ai, "same", "science_review", {"synthetic": True})
    assert original["settings_snapshot"]["effort"] == "high"
    m.configure(db, setup_ai, "deepseek-flash", "off")
    assert (
        jobs.enqueue(db, setup_ai, "same", "science_review", {"synthetic": True})[
            "settings_snapshot"
        ]["effort"]
        == "high"
    )
    assert (
        jobs.enqueue(db, setup_ai, "new", "science_review", {"synthetic": True})[
            "settings_snapshot"
        ]["effort"]
        == "off"
    )
    with pytest.raises(ValueError):
        jobs.enqueue(db, setup_ai, "same", "science_review", {"changed": True})


def test_budget_stops_and_reservation_survives_uncertain_failure(db, setup_ai):
    jobs.enqueue(db, setup_ai, "bill", "science_review", {})
    claim = jobs.claim(db)
    assert claim and claim["reserved_cny"] > 0
    jobs.finish(db, claim, error=RuntimeError("DO NOT RECORD private secret or prompt"))
    row = db.execute("SELECT * FROM ai_call_events").fetchone()
    assert row["error_code"] == "RuntimeError" and "private" not in str(row)
    db.execute("UPDATE ai_jobs SET next_retry=clock_timestamp()-interval '1 minute'")
    m.configure(db, setup_ai, "deepseek-flash", "high", daily_cny=0, monthly_cny=0)
    assert jobs.claim(db) is None
    assert db.execute("SELECT state FROM ai_jobs").fetchone()["state"] == "blocked"
    assert (
        db.execute("SELECT count(*) AS n FROM ai_cny_reservations").fetchone()["n"] == 1
    )


def test_expired_lease_fences_old_worker(db, setup_ai):
    jobs.enqueue(db, setup_ai, "crash", "science_review", {})
    old = jobs.claim(db)
    db.execute("UPDATE ai_jobs SET lease_until=clock_timestamp()-interval '1 minute'")
    new = jobs.claim(db)
    assert new["lease_token"] != old["lease_token"]
    with pytest.raises(ValueError, match="stale_lease"):
        jobs.finish(db, old, result={})
    assert jobs.finish(db, new, result={"usage": {}}) == "succeeded"


def test_authorization_required_before_any_real_request(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_AUTHORIZED", raising=False)
    with pytest.raises(ValueError, match="authorization_required"):
        m.fetch_official()
    monkeypatch.setenv("DEEPSEEK_API_AUTHORIZED", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-never-sent")
    with pytest.raises(ValueError, match="private_excerpt"):
        m.authorized(private=True)


def synthetic_result(job, db, passed=True, quote=None):
    u = job["payload"]["evidence"][0]
    result = dict(
        real_api=True,
        usage=dict(input_tokens=10, output_tokens=10, cache_read_input_tokens=0),
        response=dict(
            passed=passed,
            independent_choice=(
                db.execute(
                    "SELECT content FROM reviewed_questions WHERE id=%s",
                    (job["payload"]["question_id"],),
                ).fetchone()["content"]["answer"]
                if db.execute(
                    "SELECT content FROM reviewed_questions WHERE id=%s",
                    (job["payload"]["question_id"],),
                ).fetchone()["content"]["question_type"]
                == "single_choice"
                else None
            ),
            checks={a: passed for a in AXES},
            claims=[
                dict(
                    unit_id=u["unit_id"],
                    excerpt=quote or u["text"],
                    claim="Synthetic test-only supported claim with no real scientific assertion",
                    criterion_indices=[0],
                )
            ],
            unresolved=[] if passed else ["Synthetic uncertainty"],
            risk_resolutions={},
            mapping_evidence={
                tag: [u["unit_id"]]
                for tag in db.execute(
                    "SELECT content FROM reviewed_questions WHERE id=%s",
                    (job["payload"]["question_id"],),
                ).fetchone()["content"]["knowledge_tags"]
            },
            rationale="Synthetic mocked round result on synthetic evidence; not a real API call or scientific certification",
        ),
    )
    db.execute(
        "UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s",
        (Jsonb(result), job["id"]),
    )
    return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()


def test_real_rounds_required_no_static_ai_or_human_dependency(db, setup_ai, candidate):
    planned = quality.schedule_checks(db, setup_ai, candidate)
    assert len(planned) == 2 and "answer" not in planned[0]["payload"]["question"]
    assert not quality.promote(db, candidate)
    db.execute(
        "UPDATE auto_question_states SET status='checking' WHERE question_id=%s",
        (candidate,),
    )
    for job in planned:
        assert quality.accept_round(db, synthetic_result(job, db))
    assert quality.promote(db, candidate) and quality.ready(db, candidate)
    assert (
        db.execute("SELECT count(*) AS n FROM candidate_human_decisions").fetchone()[
            "n"
        ]
        == 0
    )
    assert (
        db.execute("SELECT count(*) AS n FROM source_review_events").fetchone()["n"]
        == 0
    )


def test_invented_quote_blocks_despite_model_passing_itself(db, setup_ai, candidate):
    j = quality.schedule_checks(db, setup_ai, candidate)[0]
    assert not quality.accept_round(
        db, synthetic_result(j, db, quote="Invented nonexistent textbook evidence")
    )
    assert (
        db.execute("SELECT status FROM auto_question_states").fetchone()["status"]
        == "blocked"
    )


def test_mock_flag_cannot_become_production_ready(db, setup_ai, candidate):
    j = quality.schedule_checks(db, setup_ai, candidate)[0]
    db.execute(
        "UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s",
        (Jsonb(dict(real_api=False, response={})), j["id"]),
    )
    j = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (j["id"],)).fetchone()
    with pytest.raises(ValueError, match="actual_model"):
        quality.accept_round(db, j)


def test_repair_new_version_preserves_old_and_rechecks(db, setup_ai, candidate):
    quality.preflight(db, candidate)
    q = deepcopy(
        db.execute(
            "SELECT content FROM reviewed_questions WHERE id=%s", (candidate,)
        ).fetchone()["content"]
    )
    q["stem"] += " Additional synthetic condition."
    new = quality.repair(
        db,
        candidate,
        q,
        "Synthetic repair explains why this condition clarifies the question",
    )
    assert new != candidate and not quality.ready(db, new)
    assert (
        db.execute("SELECT count(*) AS n FROM candidate_revision_links").fetchone()["n"]
        == 1
    )
    assert (
        db.execute(
            "SELECT status FROM auto_question_states WHERE question_id=%s", (candidate,)
        ).fetchone()["status"]
        == "retired"
    )


def test_authenticated_model_commands(client, db, monkeypatch):
    from app import model_api

    @contextmanager
    def wrapped():
        yield db

    monkeypatch.setattr(model_api, "get_db", wrapped)
    assert client.get("/v2/model").status_code == 401
    headers = {"x-api-key": "learner-test-key"}
    assert (
        client.post(
            "/v2/model/command", headers=headers, json={"command": "/model help"}
        ).status_code
        == 200
    )
    out = client.post(
        "/v2/model/command", headers=headers, json={"command": "/model default"}
    ).json()
    assert (
        out["settings"]["model_id"] == "deepseek-flash" and out["settings"]["stopped"]
    )
    assert (
        client.post(
            "/v2/model/command", headers=headers, json={"command": "/model invented"}
        ).status_code
        == 422
    )


def test_automatic_grading_writes_fsrs_and_preserves_completed_score(
    db, setup_ai, running
):
    from app import quiz_v2 as quiz, auto_grading as grading

    uid, paper = running
    m.configure(
        db,
        uid,
        "deepseek-flash",
        "high",
        stopped=False,
        daily_cny=1,
        monthly_cny=5,
        task_cny=1,
        trial_cny=5,
    )
    quiz.submit(
        db,
        uid,
        paper["paper_id"],
        {
            q["id"]: (
                "A"
                if q["question_type"] == "single_choice"
                else "Synthetic answer with incomplete mechanism"
            )
            for q in paper["questions"]
        },
    )
    pending = grading.schedule(db, uid, paper["paper_id"])
    assert len(pending) == 16
    job = pending[0]
    q = job["payload"]["question"]
    result = dict(
        real_api=True,
        response=dict(
            criterion_points=[0 for _ in q["rubric"]],
            reasons=[
                "Synthetic missing mechanism criterion, not real grading"
                for _ in q["rubric"]
            ],
            total=0,
            uncertain=False,
            explanation="Synthetic mock explanation only, not a scientific assessment",
            knowledge_tags=q["knowledge_tags"],
        ),
    )
    db.execute(
        "UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s",
        (Jsonb(result), job["id"]),
    )
    job = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    grading.apply(db, job)
    assert grading.apply(db, job) == "already_applied"
    assert db.execute("SELECT count(*) AS n FROM fsrs_events").fetchone()["n"] > 0
    assert db.execute("SELECT count(*) AS n FROM grading_events").fetchone()["n"] == 1
    assert (
        db.execute("SELECT count(*) AS n FROM archive_jobs").fetchone()["n"] == 0
    )  # No private Notion export consent.
    forged = {**job, "id": job["id"] + 9999}
    with pytest.raises(ValueError, match="historical_score"):
        grading.apply(db, forged)


def test_uncertain_automatic_score_never_becomes_zero(db, setup_ai, running):
    from app import quiz_v2 as quiz, auto_grading as grading

    uid, paper = running
    m.configure(db, uid, "deepseek-flash", "high")
    quiz.submit(
        db,
        uid,
        paper["paper_id"],
        {
            q["id"]: (
                "A" if q["question_type"] == "single_choice" else "Synthetic answer"
            )
            for q in paper["questions"]
        },
    )
    job = grading.schedule(db, uid, paper["paper_id"])[0]
    q = job["payload"]["question"]
    job = {
        **job,
        "state": "succeeded",
        "result": dict(
            real_api=True,
            response=dict(
                criterion_points=[0],
                reasons=["Uncertain synthetic answer meaning"],
                total=0,
                uncertain=True,
                explanation="Synthetic uncertainty must remain pending, never guessed zero",
                knowledge_tags=q["knowledge_tags"],
            ),
        ),
    }
    with pytest.raises(ValueError, match="uncertain_score"):
        grading.apply(db, job)
    assert (
        db.execute(
            "SELECT points FROM answers_v2 WHERE attempt_id=%s AND question_id=%s",
            (job["payload"]["attempt_id"], job["payload"]["question_id"]),
        ).fetchone()["points"]
        is None
    )


def test_knowledge_requires_independent_confirmation_and_exact_quote(
    db, setup_ai, candidate
):
    from app import auto_knowledge as k

    unit = db.execute("SELECT id FROM source_units ORDER BY id LIMIT 1").fetchone()[
        "id"
    ]
    job = k.schedule(db, setup_ai, unit)
    text = job["payload"]["evidence"]["text"]
    result = dict(
        real_api=True,
        response=dict(
            uncertain=False,
            concepts=[
                dict(
                    name="Synthetic concept",
                    relation_type="definition",
                    target="",
                    excerpt=text,
                    rationale="Synthetic test concept, not a real scientific extraction",
                )
            ],
        ),
    )
    db.execute(
        "UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s",
        (Jsonb(result), job["id"]),
    )
    job = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    second = k.accept(db, job)
    assert (
        db.execute(
            "SELECT count(*) AS n FROM auto_knowledge_events WHERE status='ready'"
        ).fetchone()["n"]
        == 0
    )
    result = dict(
        real_api=True,
        response=dict(
            confirmations=[
                dict(
                    index=0,
                    supported=True,
                    excerpt=text,
                    rationale="Independent synthetic fixture analysis only; does not certify real science",
                )
            ]
        ),
    )
    db.execute(
        "UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s",
        (Jsonb(result), second["id"]),
    )
    second = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (second["id"],)).fetchone()
    k.accept(db, second)
    assert (
        db.execute(
            "SELECT count(*) AS n FROM auto_knowledge_events WHERE status='ready'"
        ).fetchone()["n"]
        == 1
    )


def test_discovery_change_invalidates_compatibility(db, setup_ai):
    assert m.catalog(db)["models"][0]["compatible"]
    m.refresh(db, lambda: dict(data=[dict(id="deepseek-flash", context_window=12345)]))
    assert not m.catalog(db)["models"][0]["compatible"]
    with pytest.raises(ValueError, match="not_admitted"):
        m.snapshot(db, setup_ai)


def test_automatic_gate_to_28_150_60_without_human_signature(
    db, tmp_path, setup_ai, blueprint
):
    """Mock scientific outcomes only; actual DB, selection, timer and grading machinery."""
    from uuid import uuid4
    from test_ocr_only import material
    from app import (
        sources,
        candidate_review as candidate_service,
        quiz_v2,
        training_sessions,
    )
    from app.training_spec import COUNTS, POINTS, SUBJECTS
    from app.candidate_review import snapshot_unit

    base = packet_fixture.__wrapped__(db, tmp_path)
    for subject in SUBJECTS:
        template = deepcopy(base)
        if subject == "cell_biology":
            path, meta = material(
                tmp_path,
                "cell_biology",
                content="Synthetic cell mechanism evidence for isolated regression only",
            )
            doc = sources.import_source(db, path, meta)
            unit = db.execute(
                "SELECT id FROM source_units WHERE document_id=%s ORDER BY ordinal LIMIT 1",
                (doc,),
            ).fetchone()["id"]
            u = snapshot_unit(db, unit)
            e = template["question"]["evidence"][0]
            e.update(
                file_id=u["file_id"],
                source_sha256=u["source_sha256"],
                ocr_page=u["ocr_page"],
                paragraph_id=unit,
                source_unit_id=unit,
                chapter=u["chapter"],
                locator=f"OCR:{u['ocr_page']}:UNIT:{unit}",
                excerpt=u["text"],
            )
            path, meta = material(tmp_path, "cell_biology_exams")
            doc = sources.import_source(db, path, meta)
            style = db.execute(
                "SELECT id FROM source_units WHERE document_id=%s AND text LIKE '1. Synthetic%%' ORDER BY ordinal LIMIT 1",
                (doc,),
            ).fetchone()["id"]
            template["style_sources"][0]["unit_id"] = style
            template["ai_analysis"]["rubric_evidence"][0]["unit_ids"] = [unit]
        for kind, count in COUNTS.items():
            for i in range(count):
                p = deepcopy(template)
                p["candidate_key"] = str(uuid4())
                q = p["question"]
                q.update(
                    subject=subject,
                    question_type=kind,
                    stem=str(uuid4())
                    + " "
                    + str(uuid4())
                    + " Synthetic isolated question",
                    max_points=POINTS[kind],
                )
                q["rubric"][0]["points"] = POINTS[kind]
                if kind != "single_choice":
                    q.update(options={}, answer="fixture solution")
                if kind == "fill_blank":
                    q.update(
                        stem=q["stem"] + " ____", accepted_answers=["fixture solution"]
                    )
                qid = candidate_service.create_packet(db, p)
                planned = quality.schedule_checks(db, setup_ai, qid)
                for j in planned:
                    assert quality.accept_round(db, synthetic_result(j, db))
                assert quality.promote(db, qid)
    paper = quiz_v2.create_paper(db, setup_ai, blueprint)
    assert paper["max_points"] == 150
    assert (
        db.execute(
            "SELECT count(*) AS n FROM paper_items_v2 WHERE paper_id=%s",
            (paper["paper_id"],),
        ).fetchone()["n"]
        == 28
    )
    before = db.execute("SELECT count(*) AS n FROM training_sessions").fetchone()["n"]
    assert before == 0
    training_sessions.start(db, setup_ai, paper["paper_id"])
    assert len(quiz_v2.load_paper(db, setup_ai, paper["paper_id"])["questions"]) == 28
    assert (
        db.execute(
            "SELECT extract(epoch FROM deadline-started_at) AS seconds FROM training_sessions WHERE paper_id=%s",
            (paper["paper_id"],),
        ).fetchone()["seconds"]
        == 3600
    )
    assert (
        db.execute("SELECT count(*) AS n FROM source_review_events").fetchone()["n"]
        == 0
    )
    assert (
        db.execute("SELECT count(*) AS n FROM candidate_human_decisions").fetchone()[
            "n"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "expression,expected,valid",
    [
        ("2*2.5+2*1.5", "8", True),
        ("1/3+1/6", "0.5", True),
        ("2*2.5", "6", False),
        ('__import__("os").system("false")', "0", False),
        ("2**99999", "1", False),
        ("1/0", "0", False),
    ],
)
def test_bounded_calculation_checks(expression, expected, valid):
    from app.numeric_checks import verify

    assert verify(expression, expected) is valid


def test_compatibility_probe_does_not_admit_unrun_model(db, setup_ai, monkeypatch):
    from app import model_compatibility as compatibility

    monkeypatch.setenv("DEEPSEEK_API_AUTHORIZED", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-never-sent-key")
    m.refresh(db, lambda: dict(data=[dict(id="future-test-model")]))
    from cny_fixtures import price
    from app.cny_billing import install_price

    install_price(db, price("future-test-model"))
    j = compatibility.enqueue_probe(
        db,
        setup_ai,
        "future-test-model",
        "high",
    )
    assert (
        j["state"] == "pending"
        and j["settings_snapshot"]["token_limit"]
        == m.settings(db, setup_ai)["token_limit"]
    )
    assert not next(
        x for x in m.catalog(db)["models"] if x["id"] == "future-test-model"
    )["compatible"]
    with pytest.raises(ValueError, match="real_compatibility"):
        compatibility.accept(db, j)


def test_automatic_upgrade_requires_quality_reference_and_budget(db, setup_ai):
    from app.model_compatibility import auto_upgrade

    original = db.execute(
        "SELECT * FROM ai_model_catalog WHERE model_id='deepseek-flash'"
    ).fetchone()
    db.execute(
        "INSERT INTO ai_model_catalog(model_id,metadata,compatibility) VALUES('new-synthetic','{}',%s)",
        (Jsonb({**original["compatibility"], "quality_benchmark_passed": False}),),
    )
    m.configure(db, setup_ai, "deepseek-flash", "high", auto_upgrade=True)
    assert auto_upgrade(db, setup_ai) is None
    db.execute(
        "UPDATE ai_model_catalog SET compatibility=compatibility||'{\"quality_benchmark_passed\":true}' WHERE model_id='new-synthetic'"
    )
    from cny_fixtures import price
    from app.cny_billing import install_price

    install_price(db, price("new-synthetic"))
    upgraded = auto_upgrade(db, setup_ai)
    assert upgraded and upgraded["model_id"] == "new-synthetic"


def test_transcription_review_cannot_claim_official_original(db, setup_ai, candidate):
    from app import auto_exams
    from app.candidate_review import snapshot_unit

    packet = db.execute(
        "SELECT * FROM candidate_review_packets WHERE question_id=%s", (candidate,)
    ).fetchone()
    u = snapshot_unit(db, packet["style_snapshot"][0]["unit_id"])
    job = jobs.enqueue(
        db,
        setup_ai,
        "synthetic-exam",
        "exam_analysis",
        dict(evidence=[u], declared_year=2023),
    )
    response = dict(
        year=2023,
        question_type="single_choice",
        complete_stem=u["text"],
        unit_ids=[u["unit_id"]],
        blanks=[],
        subquestions=[],
        source_level="transcription",
        original_verified=True,
        official_answer_verified=False,
        reference_answer_present=False,
        missing_content=[],
        concerns=[],
        rationale="Synthetic text review fixture only; never verifies an original paper or official answer",
    )
    result = dict(real_api=True, response=response)
    db.execute(
        "UPDATE ai_jobs SET state='succeeded',result=%s WHERE id=%s",
        (Jsonb(result), job["id"]),
    )
    job = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    with pytest.raises(ValueError, match="not_official"):
        auto_exams.accept(db, job)
    response["original_verified"] = False
    job["result"]["response"] = response
    auto_exams.accept(db, job)
    assert auto_exams.observed(db)["official_original_verified"] == 0
    assert auto_exams.observed(db)["official_probability_estimate"] is None


def test_auto_only_does_not_fall_back_to_legacy_human_bank(
    db, bank, blueprint, monkeypatch
):
    from app import quiz_v2

    monkeypatch.setenv("QUIZ_AUTO_ONLY", "1")
    uid = ensure_user(db, "automatic-only-owner")
    with pytest.raises(quiz_v2.BankShortage):
        quiz_v2.create_paper(db, uid, blueprint)
    assert db.execute("SELECT count(*) AS n FROM papers_v2").fetchone()["n"] == 0


def test_approved_https_origin_behind_proxy_and_cross_origin_rejection(
    client, monkeypatch
):
    monkeypatch.setenv("QUIZ_PUBLIC_ORIGIN", "https://approved-staging.example")
    body = {"command": "/model help"}
    headers = {
        "x-api-key": "learner-test-key",
        "origin": "https://approved-staging.example",
    }
    assert (
        client.post("/v2/model/command", headers=headers, json=body).status_code == 200
    )
    assert (
        client.post(
            "/v2/model/command",
            headers={**headers, "origin": "https://evil.example"},
            json=body,
        ).status_code
        == 403
    )
    monkeypatch.setenv(
        "QUIZ_PUBLIC_ORIGIN", "https://approved-staging.example/unsafe-path"
    )
    assert (
        client.post("/v2/model/command", headers=headers, json=body).status_code == 503
    )


@pytest.mark.parametrize(
    "now,previous,expected",
    [
        ("2026-10-27T19:59:00+00:00", None, False),
        ("2026-10-27T20:00:00+00:00", None, True),
        ("2026-10-27T21:00:00+00:00", "2026-10-27T20:00:01+00:00", False),
    ],
)
def test_private_backup_beijing_schedule_and_restart_idempotence(
    now, previous, expected
):
    from datetime import datetime
    from app.scheduled_backup import due

    assert due({"snapshot_at": previous}, datetime.fromisoformat(now)) is expected


def test_scheduled_backup_is_disabled_without_storage_authorization(monkeypatch):
    from app.scheduled_backup import tick

    monkeypatch.delenv("QUIZ_PRIVATE_BACKUP_ENABLED", raising=False)
    assert not tick()


def test_independent_choice_conflict_blocks_even_when_model_checks_pass(
    db, setup_ai, candidate
):
    job = quality.schedule_checks(db, setup_ai, candidate)[0]
    job = synthetic_result(job, db)
    job["result"]["response"]["independent_choice"] = "B"
    db.execute(
        "UPDATE ai_jobs SET result=%s WHERE id=%s", (Jsonb(job["result"]), job["id"])
    )
    assert not quality.accept_round(db, job)
    assert not quality.ready(db, candidate)
