"""Fail-closed source policy for the isolated, no-model QA workflow.

Only the owner's four corrected OCR textbook/note files (.txt) and two
genuine-exam transcriptions (.docx) may enter the canonical training source
corpus. Reviewing a matching original PDF/image READ ONLY for OCR verification
does NOT introduce a seventh source and is allowed. Supplemental originals
must not silently become answer evidence or replace a locked revision.
Only daily question-generation may separately authorize the DeepSeek API.
"""
from collections import Counter

from app.source_policy import COLLECTION, FILES, pinned_source_bytes

EXPECTED_KEYS = frozenset({
    "biochemistry_upper", "biochemistry_lower", "biochemistry_notes",
    "cell_biology", "biochemistry_exams", "cell_biology_exams",
})


def inspect():
    entries = COLLECTION.get("files")
    if not isinstance(entries, list) or len(entries) != 6 or len(FILES) != 6:
        raise PermissionError("qa_only_six_source_files_authorized")
    keys = [item.get("key") for item in entries]
    if set(keys) != EXPECTED_KEYS or len(set(keys)) != len(keys):
        raise PermissionError("qa_source_keys_modified")
    kinds = Counter(item.get("kind") for item in entries)
    if kinds != {"textbook": 4, "exam_transcription": 2}:
        raise PermissionError("qa_source_type_distribution_changed")
    for item in entries:
        filename = item.get("filename", "").lower()
        if not filename.endswith(".txt" if item["kind"] == "textbook" else ".docx"):
            raise PermissionError("qa_non_ocr_or_non_transcription_source")
        if item.get("source_mode") not in (None, "ocr_only"):
            raise PermissionError("qa_source_mode_not_ocr_only")
        # The actual SHA check occurs again at the independent DB evidence gate.
        if item["file_id"] not in FILES:
            raise PermissionError("qa_unrecognized_file_id")
    return {
        "source_count": 6,
        "ocr_textbooks_and_notes": 4,
        "genuine_exam_transcriptions": 2,
        "additional_pdf_sources_allowed": False,
        "original_pdf_visual_review_allowed": True,
        "original_pdf_never_training_source_without_explicit_reimport": True,
        "external_model_calls_allowed": False,
        "source_manifest_valid": True,
    }
