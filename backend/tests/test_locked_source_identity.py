"""Exact-source allowlist for new automatic generation, not a human review.

These tests inspect the checked-in manifest only; they never use a production
database, trigger a model, or copy any of the six copyrighted source bodies.
"""
import json

from app.source_policy import COLLECTION, FILES, pinned_source_bytes
from pathlib import Path


def test_exact_six_pinned_source_hashes_are_the_only_accepted_versions():
    locked = json.loads(
        (Path(__file__).parents[1] / "app" / "ocr_source_sha256_lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert locked["collection_version"] == COLLECTION["version"]
    assert len(FILES) == len(locked["files"]) == 6
    for item in COLLECTION["files"]:
        digest = locked["files"][item["key"]]["sha256"]
        assert pinned_source_bytes(item["file_id"], digest)
        assert not pinned_source_bytes(item["file_id"], "0" * 64)
        assert not pinned_source_bytes(item["file_id"], "")
        assert not pinned_source_bytes(item["file_id"], None)
    assert not pinned_source_bytes("unapproved-source-file-id", "a" * 64)


def test_source_id_and_valid_hash_cannot_be_mixed_across_two_books():
    locked = json.loads(
        (Path(__file__).parents[1] / "app" / "ocr_source_sha256_lock.json").read_text(
            encoding="utf-8"
        )
    )
    upper = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_upper")
    lower_digest = locked["files"]["biochemistry_lower"]["sha256"]
    assert not pinned_source_bytes(upper["file_id"], lower_digest)
