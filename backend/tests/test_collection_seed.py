"""Six private source files: pinned preflight and atomic isolated import tests.

All test files are synthetic, not the learner's copyrighted originals.
"""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import json
import shutil

import pytest

from app import collection_seed as seed
from app.source_policy import COLLECTION
from test_ocr_only import material


def synthetic_collection(tmp_path):
    root = tmp_path / "six"
    lock = {"collection_version": COLLECTION["version"], "files": {}}
    for item in COLLECTION["files"]:
        path, _metadata = material(tmp_path, item["key"])
        dest = root / item["relative_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        raw = dest.read_bytes()
        lock["files"][item["key"]] = {
            "sha256": sha256(raw).hexdigest(), "bytes": len(raw)
        }
    path = tmp_path / "sha_lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    return root, path


def test_preflight_requires_every_actual_file_and_lock(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    items = seed.preflight(root, lock_path=lock)
    assert len(items) == 6
    assert sum(i["ocr_page_markers"] for i in items) == 1915
    assert all(i["sha256"] and i["bytes"] for i in items)
    redacted = seed.safe_report(items)
    assert "path" not in redacted["files"][0]
    assert "metadata" not in redacted["files"][0]
    assert redacted["scientific_accuracy_verified"] is False


def test_preflight_detects_any_changed_source_byte(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    item = next(x for x in COLLECTION["files"] if x["key"] == "cell_biology")
    file = root / item["relative_path"]
    file.write_bytes(file.read_bytes() + b"unauthorized rewrite")
    with pytest.raises(ValueError, match="SHA-256"):
        seed.preflight(root, lock_path=lock)


def test_preflight_detects_missing_file(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    item = COLLECTION["files"][0]
    (root / item["relative_path"]).unlink()
    with pytest.raises(ValueError, match="Missing or symlinked"):
        seed.preflight(root, lock_path=lock)


def test_write_to_isolated_test_db_reads_back_all_six_without_synthetic_approval(
    db, tmp_path, monkeypatch
):
    root, lock = synthetic_collection(tmp_path)
    items = seed.preflight(root, lock_path=lock)

    @contextmanager
    def test_schema(_schema, _target):
        yield db

    monkeypatch.setattr(seed, "isolated_db", test_schema)
    report = tmp_path / "readback.json"
    result = seed.import_isolated(
        items, "source_staging_synthetic_unit", "development", report
    )
    assert result["status"] == "committed_to_isolated_schema"
    assert result["database_integrity"]["imported_files"] == 6
    assert result["database_integrity"]["integrity_passed_files"] == 6
    assert len(result["imported"]) == 6
    assert report.exists()
    for row in result["imported"]:
        assert db.execute(
            "SELECT count(*) AS n FROM source_units WHERE document_id=%s",
            (row["document_id"],),
        ).fetchone()["n"] > 0
    assert db.execute("SELECT count(*) AS n FROM reviewed_questions").fetchone()["n"] == 0


def test_invalid_production_import_is_refused_before_database_access(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    with pytest.raises(ValueError, match="production"):
        seed.import_isolated([], "source_staging_test", "staging", tmp_path / "report.json")
