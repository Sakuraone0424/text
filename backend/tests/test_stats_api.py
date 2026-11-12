"""The statistics, mistake and practice endpoints: login required, validated, owner-only."""
from datetime import datetime, timedelta, timezone

import pytest

from app import auto_quality
from app.common import ensure_user
from stats_helpers import item, paper, question
from test_api_v2 import LEARNER


def open_stock(monkeypatch):
    """Synthetic questions have no pinned textbook evidence and there is no daily-paper reserve to protect in these tests."""
    from app import auto_generation, practice_center

    monkeypatch.setattr(auto_generation, "pinned_science_evidence", lambda content: True)
    monkeypatch.setattr(practice_center, "reserve_of", lambda subject, kind: 0)


@pytest.fixture
def api(client, db, monkeypatch):
    from app import learning_stats

    uid = ensure_user(db, "test-owner")
    return client, db, uid, learning_stats


def test_every_endpoint_needs_a_login(client):
    for method, path in [
        ("get", "/v2/stats/overview"), ("get", "/v2/stats/profile"), ("get", "/v2/mistakes"), ("get", "/v2/papers/1/timing"),
        ("post", "/v2/practice"), ("get", "/v2/practice/1"), ("post", "/v2/practice/1/answer"),
    ]:
        assert getattr(client, method)(path).status_code in (401, 403, 405), path


def test_overview_and_profile_return_numbers_for_the_logged_in_learner(api):
    client, db, uid, stats = api
    today = stats.today_cn(db)
    paper(db, uid, today, [item(got=4), item("cell_biology", "term", 6, 3)], when=datetime.now(timezone.utc))
    overview = client.get("/v2/stats/overview", headers=LEARNER).json()
    assert overview["streak"]["current"] == 1 and overview["today_paper"]["state"] == "submitted" and overview["has_data"]
    profile = client.get("/v2/stats/profile", headers=LEARNER).json()
    assert profile["summary"]["papers"] == 1 and len(profile["calendar"]) == 56 and len(profile["trend"]) == 1


def test_mistake_filters_are_validated_and_applied(api):
    client, db, uid, stats = api
    paper(db, uid, stats.today_cn(db), [item(got=0, chapter="X"), item("cell_biology", "term", 6, 0, "Y")], when=datetime.now(timezone.utc))
    body = client.get("/v2/mistakes", headers=LEARNER, params={"subject": "cell_biology"}).json()
    assert body["total"] == 1 and body["items"][0]["question_type"] == "term"
    assert client.get("/v2/mistakes", headers=LEARNER, params={"subject": "physics"}).status_code == 422
    assert client.get("/v2/mistakes", headers=LEARNER, params={"question_type": "essay2"}).status_code == 422
    assert client.get("/v2/mistakes", headers=LEARNER, params={"limit": 500}).status_code == 422
    assert client.get("/v2/mistakes", headers=LEARNER, params={"chapter": "X"}).json()["total"] == 1


def test_timing_of_a_paper_is_owner_only_numbers(api):
    client, db, uid, stats = api
    made = paper(db, uid, stats.today_cn(db), [item(seconds=30), item("biochemistry", "essay", 18, 9, seconds=3000)], when=datetime.now(timezone.utc))
    body = client.get(f"/v2/papers/{made['paper_id']}/timing", headers=LEARNER).json()
    assert body["recorded"] is True and {t["question_type"] for t in body["types"]} == {"single_choice", "essay"}
    other = ensure_user(db, "somebody-else")
    theirs = paper(db, other, stats.today_cn(db) - timedelta(days=1), [item(seconds=10)], when=datetime.now(timezone.utc) - timedelta(days=1))
    assert client.get(f"/v2/papers/{theirs['paper_id']}/timing", headers=LEARNER).json() == {"recorded": False, "types": []}


def test_practice_over_http_end_to_end(api, monkeypatch):
    client, db, uid, stats = api
    monkeypatch.setattr(auto_quality, "ready", lambda conn, qid: True)
    open_stock(monkeypatch)
    ids = []
    for n in range(6):
        qid = question(db, "biochemistry", "single_choice", 3, "糖代谢", options={"A": "a", "B": "b", "C": "c", "D": "d"}, answer="A", stem=f"练习题{n}")
        db.execute("INSERT INTO auto_question_states(question_id,status,content_sha256) VALUES (%s,'ready','x')", (qid,))
        ids.append(qid)
    started = client.post("/v2/practice", headers=LEARNER, json={"mode": "chapter", "subject": "biochemistry", "chapter": "糖代谢", "size": 2})
    assert started.status_code == 200 and len(started.json()["questions"]) == 2 and started.json()["counts_toward_daily_paper"] is False
    sid = started.json()["set_id"]
    q = started.json()["questions"][0]
    assert "answer" not in q
    answered = client.post(f"/v2/practice/{sid}/answer", headers=LEARNER, json={"question_id": q["id"], "text": "A", "seconds": 9})
    assert answered.status_code == 200 and answered.json()["correct"] is True
    assert client.post(f"/v2/practice/{sid}/answer", headers=LEARNER, json={"question_id": q["id"], "text": "A"}).status_code == 409  # once
    assert client.post(f"/v2/practice/{sid}/answer", headers=LEARNER, json={"question_id": q["id"], "text": "A", "seconds": 99999}).status_code == 422
    assert client.get(f"/v2/practice/{sid}", headers=LEARNER).json()["answered"] == 1
    assert client.post("/v2/practice", headers=LEARNER, json={"mode": "chapter", "subject": "biochemistry", "chapter": "不存在", "size": 5}).status_code == 409
    assert client.post("/v2/practice", headers=LEARNER, json={"mode": "weird"}).status_code == 422
    assert client.post("/v2/practice", headers=LEARNER, json={"mode": "chapter", "subject": "biochemistry", "chapter": "糖代谢", "size": 99}).status_code == 422
    stranger = ensure_user(db, "intruder")
    db.execute("UPDATE practice_sets SET user_id=%s WHERE id=%s", (stranger, sid))
    assert client.get(f"/v2/practice/{sid}", headers=LEARNER).status_code == 404  # not found for the real learner
