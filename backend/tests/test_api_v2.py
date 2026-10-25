from app import quiz_v2 as q
from app.common import ensure_user

LEARNER = {"x-api-key": "learner-test-key"}
REVIEWER = {"x-review-api-key": "operator-test-key"}


def test_legacy_api_contract_and_v2_shortage(client, blueprint):
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/quiz/today").status_code == 401
    response = client.get("/quiz/today", headers=LEARNER)
    assert response.status_code == 410 and "retired" in response.json()["detail"]
    response = client.post("/v2/papers", headers=LEARNER, json=blueprint.model_dump())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "insufficient_reviewed_bank"
    assert len(response.json()["detail"]["shortages"]) == 10
    assert client.get("/history", headers=LEARNER).json() == {"items": []}
    assert client.get("/stats", headers=LEARNER).status_code == 200
    assert client.get("/review/due", headers=LEARNER).status_code == 410


def test_review_authorization_and_invalid_content(
    client, question_factory, passing_audit, monkeypatch
):
    body = question_factory().model_dump()
    assert client.post("/v2/questions", json=body, headers=LEARNER).status_code == 403
    assert (
        client.post(
            "/v2/questions", json={**body, "evidence": []}, headers=REVIEWER
        ).status_code
        == 422
    )
    draft = client.post("/v2/questions", json=body, headers=REVIEWER).json()
    qid = draft["id"]
    assert (
        client.post(f"/v2/questions/{qid}/publish", headers=REVIEWER).status_code == 409
    )
    assert client.post(
        f"/v2/questions/{qid}/audit", json=passing_audit.model_dump(), headers=REVIEWER
    ).json()["passed"]
    assert (
        client.post(f"/v2/questions/{qid}/publish", headers=REVIEWER).json()["status"]
        == "published"
    )
    assert client.get(f"/v2/questions/{qid}", headers=LEARNER).status_code == 403
    assert (
        len(client.get(f"/v2/questions/{qid}", headers=REVIEWER).json()["audits"]) == 1
    )
    assert (
        client.post(f"/v2/questions/{qid}/withdraw", headers=REVIEWER).json()["status"]
        == "withdrawn"
    )
    monkeypatch.delenv("QUIZ_REVIEW_API_KEY")
    assert client.post("/v2/questions", json=body, headers=REVIEWER).status_code == 503


def test_http_mixed_paper_and_rollback_invalid_submission(
    client, db, bank, blueprint, monkeypatch
):
    response = client.post("/v2/papers", json=blueprint.model_dump(), headers=LEARNER)
    assert response.status_code == 200
    paper = response.json()
    pid = paper["paper_id"]
    assert paper["count"] == 28
    assert paper["questions"] == []
    paper = client.post(f"/v2/papers/{pid}/start", headers=LEARNER).json()
    invalid = {
        str(i["id"]): "A"
        if i["question_type"] == "single_choice"
        else "fixture solution"
        for i in paper["questions"]
    }
    invalid[str(paper["questions"][0]["id"])] = "not_an_option"
    assert (
        client.post(
            f"/v2/papers/{pid}/submit", json={"answers": invalid}, headers=LEARNER
        ).status_code
        == 409
    )
    assert db.execute("SELECT COUNT(*) AS n FROM attempts_v2").fetchone()["n"] == 0
    invalid[str(paper["questions"][0]["id"])] = "A"
    response = client.post(
        f"/v2/papers/{pid}/submit", json={"answers": invalid}, headers=LEARNER
    )
    assert response.status_code == 200 and response.json()["score"] is None
    assert (
        client.post(
            f"/v2/papers/{pid}/submit", json={"answers": invalid}, headers=LEARNER
        ).status_code
        == 409
    )
    assert (
        client.get(f"/v2/papers/{pid}/result", headers=LEARNER).json()["pending"] == 16
    )
    item = next(i for i in paper["questions"] if i["question_type"] == "essay")
    path = f"/v2/papers/{pid}/answers/{item['id']}/grade"
    payload = {
        "criterion_points": [2],
        "reviewer": "expert",
        "feedback": "Meets the rubric",
    }
    assert client.post(path, json=payload, headers=LEARNER).status_code == 403
    assert (
        client.post(
            path, json={**payload, "criterion_points": [True]}, headers=REVIEWER
        ).status_code
        == 422
    )
    assert client.post(path, json=payload, headers=REVIEWER).json()["pending"] == 15
    monkeypatch.setenv("QUIZ_USERNAME", "other-owner")
    assert client.get(f"/v2/papers/{pid}", headers=LEARNER).status_code == 404
    assert client.get(f"/v2/papers/{pid}/result", headers=LEARNER).status_code == 404


def test_review_completion_is_owned_and_due(client, db, bank, blueprint, monkeypatch):
    uid = ensure_user(db, "test-owner")
    paper = q.create_paper(db, uid, blueprint)
    from app.training_sessions import start

    start(db, uid, paper["paper_id"])
    paper = q.load_paper(db, uid, paper["paper_id"])
    answers = {
        i["id"]: "B" if i["question_type"] == "single_choice" else "fixture solution"
        for i in paper["questions"]
    }
    q.submit(db, uid, paper["paper_id"], answers)
    db.execute("UPDATE fsrs_cards SET due=clock_timestamp()")
    rows = client.get("/v2/memory/due", headers=LEARNER).json()["items"]
    assert len(rows) == 6
    row = rows[0]
    path = f"/v2/memory/{row['question_id']}/review"
    payload = {
        "rating": 3,
        "expected_version": row["version"],
        "event_key": "actual-review-1",
    }
    monkeypatch.setenv("QUIZ_USERNAME", "stranger")
    assert client.post(path, json=payload, headers=LEARNER).status_code == 404
    monkeypatch.setenv("QUIZ_USERNAME", "test-owner")
    assert client.post(path, json=payload, headers=LEARNER).status_code == 200
    assert len(client.get("/v2/memory/due", headers=LEARNER).json()["items"]) == 5


def test_quality_report_requires_operator(client):
    assert client.get("/v2/quality/report", headers=LEARNER).status_code == 403
    report = client.get("/v2/quality/report", headers=REVIEWER).json()
    assert report["historical_questions"] == 0 and report["duplicate_threshold"] == 0.85


def test_retired_legacy_training_never_creates_records(client, db):
    assert client.get("/quiz/today", headers=LEARNER).status_code == 410
    assert (
        client.post("/quiz/submit", headers=LEARNER, json={"answers": {}}).status_code
        == 410
    )
    assert db.execute("SELECT COUNT(*) AS n FROM papers").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM attempts").fetchone()["n"] == 0
