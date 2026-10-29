"""Verified question stock and genuine exam style must be reported separately."""
from app.bank_readiness import report
from app.common import ensure_user
from test_api_v2 import LEARNER


def test_empty_private_database_reports_60q_and_120q_shortages(db, client):
    uid = ensure_user(db, "new-training-student")
    result = report(db, uid)
    assert len(result["buckets"]) == 8
    assert sum(b["required"] for b in result["buckets"]) == 60
    assert sum(b["two_day_reserve_target"] for b in result["buckets"]) == 120
    assert sum(b["shortage_at_least"] for b in result["buckets"]) == 60
    assert sum(b["two_day_reserve_shortage_at_least"] for b in result["buckets"]) == 120
    assert result["minimum_bucket_counts_met"] is False
    assert result["two_day_reserve_counts_met"] is False
    assert result["exact_selection_verified"] is False
    assert result["genuine_past_exam_style"]["ready_for_same_type_style"] is False
    assert len(result["genuine_past_exam_style"]["missing_same_type_style"]) == 8
    response = client.get("/v2/quality/60q-bank-readiness", headers=LEARNER)
    assert response.status_code == 200
    assert response.json()["genuine_past_exam_style"]["missing_same_type_style"]
