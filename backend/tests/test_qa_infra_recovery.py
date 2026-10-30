"""ENOSPC recovery requires exact evidence and no possible provider receipt."""
from pathlib import Path

from app import qa_infra_recovery as r


def test_reject_missing_and_linked_private_directory(tmp_path):
    job = {"id": 7, "attempts": 2}
    assert r.receipt_or_unsafe_path_exists(tmp_path / "missing", job)
    home = tmp_path / "job-7-attempt-1"
    home.mkdir()
    assert not r.receipt_or_unsafe_path_exists(tmp_path, job)
    (home / "provider-receipt").mkdir()
    assert r.receipt_or_unsafe_path_exists(tmp_path, job)


def test_audit_is_preserved_and_prevents_replay(tmp_path):
    home = tmp_path / "job-7-attempt-2"
    home.mkdir()
    (home / "task-audit.json").write_text('{"sent":true}')
    assert r.receipt_or_unsafe_path_exists(tmp_path, {"id": 7, "attempts": 2})


def test_evidence_requires_fresh_exact_snapshot(monkeypatch):
    item = dict(unit_id=5, unit_sha256="abc", file_id="known", source_sha256="sha")
    monkeypatch.setattr(r, "snapshot_unit", lambda conn, unit_id: dict(item))
    monkeypatch.setattr(r, "pinned_source_bytes", lambda file_id, sha: True)
    assert r.evidence_is_current(None, {"kind": "textbook_analysis", "payload": {"evidence": item}})
    changed = {**item, "unit_sha256": "tampered"}
    assert not r.evidence_is_current(None, {"kind": "textbook_analysis", "payload": {"evidence": changed}})
    assert not r.evidence_is_current(None, {"kind": "generation", "payload": {"evidence": [item]}})


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, sql, args):
        self.queries.append((sql, args))
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return None


def test_single_source_safe_replay_keeps_original(monkeypatch, tmp_path):
    item = dict(unit_id=5, unit_sha256="abc", file_id="known", source_sha256="sha")
    monkeypatch.setattr(r, "snapshot_unit", lambda conn, unit_id: dict(item))
    monkeypatch.setattr(r, "pinned_source_bytes", lambda file_id, sha: True)
    created = []
    monkeypatch.setattr(r, "enqueue", lambda *args: created.append(args))
    old = dict(id=17, attempts=3, kind="textbook_analysis", job_key="knowledge:17",
               payload={"evidence": item})
    db = FakeConn([old])
    assert r.replay_enospc(db, 3, tmp_path) == 1
    assert created[0][2] == "knowledge:17" + r.RECOVERY_SUFFIX
    assert db.queries[0][1][0] == 3
    assert "provider_request_confirmed=true" in db.queries[0][0]
    assert "errno" in db.queries[0][0]


def test_unknown_or_semantic_failure_never_selected(monkeypatch, tmp_path):
    # The database predicate is the first barrier; assert it excludes
    # ambiguous historical OSError and all invalid science.
    db = FakeConn([])
    assert r.replay_enospc(db, 3, tmp_path) == 0
    sql = db.queries[0][0]
    assert "error_code='OSError'" in sql
    assert "'28'" in sql
    assert "state='blocked'" in sql


def test_owner_pause_only_resumes_signed_fresh_unexhausted_tasks(monkeypatch):
    from app import qa_automation
    evidence = {"unit_id": 1, "unit_sha256": "sha", "file_id": "f", "source_sha256": "source"}
    monkeypatch.setattr(qa_automation, "check_job", lambda j: None)
    monkeypatch.setattr(r, "evidence_is_current", lambda c, j: True)
    row = dict(id=42, kind="textbook_analysis", attempts=0,
               settings_snapshot={"retry_limit": 2}, payload={"evidence": evidence})
    conn = FakeConn([row])
    assert r.requeue_owner_paused(conn, 9, "signed-scope") == 1
    sql = conn.queries[0][0]
    assert "error_code='automation_stopped'" in sql
    assert "qa_automation" in sql
    assert conn.queries[-1][1] == (42,)
    assert "next_retry=clock_timestamp()" in conn.queries[-1][0]


def test_owner_pause_does_not_reset_attempts_or_replay_stale(monkeypatch):
    from app import qa_automation
    monkeypatch.setattr(qa_automation, "check_job", lambda j: None)
    monkeypatch.setattr(r, "evidence_is_current", lambda c, j: False)
    row = dict(id=42, kind="textbook_analysis", attempts=0,
               settings_snapshot={"retry_limit": 2})
    conn = FakeConn([row])
    assert r.requeue_owner_paused(conn, 9, "scope") == 0
    assert len(conn.queries) == 1
    monkeypatch.setattr(r, "evidence_is_current", lambda c, j: True)
    row["attempts"] = 3
    conn = FakeConn([row])
    assert r.requeue_owner_paused(conn, 9, "scope") == 0
    assert len(conn.queries) == 1
