"""Bounded private originals, explicit multipart identities and page correspondence."""

import hashlib
import io
import json
from pathlib import Path

MIB = 1024 * 1024
MAX_OCR_BYTES = 50 * MIB
MAX_PART_BYTES = 100 * MIB
MAX_TOTAL_BYTES = 512 * MIB
MAX_PARTS = 16
MAX_PART_PAGES = 2000
MAX_TOTAL_PAGES = 4000


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_bounded(path, limit):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(
            f"File missing, not regular, or exceeds {limit // MIB} MiB limit"
        )
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise ValueError("File empty or exceeds bounded read limit")
    return raw


def pdf_pages(raw):
    from pypdf import PdfReader

    if not raw.startswith(b"%PDF-"):
        raise ValueError("Original part must be an actual PDF")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("Encrypted original PDF is not supported")
        count = len(reader.pages)
    except Exception as exc:
        raise ValueError("Original PDF cannot be parsed or is encrypted") from exc
    if not 1 <= count <= MAX_PART_PAGES:
        raise ValueError("Original PDF page count exceeds bounded limit")
    return count


def part_manifest(parts):
    return [
        {
            k: p[k]
            for k in (
                "part_number",
                "file_id",
                "sha256",
                "byte_size",
                "pdf_pages",
                "page_offset",
            )
        }
        for p in parts
    ]


def manifest_sha(parts):
    return sha(
        json.dumps(
            part_manifest(parts),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )


def load_parts(specifications):
    if (
        not isinstance(specifications, list)
        or not 1 <= len(specifications) <= MAX_PARTS
    ):
        raise ValueError("Original PDF parts require 1–16 explicit entries")
    parts, total, offset, ids = [], 0, 0, set()
    for number, spec in enumerate(specifications, 1):
        if not isinstance(spec, dict) or set(spec) - {
            "path",
            "file_id",
            "page_offset",
            "expected_pages",
            "sha256",
            "content",
        }:
            raise ValueError("Unknown original-part fields")
        if (
            not isinstance(spec.get("file_id"), str)
            or not spec["file_id"].strip()
            or spec["file_id"] in ids
        ):
            raise ValueError("Original part file IDs must be nonempty and unique")
        if type(spec.get("page_offset")) is not int or spec["page_offset"] != offset:
            raise ValueError(
                "Original part offsets must be contiguous, ordered and begin at zero"
            )
        if (
            type(spec.get("expected_pages")) is not int
            or not 1 <= spec["expected_pages"] <= MAX_PART_PAGES
        ):
            raise ValueError("Each original part requires a bounded expected_pages")
        # Size checked before reading; aggregate budget checked before allocating next file.
        remaining = min(MAX_PART_BYTES, MAX_TOTAL_BYTES - total)
        if remaining <= 0:
            raise ValueError("Original parts exceed aggregate size limit")
        if "content" in spec:
            raw = bytes(spec["content"])
            if not raw or len(raw) > remaining:
                raise ValueError("Stored original part exceeds size limit")
        else:
            if not spec.get("path") or Path(spec["path"]).suffix.lower() != ".pdf":
                raise ValueError("Original parts require local PDF paths")
            raw = read_bounded(spec["path"], remaining)
        actual_pages = pdf_pages(raw)
        checksum = sha(raw)
        if actual_pages != spec["expected_pages"]:
            raise ValueError(
                "Original part actual page count differs from expected_pages"
            )
        if spec.get("sha256") is not None and spec["sha256"] != checksum:
            raise ValueError("Original part checksum differs from handoff manifest")
        total += len(raw)
        offset += actual_pages
        if offset > MAX_TOTAL_PAGES:
            raise ValueError("Original parts exceed whole-book page limit")
        ids.add(spec["file_id"])
        parts.append(
            dict(
                part_number=number,
                file_id=spec["file_id"],
                sha256=checksum,
                byte_size=len(raw),
                pdf_pages=actual_pages,
                page_offset=spec["page_offset"],
                content=raw,
            )
        )
    return parts


def map_originals(pages, parts, original_count=None):
    seen = set()
    for page in pages:
        physical = page["pdf_page"]
        if physical in seen:
            raise ValueError("Multiple OCR pages map to the same physical PDF page")
        seen.add(physical)
        if original_count and physical > original_count:
            raise ValueError("OCR mapping points outside the actual original PDF")
        if parts:
            matches = [
                p
                for p in parts
                if p["page_offset"] < physical <= p["page_offset"] + p["pdf_pages"]
            ]
            if len(matches) != 1:
                raise ValueError(
                    "OCR physical page must resolve to exactly one original PDF part"
                )
            part = matches[0]
            page["original_part"] = part["part_number"]
            page["original_page"] = physical - part["page_offset"]
        else:
            page["original_part"] = None
            page["original_page"] = physical if original_count else None


def original_available_sql(alias="d"):
    # Presence only, as in the former single-file fast path. Full checksums are
    # independently required on verification and review/publication.
    return f"({alias}.original_content IS NOT NULL OR ({alias}.metadata->>'original_kind'='pdf_parts' AND (SELECT COUNT(*) FROM source_original_parts op WHERE op.document_id={alias}.id)=jsonb_array_length({alias}.metadata->'original_parts_manifest') AND EXISTS(SELECT 1 FROM source_original_parts op WHERE op.document_id={alias}.id)))"


def source_index_sha(rows, pages):
    unit_keys = (
        "chapter",
        "section",
        "text",
        "pdf_page",
        "printed_page",
        "ocr_page",
        "volume",
        "quality_flags",
        "ocr_global_page",
        "original_part",
        "original_page",
    )
    page_keys = (
        "ocr_global_page",
        "volume",
        "ocr_page",
        "pdf_page",
        "printed_page",
        "original_part",
        "original_page",
        "text_characters",
        "paragraph_count",
    )
    payload = {
        "units": [{k: r[k] for k in unit_keys} for r in rows],
        "pages": [{k: p[k] for k in page_keys} for p in pages],
    }
    return sha(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )
