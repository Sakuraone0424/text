"""Offline synthetic fixtures; real_api always false, no formal approval."""

import json
from copy import deepcopy
import pytest
from app.quality_preview import validate
from app.auto_quality import review_payload
from app.answer_cues import conflict
from app.question_quality import AXES


def fixture():
    question = dict(
        subject="biochemistry",
        question_type="single_choice",
        stem="Synthetic stem",
        options={"A": "one", "B": "two", "C": "three", "D": "four"},
        knowledge_tags=["test"],
        answer="A",
        rubric=[{"points": 2}],
        blanks=[],
        max_points=2,
        evidence=[{"source_unit_id": 1, "excerpt": "Synthetic cited text"}],
    )
    unit = dict(
        unit_id=1,
        unit_sha256="a" * 64,
        file_id="synthetic",
        source_sha256="b" * 64,
        chapter="chapter",
        section="section",
        ocr_page=1,
        quality_flags=[],
        subject="biochemistry",
        source_kind="textbook",
        text="Unneeded context. Synthetic cited text. trailing context.",
    )
    finding = dict(
        independent_choice="A",
        passed=True,
        checks={a: True for a in AXES},
        claims=[
            dict(
                unit_id=1,
                excerpt="Synthetic cited text",
                claim="Synthetic claim with enough explanation",
                criterion_indices=[0],
            )
        ],
        unresolved=[],
        risk_resolutions={},
        calculations=[],
        mapping_evidence={"test": [1]},
        rationale="Synthetic offline test only; never an actual scientific approval",
    )
    return question, unit, finding


@pytest.mark.parametrize(
    "case", ["pass", "reject", "conflict", "malformed", "tampered", "partial"]
)
def test_offline_outcomes_never_produce_real_result(case):
    q, u, f = fixture()
    phase = "science"
    if case == "reject":
        f["passed"] = False
    if case == "conflict":
        f["independent_choice"] = "B"
    if case == "tampered":
        f["claims"][0]["excerpt"] = "Fabricated original text"
    if case == "partial":
        phase = "answer"
        f["claims"][0]["criterion_indices"] = []
    raw = "{" if case == "malformed" else json.dumps(f)
    result = validate(raw, q, [u], phase)
    assert result["real_api"] is False and result["formal_approval"] is False
    assert result["local_checks_passed"] == (case == "pass")


def test_material_blinding_and_excerpt_minimization():
    q, u, f = fixture()
    style = dict(
        u,
        source_kind="exam_transcription",
        relationship="Historical Codex verdict must not escape",
        ai_analysis="old verdict",
        year=2026,
    )
    s = review_payload("science", 1, "c" * 64, q, [u], [style])
    a = review_payload("answer", 1, "c" * 64, q, [u], [style])
    assert (
        "answer" not in s["question"]
        and "rubric" not in s["question"]
        and "max_points" not in s["question"]
    )
    assert a["question"]["answer"] == "A"
    assert a["style"] == []
    assert s["evidence"][0]["text"] == "Synthetic cited text"
    assert "relationship" not in s["style"][0] and "ai_analysis" not in s["style"][0]
    assert "old verdict" not in json.dumps([s, a])
    assert "ai_analysis" not in a["question"]


def test_cue_rule_survives_question_revision_and_order():
    a = dict(subject="biochemistry", knowledge_tags=["实验方法/二维电泳"])
    b = deepcopy(a)
    assert conflict(a, [b]) and conflict(b, [a])
    assert not conflict(
        a, [dict(subject="cell_biology", knowledge_tags=b["knowledge_tags"])]
    )


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": True, "output_tokens": 1, "cache_read_input_tokens": 0},
        {"input_tokens": 1, "output_tokens": 1},
    ],
)
def test_unknown_or_invalid_usage_never_qualifies(usage):
    from app.auto_quality import complete_usage

    assert not complete_usage(usage)


def test_timeout_and_partial_batch_never_approve_formal_questions(db, monkeypatch):
    from test_ai_automation import setup_ai, candidate
    from app import ai_jobs, model_router, auto_quality

    uid = setup_ai.__wrapped__(db)
    # Synthetic isolated fixture only; no authorization flags are enabled.
    from tempfile import TemporaryDirectory
    from pathlib import Path

    with TemporaryDirectory() as d:
        qid = candidate.__wrapped__(db, Path(d))
    planned = auto_quality.schedule_checks(db, uid, qid)
    monkeypatch.setattr(model_router, "authorized", lambda **kwargs: None)
    calls = []

    def fake(job):
        calls.append(job["kind"])
        if job["kind"] == "science_review":
            raise TimeoutError("synthetic timeout")
        return {"real_api": False, "response": {"malformed": "synthetic"}, "usage": {}}

    # Use the application's connection boundary without opening any network.
    from contextlib import contextmanager

    @contextmanager
    def conn():
        yield db

    assert ai_jobs.run_once(conn, fake)
    assert ai_jobs.run_once(conn, fake)
    rows = db.execute("select * from ai_jobs order by id").fetchall()
    assert any(r["state"] in ("retrying", "blocked") for r in rows)
    assert any(
        r["state"] == "succeeded" and r["result"]["real_api"] is False for r in rows
    )
    for r in rows:
        if r["state"] == "succeeded":
            with pytest.raises(ValueError, match="actual_model"):
                auto_quality.accept_round(db, r)
    assert not auto_quality.ready(db, qid)
    assert (
        db.execute(
            "select count(*) AS n from auto_quality_events where phase in ('science','answer')"
        ).fetchone()["n"]
        == 0
    )
    assert len(planned) == 2 and len(calls) == 2
