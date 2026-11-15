"""Real transcription planner follows the persisted worklist algorithm."""
from app import auto_exams


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        if "count(*)" in sql:
            return FakeResult({"n": 0})
        if "FROM evidence_review_work" in sql:
            return FakeResult({
                "work_key": "actual-source-work", "source_unit_ids": [1],
                "evidence": {"year": 2024, "concerns": []},
            })
        raise AssertionError("unexpected query")


def test_exam_analysis_uses_current_stored_algorithm(monkeypatch):
    monkeypatch.setattr(auto_exams, "stored_work_version",
                        lambda conn: "evidence-work/1.7")
    monkeypatch.setattr(auto_exams, "snapshot_unit",
                        lambda conn, uid: {"unit_id": uid, "source_kind": "exam_transcription"})
    queued = []
    monkeypatch.setattr(auto_exams, "enqueue",
                        lambda conn, uid, key, kind, payload: queued.append((key, kind, payload)))
    conn = FakeConn()
    auto_exams.plan(conn, 7)
    assert len(queued) == 1
    assert queued[0][1] == "exam_analysis"
    assert queued[0][2]["declared_year"] == 2024
    assert queued[0][2]["evidence"][0]["unit_id"] == 1
    assert "algorithm=%s" in conn.calls[1][0]
    assert conn.calls[1][1] == ("evidence-work/1.7",)
    assert "evidence-work/1.6" not in conn.calls[1][0]
