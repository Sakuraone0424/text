"""The 60q release gate demands real provenance, never a green fixture."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.qa_real_60q_readback import assess_attempt
from app.source_policy import COLLECTION
from app.training_spec_v2 import COUNTS, POINTS, SUBJECTS, default_blueprint


def sample():
    item = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_upper")
    locked = json.loads(
        (Path(__file__).parents[1] / "app" / "ocr_source_sha256_lock.json")
        .read_text(encoding="utf8")
    )
    hash_value = locked["files"][item["key"]]["sha256"]
    rows = []
    position = 0
    subjective = set()
    for subject in SUBJECTS:
        for kind, count in COUNTS.items():
            for _ in range(count):
                position += 1
                points = POINTS[kind]
                is_subjective = kind in ("term", "short_answer")
                rows.append({
                    "position": position, "question_id": position,
                    "snapshot": {
                        "subject": subject, "question_type": kind,
                        "max_points": points,
                        "rubric": [{"description": "synthetic score", "points": points}],
                        "evidence": [{
                            "file_id": item["file_id"],
                            "source_sha256": hash_value,
                            "chapter": "synthetic source test",
                        }],
                    },
                    "answer": json.dumps({"text": "synthetic answer"}),
                    "points": points,
                    "graded_by": "AI自动核验" if is_subjective else None,
                    "feedback": {"ai_job_id": position+1000,
                                 "model_id": "synthetic_fake_do_not_publish"}
                        if is_subjective else {},
                })
                if is_subjective:
                    subjective.add(position)
    # One real-style failed answer event is essential to prove FSRS wiring.
    rows[0]["points"] = 0
    start = datetime(2026, 10, 30, tzinfo=timezone.utc)
    paper = {
        "blueprint": default_blueprint(), "attempt_id": 99,
        "attempt_submitted_at": start + timedelta(minutes=48),
        "session_submitted_at": start + timedelta(minutes=48),
        "finalized_at": start + timedelta(minutes=49),
        "started_at": start, "deadline": start + timedelta(minutes=60),
    }
    return paper, rows, set(range(1, 61)), {"graded-failure:99:1"}, subjective


def test_complete_synthetic_shape_proves_predicate_only_not_any_real_qa_database():
    data = sample()
    result = assess_attempt(
        data[0], data[1], qualified_ids=data[2], fsrs_failure_keys=data[3],
        verified_ai_grade_ids=data[4],
    )
    assert result["real_60q_end_to_end_verified"] is True
    assert result["questions"] == 60
    assert result["max_points"] == 150
    assert result["duration_seconds"] == 3600
    assert result["independently_model_graded_subjective_questions"] == 10
    # This is deliberately only an isolated synthetic unit-test fixture.
    assert all(
        r["feedback"].get("model_id") == "synthetic_fake_do_not_publish"
        for r in data[1] if r["snapshot"]["question_type"] in ("term", "short_answer")
    )


@pytest.mark.parametrize("mutate", [
    lambda a: a[1].pop(),
    lambda a: a[1][1].update(position=1),
    lambda a: a[1][1].update(question_id=1),
    lambda a: a[1][0].update(points=None),
    lambda a: a[1][0].update(points=999),
    lambda a: a[1][0].update(answer="{}"),
    lambda a: a[1][0]["snapshot"].update(max_points=3),
    lambda a: a[1][0]["snapshot"]["evidence"][0].update(source_sha256="0"*64),
    lambda a: a[2].remove(1),
    lambda a: a[3].clear(),
    lambda a: a[4].clear(),
    lambda a: a[0].update(finalized_at=None),
    lambda a: a[0].update(deadline=a[0]["started_at"] + timedelta(minutes=61)),
    lambda a: a[0]["blueprint"].update(duration_seconds=1800),
])
def test_fail_closed_on_missing_real_grade_fsrs_score_source_or_time(mutate):
    data = list(deepcopy(sample()))
    mutate(data)
    report = assess_attempt(
        data[0], data[1], qualified_ids=data[2], fsrs_failure_keys=data[3],
        verified_ai_grade_ids=data[4],
    )
    assert report["real_60q_end_to_end_verified"] is False


def test_wrong_versions_do_not_pretend_legacy_papers_were_regraded():
    a = list(sample())
    a[0]["blueprint"]["specification"] = "xiaohan-150-60-v1"
    assert assess_attempt(
        a[0], a[1], qualified_ids=a[2], fsrs_failure_keys=a[3],
        verified_ai_grade_ids=a[4],
    )["reason"] == "not_new_60q_specification"
