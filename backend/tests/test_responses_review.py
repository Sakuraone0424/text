"""Isolated HTTP transport fixtures only; no provider calls or formal approval."""

import json
from copy import deepcopy
import httpx
import pytest

from app import responses_review as r, review_campaign as campaign
from app.ai_jobs import content_sha, ProviderFailure
from app.question_quality import AXES


@pytest.fixture
def request_job(monkeypatch, tmp_path):
    p = r.probe_payload({"synthetic_only": True})
    policy = dict(version="review-campaign/5", contract_version=5, model_id="deepseek-flash", effort="high", output_cap=32768,
                  system_sha256="a"*64, question_payloads={"31:answer": "b"*64}, legacy_scope_id="c"*64, legacy_attempts=5,
                  legacy_reserved_cny="0.483840", legacy_unknown_acknowledged=[{"job_id":5,"attempt":1}], transport="responses_json_schema",
                  probe_payload_sha256=content_sha(p), acknowledged_unknowns=[dict(job_id=5,attempt=1,result_sha256="d"*64,reserved_cny=".096768"),dict(job_id=88,attempt=1,result_sha256="e"*64,reserved_cny=".326144")])
    sid = campaign.scope(policy)
    monkeypatch.setenv("DEEPSEEK_REVIEW_SCOPE_ID", sid)
    monkeypatch.setenv("DEEPSEEK_API_AUTHORIZED", "1")
    monkeypatch.setenv("DEEPSEEK_PRIVATE_DATA_AUTHORIZED", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-proxy-placeholder")
    monkeypatch.setenv("AI_PRIVATE_HOME", str(tmp_path))
    return dict(id=1,kind="compatibility",state="running",attempts=1,payload=p,settings_snapshot=dict(model_id="deepseek-flash",effort="high",token_limit=32768,retry_limit=0,max_concurrency=1,review_campaign=dict(scope_id=sid,policy=policy)))


def response(body):
    finding=dict(independent_choice=None,passed=True,checks={k:True for k in AXES},claims=[dict(unit_id=1,excerpt=r.SYNTHETIC_TEXT,claim="Synthetic arithmetic result independently checked",criterion_indices=[])],unresolved=[],risk_resolutions={},calculations=[],mapping_evidence={"synthetic/arithmetic":[1]},rationale="Synthetic protocol fixture only, not an actual scientific approval.")
    return dict(id="response-synthetic",model="deepseek-flash",status="completed",reasoning={"effort":"high"},max_output_tokens=32768,text=body["text"],previous_response_id=None,store=False,output=[dict(type="message",role="assistant",status="completed",content=[dict(type="output_text",text=json.dumps(finding))])],usage=dict(input_tokens=100,input_tokens_details={"cached_tokens":20},output_tokens=200,output_tokens_details={"reasoning_tokens":100},total_tokens=300))


def test_empty_flags_have_explicit_metadata_and_no_response_field_is_added(request_job, monkeypatch):
    job=deepcopy(request_job);p=r.probe_payload({"synthetic_only":True},version=2)
    policy=job["settings_snapshot"]["review_campaign"]["policy"]
    policy.update(version="review-campaign/6",contract_version=6,wire_contract_sha256=r.wire_contract_sha256(),probe_payload_sha256=content_sha(p))
    sid=campaign.scope(policy);job["settings_snapshot"]["review_campaign"]["scope_id"]=sid;job["payload"]=p
    monkeypatch.setenv("DEEPSEEK_REVIEW_SCOPE_ID",sid)
    body=r.body_for(job);schema=body["text"]["format"]["schema"]
    risk=schema["properties"]["risk_resolutions"]
    assert list(risk["properties"])==["_no_supplied_flags"]
    data=response(body);finding=json.loads(data["output"][0]["content"][0]["text"])
    finding["risk_resolutions"]={"_no_supplied_flags":risk["properties"]["_no_supplied_flags"]["enum"][0]}
    data["output"][0]["content"][0]["text"]=json.dumps(finding)
    assert r.parse_response(data,body)==finding
    del finding["risk_resolutions"]
    data["output"][0]["content"][0]["text"]=json.dumps(finding)
    with pytest.raises(ValueError,match="required_keys"):
        r.parse_response(data,body)


def test_strict_schema_preserves_every_finding_field_and_truthful_failure(request_job):
    body=r.body_for(request_job);s=body["text"]["format"]["schema"]
    assert set(s["required"])==set(r.Finding.model_fields)
    assert s["additionalProperties"] is False
    assert set(s["properties"]["checks"]["required"])==set(AXES)
    assert s["properties"]["mapping_evidence"]["properties"]["synthetic/arithmetic"]["type"]=="array"
    assert "minItems" not in s["properties"]["mapping_evidence"]["properties"]["synthetic/arithmetic"]
    assert "previous_response_id" not in body and "tools" not in body
    assert body["reasoning"]=={"effort":"high"} and body["max_output_tokens"]==32768


@pytest.mark.parametrize("defect", [None,"metadata_defaults","partial_json","incomplete","wrong_model","missing_usage","bad_cache","wrong_effort","wrong_schema","tool"])
def test_one_request_exact_receipts_and_fail_closed(request_job, tmp_path, defect):
    calls=[]
    def handle(req):
        assert str(req.url)==r.URL
        body=json.loads(req.content);calls.append(body);data=response(body)
        if defect=="metadata_defaults":data["text"]["format"]["description"]=None;data["reasoning"]["summary"]=None
        if defect=="partial_json":data["output"][0]["content"][0]["text"]='{"passed":true'
        if defect=="incomplete":data["status"]="incomplete"
        if defect=="wrong_model":data["model"]="other"
        if defect=="missing_usage":del data["usage"]
        if defect=="bad_cache":data["usage"]["input_tokens_details"]["cached_tokens"]=101
        if defect=="wrong_effort":data["reasoning"]["effort"]="low"
        if defect=="wrong_schema":data["text"]={"format":{"type":"text"}}
        if defect=="tool":data["output"].append({"type":"function_call"})
        return httpx.Response(200,json=data)
    def client(**kwargs):return httpx.Client(transport=httpx.MockTransport(handle),**kwargs)
    if defect and defect!="metadata_defaults":
        with pytest.raises(ProviderFailure):r.execute(request_job,client_factory=client)
    else:
        result=r.execute(request_job,client_factory=client)
        assert result["usage"]==dict(input_tokens=80,cache_read_input_tokens=20,output_tokens=200,cache_creation_input_tokens=0)
        assert result["response_status"]=="completed"
    assert len(calls)==1
    receipt=json.loads((tmp_path/'job-1/provider-receipt/receipt.json').read_text())
    assert receipt["http_status"]==200 and receipt["request_sha256"] and receipt["response_sha256"]
    assert "Authorization" not in (tmp_path/'job-1/provider-receipt/request.json').read_text()
