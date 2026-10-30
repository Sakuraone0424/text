"""QA-only independent restore evidence cannot be silently recycled."""
import pytest
from app.qa_backup_worker import historical_restore_status


def _receipt():
    return {
        "restore_executed": True,
        "independent_readback": True,
        "backup_sha256": "a" * 64,
        "target_system_id": "separate-restore-cluster",
        "tables_verified": 81,
        "migrations_verified": 23,
        "source_files_verified": 6,
    }


def test_new_daily_snapshot_is_not_falsely_restored():
    result = historical_restore_status(_receipt(), "b" * 64, "separate-restore-cluster")
    assert result["status"] == "historic_restore_only_latest_snapshot_unverified"
    assert result["historic_restore_proof"] is True
    assert result["independently_restored_current_snapshot"] is False
    assert result["fresh_restore_executed_this_tick"] is False
    assert result["restore_executed"] is False
    assert result["prior_verified_tables"] == 81


def test_same_snapshot_still_is_not_a_fresh_restore():
    result = historical_restore_status(_receipt(), "a" * 64, "separate-restore-cluster")
    assert result["independently_restored_current_snapshot"] is True
    assert result["fresh_restore_executed_this_tick"] is False
    assert result["restore_executed"] is False


def test_stale_or_unproved_receipt_cannot_approve_restore():
    receipt = {**_receipt(), "independent_readback": False}
    result = historical_restore_status(receipt, "a" * 64, "separate-restore-cluster")
    assert result["historic_restore_proof"] is False
    assert result["independently_restored_current_snapshot"] is False


def test_receipt_for_wrong_cluster_refused():
    with pytest.raises(PermissionError, match="different_cluster"):
        historical_restore_status(_receipt(), "a" * 64, "unknown-target")


def test_wrong_restore_cluster_rejected_before_untrusted_or_missing_snapshot_index(tmp_path,monkeypatch):
    import json
    from app import qa_backup_worker as worker

    monkeypatch.setattr(worker, "ROOT", tmp_path)
    (tmp_path/"independent-restore-receipt.json").write_text(
        json.dumps({"target_system_id":"old-cluster","backup_sha256":"a"*64})
    )
    with pytest.raises(PermissionError, match="different_cluster"):
        worker.restore_once(tmp_path, {}, {}, "new-cluster")
