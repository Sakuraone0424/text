"""Evidence status must never call an OCR citation a human-certified original."""

from app import quiz_v2 as quiz
from test_api_v2 import LEARNER


def test_owner_can_read_verification_only_after_submission(client, db, running, monkeypatch):
    uid, paper = running
    pid = paper["paper_id"]
    url = f"/v2/papers/{pid}/verification"
    assert client.get(url, headers=LEARNER).status_code == 404
    quiz.submit(db, uid, pid, {})
    response = client.get(url, headers=LEARNER)
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 28
    assert len(payload["items"]) == 28
    assert all(item["ocr_quote_matches_stored_text"] for item in payload["items"])
    assert all(item["review_audit_record_matches_current_question"] for item in payload["items"])
    assert all(not item["independent_ai_science_and_answer_review"] for item in payload["items"])
    assert all(item["printed_original_book_page_verified"] is None for item in payload["items"])
    assert all(item["official_exam_original_verified"] is None for item in payload["items"])
    monkeypatch.setenv("QUIZ_USERNAME", "unauthorized-user")
    assert client.get(url, headers=LEARNER).status_code == 404
