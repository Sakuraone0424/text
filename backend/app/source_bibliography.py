"""Publisher/author metadata copied only from the user's locked OCR covers.

The titles/editions below are grounded in the opening pages of the supplied
four textbooks. A SHA-256 match and in-file cover tokens are BOTH required.
Do not infer a printed page from an OCR marker or invent a note-book author.
"""

import json
import re
from pathlib import Path

from app.source_policy import COLLECTION

BIBLIOGRAPHY = {
    "biochemistry_upper": {
        "authors": "昌增益、施一公、王新泉（主编）",
        "edition": "2025年12月第1版（上册）",
        "cover_tokens": ("昌增益", "施一公", "王新泉", "2025", "高等教育出版社"),
    },
    "biochemistry_lower": {
        "authors": "昌增益、施一公、王新泉（主编）",
        "edition": "2025年12月第1版（下册）",
        "cover_tokens": ("昌增益", "施一公", "王新泉", "2025", "高等教育出版社"),
    },
    "cell_biology": {
        "authors": "丁明孝、王喜忠、张传茂、陈建国（主编）",
        "edition": "2020年5月第5版",
        "cover_tokens": ("丁明孝", "王喜忠", "张传茂", "陈建国", "2020", "高等教育出版社"),
    },
    "biochemistry_notes": {
        # This is a cover organization, not a fabricated named author.
        "authors": "求臻生物考研（封面署名机构）",
        "edition": "2027级／27版（校订OCR资料）",
        "cover_tokens": ("求臻生物考研", "2027", "27版", "生化高分笔记"),
    },
}


def normalized(text):
    return re.sub(r"\s+", "", text)


def from_locked_cover(key, actual_sha256, header, *, expected_sha256=None):
    spec = BIBLIOGRAPHY.get(key)
    if spec is None:
        raise ValueError("no_verified_cover_metadata_for_source")
    item = next((v for v in COLLECTION["files"] if v["key"] == key), None)
    if not item or item["kind"] != "textbook":
        raise ValueError("source_is_not_a_textbook")
    if expected_sha256 is None:
        pinned = json.loads(
            Path(__file__).with_name("ocr_source_sha256_lock.json").read_text()
        )
        if pinned["collection_version"] != COLLECTION["version"]:
            raise ValueError("bibliography_source_version_mismatch")
        expected_sha256 = pinned["files"][key]["sha256"]
    if actual_sha256 != expected_sha256:
        raise ValueError("unmatched_locked_source_sha256")
    plain = normalized(header)
    if not all(normalized(token) in plain for token in spec["cover_tokens"]):
        raise ValueError("bibliography_cover_tokens_missing")
    return {
        "file_id": item["file_id"],
        "textbook": item["filename"],
        "authors": spec["authors"],
        "edition": spec["edition"],
        "verification": "source_cover_tokens_and_locked_sha256",
    }


def from_database(conn, unit):
    """Never reuse cached authors from a changed or synthetic source version."""
    if unit["source_kind"] != "textbook":
        raise ValueError("only_textbook_evidence_can_define_answers")
    row = conn.execute(
        "SELECT d.sha256,substring(v.source_content FROM 1 FOR 16000) AS cover "
        "FROM source_documents d JOIN source_versions v ON v.document_id=d.id "
        "WHERE d.file_id=%s AND d.sha256=%s ORDER BY d.id DESC LIMIT 1",
        (unit["file_id"], unit["source_sha256"]),
    ).fetchone()
    if not row:
        raise ValueError("missing_current_source_bytes")
    key = next(
        (item["key"] for item in COLLECTION["files"] if item["file_id"] == unit["file_id"]),
        None,
    )
    return from_locked_cover(
        key, row["sha256"], bytes(row["cover"]).decode("utf-8", errors="ignore")
    )
