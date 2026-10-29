"""Read-only six-source chapter coverage with synthetic, non-user page text."""
import json

import pytest
from app.offline_chapter_coverage import report
from test_collection_seed import synthetic_collection
from app.source_policy import COLLECTION


def test_four_pinned_textbooks_chapter_coverage_has_exact_page_denominators(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    result = report(root, lock_path=lock)
    assert result["all_six_source_sha256_preflight_passed"] is True
    assert result["ocr_pages_total"] == 1915
    assert len(result["books"]) == 4
    for book in result["books"]:
        spec = next(s for s in COLLECTION["files"] if s["key"] == book["key"])
        assert book["ocr_pages_total"] == spec["expected_pages"]
        assert book["mapped_chapter_count"] >= 1
        assert book["pages_with_any_mapped_chapter"] <= book["ocr_pages_total"]
        assert book["first_mapped_body_ocr_page"] is not None
        assert book["chapter_body_has_no_unmapped_ocr_pages"]
        assert book["unmapped_at_or_after_first_chapter"] == []
        assert all(p < book["first_mapped_body_ocr_page"]
                   for p in book["unmapped_before_first_chapter"])
        assert all(0 < chapter["first_ocr_page"] <= chapter["last_ocr_page"]
                   <= spec["expected_pages"] for chapter in book["chapters"])
        assert all(chapter["scientific_answer_verified"] is False
                   for chapter in book["chapters"])
    assert result["scientific_content_verified"] is False
    assert result["postgresql_readback_verified"] is False
    assert result["exam_topic_alignment_verified"] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert "Synthetic evidence paragraph" not in serialized
    assert "/tmp/" not in serialized


def test_changed_source_bytes_block_chapter_mapping_before_any_report(tmp_path):
    root, lock = synthetic_collection(tmp_path)
    textbook = next(x for x in COLLECTION["files"] if x["key"] == "cell_biology")
    target = root / textbook["relative_path"]
    target.write_bytes(target.read_bytes() + b"not the pinned source")
    with pytest.raises(ValueError, match="SHA-256"):
        report(root, lock_path=lock)
