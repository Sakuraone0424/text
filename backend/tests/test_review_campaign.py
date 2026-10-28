"""Synthetic rollback-only campaign tests. Never call a model or certify content."""

from decimal import Decimal
from copy import deepcopy
import pytest
from app import ai_jobs, review_campaign as campaign
from test_limited_trial import trial, success, private_job, setup_ai, live_app  # noqa: F401


@pytest.fixture
def authorized_campaign(db, trial, monkeypatch):
    for _ in range(4):
        job = ai_jobs.claim(db, scope_id=trial[2])
        success(db, job)
    private_job(db, trial)
    old = ai_jobs.claim(db, scope_id=trial[2])
    ai_jobs.finish(db, old, error=ai_jobs.ProviderFailure(200, usage={"input_tokens": 2435, "output_tokens": 4096, "cache_read_input_tokens": 0}))
    assert old["id"] == 5
    policy = dict(version="review-campaign/1", model_id="deepseek-flash", effort="high", output_cap=32768, system_sha256="a" * 64,
                  question_payloads=trial[1]["question_payloads"], legacy_scope_id=trial[2], legacy_attempts=5,
                  legacy_reserved_cny="0.483840", legacy_unknown_acknowledged=[{"job_id": 5, "attempt": 1}])
    sid = campaign.scope(policy)
    monkeypatch.setenv("DEEPSEEK_REVIEW_SCOPE_ID", sid)
    campaign.authorize(db, trial[0], policy, "c" * 64)
    campaign.activate(db, sid, disable_user_limits=True)
    return trial[0], policy, sid, trial[3]


def enqueue(db, value, slot="39:science", suffix=""):
    uid, policy, sid, payloads = value
    p = payloads[slot]
    job = ai_jobs.enqueue(db, uid, "campaign-synthetic:" + slot + suffix, p["phase"] + "_review", p)
    campaign.bind(db, job["id"], sid)
    return db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()


def test_disabled_user_limits_preserve_history_and_allow_approved_scope(db, authorized_campaign):
    job = enqueue(db, authorized_campaign)
    claimed = ai_jobs.claim(db, scope_id=authorized_campaign[2], scope_kind="review_campaign")
    assert claimed["id"] == job["id"] and claimed["attempts"] == 1
    assert claimed["settings_snapshot"]["effort"] == "high" and claimed["settings_snapshot"]["token_limit"] == 32768
    assert claimed["reserved_cny"] == Decimal(".326144")
    old = db.execute("SELECT * FROM ai_jobs WHERE id=5").fetchone()
    assert old["state"] == "blocked" and old["billing_status"] == "unknown" and old["attempts"] == 1
    assert db.execute("SELECT sum(amount_cny) AS n FROM ai_cny_reservations WHERE job_id<=5").fetchone()["n"] == Decimal(".483840")
    assert ai_jobs.claim(db, scope_id=authorized_campaign[2], scope_kind="review_campaign") is None


def test_unknown_new_charge_stops_this_and_future_campaigns(db, authorized_campaign, monkeypatch):
    enqueue(db, authorized_campaign)
    first = ai_jobs.claim(db, scope_id=authorized_campaign[2], scope_kind="review_campaign")
    ai_jobs.finish(db, first, error=TimeoutError())
    peer = enqueue(db, authorized_campaign, "39:answer")
    assert campaign.claim_reason(db, peer) == "campaign_unacknowledged_unknown_charge_stop"
    policy = deepcopy(authorized_campaign[1]);policy["question_payloads"].pop("9:answer")
    sid = campaign.scope(policy);monkeypatch.setenv("DEEPSEEK_REVIEW_SCOPE_ID", sid)
    campaign.authorize(db, authorized_campaign[0], policy, "d" * 64)
    campaign.activate(db, sid)
    next_job = enqueue(db, (authorized_campaign[0], policy, sid, authorized_campaign[3]), "26:science", "next")
    assert campaign.claim_reason(db, next_job) == "campaign_unacknowledged_unknown_charge_stop"


def test_single_attempt_duplicate_slot_and_scope_isolation(db, authorized_campaign):
    first = enqueue(db, authorized_campaign)
    enqueue(db, authorized_campaign, suffix="duplicate")
    assert campaign.claim_reason(db, first) == "campaign_slot_already_bound"
    assert db.execute("SELECT sum(attempts) AS n FROM ai_jobs").fetchone()["n"] == 5
    old = db.execute("SELECT * FROM ai_jobs WHERE id=5").fetchone()
    with pytest.raises(ValueError, match="unattempted"):
        campaign.bind(db, old["id"], authorized_campaign[2])


def test_campaign_rejects_low_bad_payload_and_unauthorized_scope(db, authorized_campaign, monkeypatch):
    job = enqueue(db, authorized_campaign)
    stored = db.execute("SELECT * FROM ai_jobs WHERE id=%s", (job["id"],)).fetchone()
    bad = deepcopy(stored);bad["settings_snapshot"]["effort"] = "low"
    with pytest.raises(ValueError, match="parameters_changed"):campaign.check_job(bad)
    bad = deepcopy(stored);bad["payload"]["unexpected"] = "private extra"
    with pytest.raises(ValueError, match="private_payload"):campaign.check_job(bad)
    monkeypatch.delenv("DEEPSEEK_REVIEW_SCOPE_ID")
    with pytest.raises(ValueError, match="not_authorized"):campaign.check_job(stored)


def test_pause_is_audited_and_zero_limits_default_remain_enabled(db, authorized_campaign):
    campaign.pause(db, authorized_campaign[2])
    settings = db.execute("SELECT * FROM ai_settings WHERE user_id=%s", (authorized_campaign[0],)).fetchone()
    assert settings["stopped"] and not settings["task_limit_enabled"]
    assert db.execute("SELECT action FROM ai_review_campaign_events ORDER BY id DESC LIMIT 1").fetchone()["action"] == "paused"
    assert db.execute("SELECT count(*) AS n FROM ai_call_events").fetchone()["n"] == 5


def test_contract_upgrade_never_relabels_rejection_or_retries_science_conflict():
    from app.question_quality import AXES
    rejection = {"settings_snapshot": {}, "result": {"issues": ["knowledge_mapping_not_evidenced"], "finding": {"passed": True, "checks": {key: True for key in AXES}, "unresolved": []}}}
    original = deepcopy(rejection)
    assert campaign.contract_retry_allowed(rejection, 2)
    assert not campaign.contract_retry_allowed(rejection, 1)
    assert rejection == original
    rejection["result"]["issues"].append("independent_answer_conflict")
    assert not campaign.contract_retry_allowed(rejection, 2)
    rejection = deepcopy(original);rejection["result"]["finding"]["unresolved"] = ["real scientific uncertainty"]
    assert not campaign.contract_retry_allowed(rejection, 2)


def test_v2_contract_preserves_blinding_material_and_historical_payload():
    from test_prelive_quality import fixture
    from app.auto_quality import review_payload
    q, u, _ = fixture()
    original = review_payload("science", 1, "c" * 64, q, [u], [])
    new = review_payload("science", 1, "c" * 64, q, [u], [], 2)
    assert {k:v for k,v in new.items() if k!='instructions'} == {k:v for k,v in original.items() if k!='instructions'}
    assert "answer" not in new["question"] and "rubric" not in new["question"]
    assert "question.knowledge_tags" in new["instructions"] and "<unit_id>:<quality_flag>" in new["instructions"]
    assert original == review_payload("science", 1, "c" * 64, q, [u], [])
    q["accepted_answers"] = ["A scientifically accepted equivalent"]
    answer = review_payload("answer", 1, "c" * 64, q, [u], [], 2)
    assert answer["question"]["accepted_answers"] == q["accepted_answers"]


def test_boolean_polarity_retry_is_bounded_and_never_accepts_old_result():
    from app.question_quality import AXES
    checks = {key: True for key in AXES};checks['duplication'] = False
    rejection = {'settings_snapshot': {'review_campaign': {'policy': {'contract_version': 2}}}, 'result': {'issues': ['unresolved_quality_findings'], 'finding': {'passed': True, 'checks': checks, 'unresolved': []}}}
    old = deepcopy(rejection)
    assert campaign.contract_retry_allowed(rejection, 3)
    assert rejection == old
    rejection['settings_snapshot']['review_campaign']['policy']['contract_version'] = 3
    assert not campaign.contract_retry_allowed(rejection, 3)
    rejection = deepcopy(old);rejection['result']['finding']['checks']['scientific_correctness'] = False
    assert not campaign.contract_retry_allowed(rejection, 3)
    rejection = deepcopy(old);rejection['result']['finding']['passed'] = False
    assert not campaign.contract_retry_allowed(rejection, 3)
