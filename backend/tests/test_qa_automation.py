"""Exact job grants cannot authorize changed text, owner, model or retries."""
import pytest
from app import qa_automation as q

@pytest.fixture
def job(monkeypatch):
    monkeypatch.setenv('QA_AUTOMATION_ENABLED','1')
    monkeypatch.setenv('DEEPSEEK_AUTOMATION_SCOPE_ID',q.SCOPE)
    monkeypatch.setenv('QA_AUTOMATION_SIGNING_KEY','test-only-signing-key-'*3)
    j=dict(id=17,user_id=2,kind='generation',payload={'text':'test-only excerpt'},
           state='running',attempts=1,settings_snapshot=dict(model_id='deepseek-flash',
           effort='high',token_limit=32768,retry_limit=2,max_concurrency=2))
    j['settings_snapshot']['qa_automation']=dict(scope_id=q.SCOPE,signature=q.signature(j))
    return j

def test_exact_job_authorized(job):
    q.check_job(job)
    assert callable(q.execution_guard(job))

@pytest.mark.parametrize('mutate',[
 lambda j:j.update(id=18), lambda j:j.update(user_id=3),
 lambda j:j.update(kind='grading'), lambda j:j['payload'].update(text='different'),
 lambda j:j['settings_snapshot'].update(model_id='other-provider'),
 lambda j:j['settings_snapshot'].update(token_limit=65536),
 lambda j:j['settings_snapshot'].update(retry_limit=5),
 lambda j:j['settings_snapshot'].update(limited_trial={}),
 lambda j:j['settings_snapshot']['qa_automation'].update(signature='0'*64),
])
def test_changed_or_cross_user_task_rejected(job,mutate):
    mutate(job)
    with pytest.raises(ValueError):q.check_job(job)

@pytest.mark.parametrize('state,attempts',[('pending',0),('running',4),('succeeded',1)])
def test_only_bounded_leased_attempt_can_reach_wire(job,state,attempts):
    job.update(state=state,attempts=attempts)
    with pytest.raises(ValueError):q.execution_guard(job)

def test_wire_rejects_unapproved_tool(job):
    guard=q.execution_guard(job)
    with pytest.raises(ValueError):guard({'model':'deepseek-flash','tools':[{'name':'shell'}]})

def test_money_consent_never_approves_different_scope(job,monkeypatch):
    monkeypatch.setenv('DEEPSEEK_AUTOMATION_SCOPE_ID','0'*64)
    with pytest.raises(PermissionError):q.check_job(job)


def test_missing_billing_calendar_keeps_unknown_period_instead_of_losing_receipt(monkeypatch):
    from pathlib import Path
    from app import cny_billing
    from datetime import datetime, timezone
    def unavailable(*args, **kwargs):raise FileNotFoundError('synthetic missing calendar')
    monkeypatch.setattr(Path,'read_text',unavailable)
    calendar=cny_billing.calendar_2026()
    assert calendar == {}
    assert cny_billing.period(datetime.now(timezone.utc),calendar) == 'unknown'


def test_continuous_reviews_use_explicit_latest_contract_without_rewriting_legacy():
    from app.auto_quality import review_payload,job_contract_version
    from test_prelive_quality import fixture
    q,u,_=fixture()
    old=review_payload('science',1,'c'*64,q,[u],[])
    new=review_payload('science',1,'c'*64,q,[u],[],7)
    assert 'answer' not in new['question'] and 'rubric' not in new['question']
    assert job_contract_version({'settings_snapshot':{'qa_automation':{}},'payload':new})==7
    assert job_contract_version({'settings_snapshot':{'qa_automation':{}},'payload':old})==1


def test_only_generation_can_be_signed_or_executed_even_with_valid_hmac(job):
    from app import qa_automation as q
    assert q.POLICY["tasks"] == ["generation"]
    assert "unlimited" not in q.POLICY["money_limit"]
    for forbidden in ("science_review", "answer_review", "exam_analysis",
                      "textbook_analysis", "knowledge_extraction",
                      "compatibility", "grading", "explanation",
                      "repair", "study_plan"):
        revised = {**job, "kind": forbidden, "settings_snapshot": {
            **job["settings_snapshot"],
        }}
        revised["settings_snapshot"]["qa_automation"] = {
            "scope_id": q.SCOPE, "signature": q.signature(revised)
        }
        with pytest.raises(ValueError, match="invalid"):
            q.check_job(revised)
