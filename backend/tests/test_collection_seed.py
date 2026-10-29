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
    from app.source_bibliography import BIBLIOGRAPHY
    for item in COLLECTION["files"]:
        # Synthetic test headers carry the tokens used to test verification.
        cover = (" ".join(BIBLIOGRAPHY[item["key"]]["cover_tokens"])
                 if item["kind"] == "textbook" else None)
        path, _metadata = material(tmp_path, item["key"], content=cover)
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
    def test_schema(_schema, _target, **kwargs):
        yield db

    monkeypatch.setattr(seed, "isolated_db", test_schema)
    report = tmp_path / "readback.json"
    result = seed.import_isolated(
        items, "source_staging_synthetic_unit", "development", report
    )
    assert result["status"] == "committed_to_isolated_schema"
    assert result["database_integrity"]["imported_files"] == 6
    assert result["database_integrity"]["integrity_passed_files"] == 6
    assert result["database_integrity"]["independent_post_commit_readback_verified"]
    assert len(result["imported"]) == 6
    assert report.exists()
    from app.source_bibliography import BIBLIOGRAPHY
    for row in result["imported"]:
        assert db.execute(
            "SELECT count(*) AS n FROM source_units WHERE document_id=%s",
            (row["document_id"],),
        ).fetchone()["n"] > 0
        if row["key"] in BIBLIOGRAPHY:
            stored = db.execute(
                "SELECT metadata FROM source_documents WHERE id=%s",
                (row["document_id"],),
            ).fetchone()["metadata"]
            assert stored["authors"] == BIBLIOGRAPHY[row["key"]]["authors"]
            assert stored["edition"] == BIBLIOGRAPHY[row["key"]]["edition"]
    assert db.execute("SELECT count(*) AS n FROM reviewed_questions").fetchone()["n"] == 0


def test_invalid_production_import_is_refused_before_database_access(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    with pytest.raises(ValueError, match="production"):
        seed.import_isolated([], "source_staging_test", "staging", tmp_path / "report.json")


def test_pinned_docx_media_count_must_match_actual_original(tmp_path):
    root, lock_path = synthetic_collection(tmp_path)
    pinned = json.loads(lock_path.read_text(encoding="utf-8"))
    pinned["files"]["biochemistry_exams"]["embedded_media_objects"] = 27
    lock_path.write_text(json.dumps(pinned), encoding="utf-8")
    with pytest.raises(ValueError, match="Pinned DOCX media identity/count mismatch"):
        seed.preflight(root, lock_path=lock_path)


def test_synthetic_docx_media_inventory_is_not_claimed_transcribed(tmp_path):
    root, lock_path = synthetic_collection(tmp_path)
    items = seed.preflight(root, lock_path=lock_path)
    exams = [item for item in items if item["source_kind"] == "exam_transcription"]
    assert len(exams) == 2
    for item in exams:
        media = item["exam_media_inventory"]
        assert media["source_sha256"] == item["sha256"]
        assert media["images_scientifically_verified"] is False
        assert media["question_image_anchors_verified"] is False


def test_commit_failing_never_writes_false_success_report(tmp_path, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def fails_on_commit(_schema, _target, **kwargs):
        class FakeConnection:
            def execute(self, *args, **kw):
                raise RuntimeError("simulated-db-write-error")
        yield FakeConnection()
        raise RuntimeError("simulated-commit-error")

    monkeypatch.setattr(seed, "isolated_db", fails_on_commit)
    report_path = tmp_path / "must-not-exist.json"
    with pytest.raises(RuntimeError):
        seed.import_isolated([], "source_staging_test", "development", report_path)
    assert not report_path.exists()


def test_atomic_fresh_qa_schema_migrates_before_six_source_import(
    db, tmp_path, monkeypatch
):
    import migrate

    root, lock = synthetic_collection(tmp_path)
    items = seed.preflight(root, lock_path=lock)
    calls = []

    @contextmanager
    def capture_qa(_schema, _target, **options):
        calls.append(("database", options))
        yield db

    def capture_migrations(conn):
        calls.append(("migrations", conn is db))

    monkeypatch.setattr(seed, "isolated_db", capture_qa)
    monkeypatch.setattr(migrate, "apply_migrations", capture_migrations)
    result = seed.import_isolated(
        items, "source_staging_fresh_private_qa", "staging",
        tmp_path / "atomic-qa.json", initialize_schema=True
    )
    assert result["schema_initialized_in_same_transaction"] is True
    assert result["database_integrity"]["independent_post_commit_readback_verified"]
    assert calls == [
        ("database", {"create": True}),
        ("migrations", True),
        ("database", {"readonly": True}),
    ]


def test_cannot_initialize_fresh_qa_on_nonstaging_cli(tmp_path):
    with pytest.raises(SystemExit):
        seed.main([
            "--directory", str(tmp_path),
            "--report", str(tmp_path / "bad.json"),
            "--initialize-schema",
            "--write-isolated",
            "--schema", "source_staging_forbidden",
            "--target", "development",
        ])
    assert not (tmp_path / "bad.json").exists()
