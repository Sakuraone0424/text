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
