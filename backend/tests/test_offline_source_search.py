"""Full-text six-source private QA cache, synthetic fixtures only."""
import json
from hashlib import sha256
import sqlite3

import pytest

from app.offline_source_search import build, search
from app.source_policy import COLLECTION
from test_collection_seed import synthetic_collection


def test_offline_private_index_uses_all_six_and_verifies_after_commit(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    index = tmp_path / "private-six-source.sqlite"
    report = build(root, index, lock_path=lock)
    assert report["source_files"] == 6
    assert report["ocr_pages"] == 1915
    assert report["postcommit_sqlite_readback_verified"] is True
    assert report["postgresql_source_imported"] is False
    assert report["scientific_answers_verified"] is False
    assert report["independent_ai_rounds_completed"] is False
    assert report["unit_count"] > 1915
    assert index.exists()
    rows = search(index, "昌增益", limit=3)
    assert len(rows) == 3
    assert all(x["ocr_page"] is not None and x["source_key"] for x in rows)
    with sqlite3.connect(f"file:{index}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 6
        assert conn.execute(
            "SELECT sum(ocr_pages) FROM sources"
        ).fetchone()[0] == 1915
        assert conn.execute(
            "SELECT count(*) FROM units WHERE scientifically_approved != 0"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM fulltext WHERE fulltext MATCH '昌增益'"
        ).fetchone()[0] > 0
    metadata = json.dumps(report, ensure_ascii=False)
    assert "Synthetic evidence paragraph" not in metadata
    assert str(root) not in metadata


def test_changed_original_prevents_any_private_index_creation(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    item = next(x for x in COLLECTION["files"] if x["key"] == "cell_biology")
    (root / item["relative_path"]).write_bytes(
        (root / item["relative_path"]).read_bytes() + b"changed bytes"
    )
    db = tmp_path / "never-created.sqlite"
    with pytest.raises(ValueError, match="SHA-256"):
        build(root, db, lock_path=lock)
    assert not db.exists()


def test_existing_index_is_not_overwritten(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    db = tmp_path / "existing.sqlite"
    db.write_bytes(b"previous private report")
    with pytest.raises(FileExistsError):
        build(root, db, lock_path=lock)
    assert db.read_bytes() == b"previous private report"


def test_original_source_book_flags_remain_pending_not_scientifically_approved(
    tmp_path,
):
    root, lock = synthetic_collection(tmp_path)
    item = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_notes")
    target = root / item["relative_path"]
    target.write_text(target.read_text(encoding="utf-8").replace(
        "===== page-001 =====", "===== page-001 =====\n过氧化物酶体定位需参考原图 ATP", 1
    ), encoding="utf-8")
    manifest = json.loads(lock.read_text(encoding="utf-8"))
    raw = target.read_bytes()
    manifest["files"][item["key"]].update(
        sha256=sha256(raw).hexdigest(), bytes=len(raw)
    )
    lock.write_text(json.dumps(manifest), encoding="utf-8")
    index = tmp_path / "private.sqlite"
    build(root, index, lock_path=lock)
    results = search(index, "过氧化物酶体", limit=5)
    assert results
    assert results[0]["source_key"] == "biochemistry_notes"
    assert results[0]["ocr_page"] == 1
    assert "image_dependent" in json.loads(results[0]["risk_flags_json"])
    assert search(index, "atp", limit=5)  # ASCII lower-case finds original ATP
    with pytest.raises(ValueError):
        search(index, "ATP" * 100)
    with sqlite3.connect(index) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE units SET scientifically_approved=1 WHERE id=(SELECT id FROM units LIMIT 1)"
            )


def test_publish_does_not_replace_index_created_during_build(tmp_path, monkeypatch):
    from app import offline_source_search as module

    root, lock = synthetic_collection(tmp_path)
    db = tmp_path / "concurrent.sqlite"
    real_link = module.os.link

    def competing_writer(src, dst):
        dst.write_bytes(b"existing-winner")
        return real_link(src, dst)

    monkeypatch.setattr(module.os, "link", competing_writer)
    with pytest.raises(FileExistsError):
        build(root, db, lock_path=lock)
    assert db.read_bytes() == b"existing-winner"
    assert not (tmp_path / "concurrent.sqlite.partial").exists()
