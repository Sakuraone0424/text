"""Pending worklist tests: no AI, no approvals and no rewritten source text."""
import pytest
from app import qa_graph_worklist_seed as seed

CELL_FILE_ID = next(
    file_id for file_id, item in seed.FILES.items()
    if item["key"] == "cell_biology"
)


def work(key, kind="unit_review", *, role="textbook", priority=50):
    return dict(
        algorithm=seed.VERSION,kind=kind,status="pending",
        subject="cell_biology",source_unit_ids=[11],
        evidence={
            "source_role":role,
            "reference":{"file_id":CELL_FILE_ID,"source_sha256":"a"*64}
        },
        work_key=key,priority=priority,
    )


def test_science_tasks_are_pending_only(monkeypatch):
    monkeypatch.setattr(seed, "pinned_source_bytes", lambda a,b: b=="a"*64)
    assert seed.eligible(work("good"))
    assert not seed.eligible(work("exam", role="exam_transcription"))
    assert not seed.eligible({**work("ready"), "status":"approved"})
    assert not seed.eligible({**work("wrong"), "algorithm":"old-version"})


def test_bounded_idempotent_science_worklist(monkeypatch):
    monkeypatch.setattr(seed, "pinned_source_bytes", lambda a,b: True)
    stored = {"already"}
    class Conn:
        def execute(self,query,args):
            assert "SELECT work_key" in query
            return self
        def fetchall(self):
            return [{"work_key": key} for key in stored]
    saved=[]
    def persist(_conn, tasks):
        saved.extend(tasks)
        stored.update(x["work_key"] for x in tasks)
        return len(tasks)
    tasks=[work("already"),work("risk",priority=90),work("clean",priority=20)]
    result=seed.ensure(Conn(),builder=lambda conn:{"work":tasks},
                       persister=persist,limit=1)
    assert [x["work_key"] for x in saved] == ["risk"]
    assert result["existing_tasks"] == 1
    assert result["new_pending_tasks"] == 1
    assert result["remaining_unseeded"] == 1
    assert result["scientifically_approved_nodes"] == 0
    assert result["all_pending_reviews_persisted"] is False


def test_duplicate_fingerprint_is_refused(monkeypatch):
    monkeypatch.setattr(seed,"pinned_source_bytes",lambda a,b:True)
    with pytest.raises(ValueError,match="duplicate"):
        seed.ensure(None,builder=lambda c:{"work":[work("repeat"),work("repeat")]})


def test_invalid_unbounded_job_rejected():
    with pytest.raises(ValueError,match="limit"):
        seed.ensure(None,limit=999999)


def test_complete_source_review_queue_has_exact_readback(monkeypatch):
    monkeypatch.setattr(seed, "pinned_source_bytes", lambda a, b: True)
    tasks = [work("one"), work("two"), work("three")]
    storage = {"one"}
    class Conn:
        def execute(self, query, args):
            assert "SELECT work_key" in query
            return self
        def fetchall(self):
            return [{"work_key": key} for key in storage]
    def persister(_conn, pending):
        storage.update(item["work_key"] for item in pending)
        return len(pending)
    report=seed.ensure(Conn(),builder=lambda _conn:{"work":tasks},persister=persister)
    assert report["new_pending_tasks"] == 2
    assert report["remaining_unseeded"] == 0
    assert report["all_pending_reviews_persisted"] is True


def test_incomplete_write_is_not_reported_as_complete(monkeypatch):
    monkeypatch.setattr(seed,"pinned_source_bytes",lambda a,b:True)
    class Conn:
        def execute(self,query,args):
            return self
        def fetchall(self):
            return []
    with pytest.raises(ValueError, match="postwrite_count"):
        seed.ensure(Conn(),builder=lambda conn:{"work":[work("one")]},
                    persister=lambda conn,tasks:1)
