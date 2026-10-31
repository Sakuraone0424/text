"""Publisher's conditional defect-replacement notice is not a verified missing OCR page."""
import pytest
from app import qa_publisher_notice_triage as notice


def rows():
    return [
        {"source_key": key, "ocr_page": page, "risk_flag": "transcription_gap_reported",
         "file_id": next(fid for fid, entry in notice.FILES.items() if entry["key"] == key),
         "source_sha256": "a"*64,
         "text": "本书如有缺页、倒页、脱页等质量问题，请到所购图书销售部门联系调换"}
        for key,page in notice.EXPECTED.items()
    ]


def test_three_exact_source_warranty_notices_do_not_become_missing_pages(monkeypatch):
    monkeypatch.setattr(notice, "pinned_source_bytes", lambda fid, sha: True)
    result = notice.summarize(rows())
    assert result["conditional_publisher_notice_ocr_regex_hits"] == 3
    assert result["actual_missing_pages_ruled_out"] is False
    assert result["risk_ledger_state_unchanged"] is True
    assert result["scientific_or_visual_approvals"] == 0


def test_actual_missing_page_claim_or_new_document_fails_closed(monkeypatch):
    monkeypatch.setattr(notice, "pinned_source_bytes", lambda fid, sha: True)
    r = rows()
    r[0]["text"] = "OCR第5页缺页，需要重新扫描"
    with pytest.raises(ValueError, match="conditional"):
        notice.summarize(r)
    r = rows()
    r[0]["ocr_page"] = 7
    with pytest.raises(ValueError, match="conditional"):
        notice.summarize(r)
    with pytest.raises(ValueError, match="drift"):
        notice.summarize(rows()[:-1])


def test_unpinned_source_sha_fails_closed(monkeypatch):
    monkeypatch.setattr(notice, "pinned_source_bytes", lambda fid, sha: False)
    with pytest.raises(ValueError, match="conditional"):
        notice.summarize(rows())
