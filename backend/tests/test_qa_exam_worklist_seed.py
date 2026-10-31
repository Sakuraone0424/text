"""The autonomous worker can seed only pending genuine-exam QA work."""
from app import qa_exam_worklist_seed as w


class FakeConn:
    def __init__(self, existing=0, source=3):
        self.counts = [existing, source]
        self.queries = []

    def execute(self, sql, args=()):
        self.queries.append((sql, args))
        return self

    def fetchone(self):
        return {"n": self.counts.pop(0)}


def test_existing_pending_exam_queue_is_immutable():
    conn = FakeConn(existing=42)
    result = w.ensure(conn, builder=lambda c: (_ for _ in ()).throw(AssertionError()))
    assert result["already_present"] == 42
    assert result["new"] == 0
    assert len(conn.queries) == 1


def test_no_transcription_source_is_not_synthesized():
    conn = FakeConn(existing=0, source=0)
    result = w.ensure(conn, builder=lambda c: (_ for _ in ()).throw(AssertionError()))
    assert result["new"] == 0


def test_only_fingerprinted_pending_exam_work_is_persisted():
    conn = FakeConn()
    rows = [
        {"algorithm": w.VERSION, "kind": "exam_question", "status": "pending", "work_key": "one"},
        {"algorithm": w.VERSION, "kind": "unit_review", "status": "pending", "work_key": "two"},
        {"algorithm": w.VERSION, "kind": "exam_question", "status": "approved", "work_key": "three"},
        {"algorithm": "evidence-work/old", "kind": "exam_question", "status": "pending", "work_key": "four"},
    ]
    recorded = []
    result = w.ensure(conn, builder=lambda c: {"work": rows},
                      persister=lambda c, items: recorded.extend(items) or len(items))
    assert result["new"] == 1
    assert len(recorded) == 1
    assert recorded[0]["work_key"] == "one"
    assert conn.queries[0][1] == (w.VERSION,)
