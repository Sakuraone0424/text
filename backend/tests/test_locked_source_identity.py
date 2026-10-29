"""Exact-source allowlist for new automatic generation, not a human review.

These tests inspect the checked-in manifest only; they never use a production
database, trigger a model, or copy any of the six copyrighted source bodies.
"""
import json

from app.source_policy import COLLECTION, FILES, pinned_source_bytes, pinned_science_evidence
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


def test_completed_generation_job_rejects_unpinned_sources_before_db_or_publication():
    import pytest
    from app.auto_generation import accept

    locked = json.loads(
        (Path(__file__).parents[1] / "app" / "ocr_source_sha256_lock.json").read_text(
            encoding="utf-8"
        )
    )
    textbook = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_upper")
    exam = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_exams")
    good_book = {
        "file_id": textbook["file_id"],
        "source_sha256": locked["files"][textbook["key"]]["sha256"],
    }
    good_exam = {
        "file_id": exam["file_id"],
        "source_sha256": locked["files"][exam["key"]]["sha256"],
    }
    for evidence, style in (
        ([{**good_book, "source_sha256": "0" * 64}], [good_exam]),
        ([good_book], [{**good_exam, "source_sha256": "0" * 64}]),
        ([], [good_exam]),
        ([good_book], []),
    ):
        fake_completed_job = {
            "state": "succeeded",
            "result": {"real_api": True, "response": {"question": {}}},
            "payload": {"evidence": evidence, "style": style},
        }
        with pytest.raises(ValueError, match="generation_source_not_in_locked"):
            accept(None, fake_completed_job)


def test_scientific_evidence_must_only_use_the_four_locked_ocr_textbooks():
    locked = json.loads(
        (Path(__file__).parents[1] / "app" / "ocr_source_sha256_lock.json").read_text(
            encoding="utf-8"
        )
    )
    upper = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_upper")
    notes = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_notes")
    exams = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_exams")
    book = {
        "file_id": upper["file_id"],
        "source_sha256": locked["files"][upper["key"]]["sha256"],
    }
    note = {
        "file_id": notes["file_id"],
        "source_sha256": locked["files"][notes["key"]]["sha256"],
    }
    real_exam = {
        "file_id": exams["file_id"],
        "source_sha256": locked["files"][exams["key"]]["sha256"],
    }
    assert pinned_science_evidence({"evidence": [book, note]})
    assert not pinned_science_evidence({"evidence": []})
    assert not pinned_science_evidence({"evidence": [real_exam]})
    assert not pinned_science_evidence({"evidence": [book, real_exam]})
    assert not pinned_science_evidence({"evidence": [{**book, "source_sha256": "0" * 64}]})
    assert not pinned_science_evidence({"evidence": [{"file_id": upper["file_id"]}]})


def test_generation_accept_rejects_exam_as_answer_and_cross_subject_source():
    import pytest
    from app.auto_generation import accept

    lock = json.loads(
        (Path(__file__).parents[1] / "app" / "ocr_source_sha256_lock.json").read_text(
            encoding="utf-8"
        )
    )
    upper = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_upper")
    exam = next(x for x in COLLECTION["files"] if x["key"] == "biochemistry_exams")
    book = dict(file_id=upper["file_id"],
                source_sha256=lock["files"][upper["key"]]["sha256"],
                source_kind="textbook", subject="biochemistry", quality_flags=[])
    past = dict(file_id=exam["file_id"],
                source_sha256=lock["files"][exam["key"]]["sha256"],
                source_kind="exam_transcription", subject="biochemistry",
                quality_flags=[])
    mutations = [
        ([past], [past]),  # A genuine past paper is NOT scientific answer evidence.
        ([book], [book]),  # A textbook is NOT a genuine past exam style anchor.
        ([dict(book, subject="cell_biology")], [past]),
        ([book], [dict(past, subject="cell_biology")]),
        ([book], [dict(past, quality_flags=["docx_table_requires_review"])]),
        ([book], [dict(past, quality_flags=["ocr_uncertain"])]),
    ]
    for evidence, style in mutations:
        job = dict(
            state="succeeded",
            result={"real_api": True, "response": {"question": {}}},
            payload={"subject": "biochemistry", "evidence": evidence, "style": style},
        )
        with pytest.raises(ValueError, match="generation_source_not_in_locked"):
            accept(None, job)
