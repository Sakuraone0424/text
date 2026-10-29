"""Private provenance review queue, synthetic fixtures only."""
import json
import sqlite3

import pytest

from app.offline_source_review_queue import build
from app.source_policy import COLLECTION
from test_collection_seed import synthetic_collection


def test_six_source_queue_tracks_real_parser_risks_without_approving(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    output = tmp_path / "review_queue.sqlite"
    report = build(root, output, lock_path=lock)
    assert report["source_files"] == 6
    assert report["original_sha256_verified"] is True
    assert report["ocr_page_markers"] == 1915
    assert report["text_units"] > 1915
    assert report["postcommit_queue_readback_verified"]
    assert not report["scientifically_verified"]
    assert not report["qa_postgresql_imported"]
    assert report["independent_ai_reviews_completed"] == 0
    with sqlite3.connect(f"file:{output}?mode=ro",uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT count(*) FROM original_sources").fetchone()[0] == 6
        assert conn.execute("SELECT sum(ocr_pages) FROM original_sources").fetchone()[0] == 1915
        assert conn.execute("SELECT count(*) FROM review_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM review_issues").fetchone()[0] == report["pending_review_issue_count"]
        assert conn.execute("SELECT count(*) FROM review_progress WHERE review_status='pending'").fetchone()[0] == report["pending_review_issue_count"]
        if report["pending_review_issue_count"]:
            assert conn.execute("SELECT count(*) FROM review_issues WHERE status='pending'").fetchone()[0] == report["pending_review_issue_count"]
    assert "Synthetic evidence paragraph" not in json.dumps(report, ensure_ascii=False)
    assert str(root) not in json.dumps(report, ensure_ascii=False)


def test_modified_locked_original_refuses_review_queue(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    item = next(x for x in COLLECTION["files"] if x["key"] == "cell_biology")
    path = root / item["relative_path"]
    path.write_bytes(path.read_bytes()+b"tampered")
    output = tmp_path / "not_created.sqlite"
    with pytest.raises(ValueError,match="SHA-256"):
        build(root,output,lock_path=lock)
    assert not output.exists()


def test_existing_review_queue_never_overwritten(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    output = tmp_path / "previous.sqlite"
    output.write_bytes(b"existing user work")
    with pytest.raises(FileExistsError):
        build(root,output,lock_path=lock)
    assert output.read_bytes() == b"existing user work"


def test_review_status_is_not_auto_approved_by_direct_update(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    output = tmp_path / "pending.sqlite"
    report=build(root,output,lock_path=lock)
    assert report["pending_review_issue_count"] > 0
    with sqlite3.connect(output) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE review_issues SET status='approved' WHERE issue_key=("
                "SELECT issue_key FROM review_issues LIMIT 1)"
            )
        assert conn.execute("SELECT count(*) FROM review_events").fetchone()[0] == 0
