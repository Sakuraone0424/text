"""Private OCR import and exact evidence checks; no Drive downloader or OCR job."""

import io
import zipfile
from pathlib import Path

from psycopg.types.json import Jsonb

from app.source_paging import PAGE as PAGE, HEADING as HEADING, parse_pages
from app.source_files import (
    sha,
    read_bounded,
    pdf_pages,
    load_parts,
    part_manifest,
    manifest_sha,
    map_originals,
    original_available_sql,
    MAX_OCR_BYTES,
    MAX_PART_BYTES,
    source_index_sha,
)


def extract(raw, suffix):
    if len(raw) > 50 * 1024 * 1024:
        raise ValueError("Source exceeds 50 MiB")
    if suffix == ".txt":
        return raw.decode("utf-8-sig")
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        if len(reader.pages) > 2000:
            raise ValueError("Too many PDF pages")
        return "\n".join(
            f"[PAGE {i}]\n{p.extract_text() or ''}"
            for i, p in enumerate(reader.pages, 1)
        )
    if suffix == ".docx":
        from docx import Document

        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist()) > 100 * 1024 * 1024:
                raise ValueError("DOCX expanded size exceeds limit")
        from docx.table import Table

        def parts(container, depth=0):
            if depth > 8:
                raise ValueError("DOCX table nesting exceeds limit")
            for part in container.iter_inner_content():
                if isinstance(part, Table):
                    for row in part.rows:
                        for cell in row.cells:
                            yield from parts(cell, depth + 1)
                else:
                    yield part.text

        return "\n".join(parts(Document(io.BytesIO(raw))))
    raise ValueError("Supported sources are UTF-8 TXT, text PDF and DOCX")


def parse(text, metadata):
    return parse_pages(text, metadata)[0]


def prepare_source(
    path, metadata, original_path=None, original_parts=None, *, raw=None, existing=None
):
    for field in ("file_id", "title", "edition", "subject"):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            raise ValueError(f"Missing source metadata: {field}")
    if metadata["subject"] not in ("biochemistry", "cell_biology"):
        raise ValueError("Unknown source subject")
    if original_path is not None and original_parts is not None:
        raise ValueError("Choose a single original or PDF parts, never both")
    path = Path(path)
    raw = read_bounded(path, MAX_OCR_BYTES) if raw is None else raw
    metadata = dict(metadata)
    for key in (
        "original_pdf_pages",
        "original_kind",
        "original_parts_manifest",
        "source_index_sha256",
    ):
        metadata.pop(key, None)  # Derived from bytes, never trusted from metadata.
    original, parts = None, []
    suffix = path.suffix.lower()
    original_suffix = Path(original_path).suffix.lower() if original_path else suffix
    if original_parts is not None:
        parts = load_parts(original_parts)
    elif original_path:
        original = read_bounded(original_path, MAX_PART_BYTES)
    elif existing:
        if existing.get("parts"):
            parts = load_parts(existing["parts"])
        elif existing.get("original"):
            original = existing["original"]
            original_suffix = existing.get("suffix", "")
    elif suffix in (".pdf", ".docx"):
        original = raw
    if parts:
        metadata["original_kind"] = "pdf_parts"
        metadata["original_parts_manifest"] = part_manifest(parts)
        metadata["original_pdf_pages"] = sum(p["pdf_pages"] for p in parts)
    elif original and original_suffix == ".pdf":
        metadata["original_pdf_pages"] = pdf_pages(original)
    rows, pages = parse_pages(extract(raw, suffix), metadata)
    if parts and not pages:
        raise ValueError("PDF parts require explicit OCR page markers")
    map_originals(pages, parts, metadata.get("original_pdf_pages"))
    mapping = {p["ocr_global_page"]: p for p in pages}
    for row in rows:
        page = mapping.get(row["ocr_global_page"], {})
        row["original_part"] = page.get("original_part")
        row["original_page"] = page.get("original_page")
    metadata["source_index_sha256"] = source_index_sha(rows, pages)
    return dict(
        metadata=metadata,
        rows=rows,
        pages=pages,
        parts=parts,
        original=original,
        sha256=sha(raw),
        original_sha256=manifest_sha(parts)
        if parts
        else sha(original)
        if original
        else None,
    )


def import_source(conn, path, metadata, original_path=None, original_parts=None):
    raw = read_bounded(path, MAX_OCR_BYTES)
    if not isinstance(metadata.get("file_id"), str):
        raise ValueError("Missing source metadata: file_id")
    conn.execute(
        "SELECT pg_advisory_xact_lock(%s)",
        (int(sha((metadata["file_id"] + sha(raw)).encode())[:15], 16),),
    )
    old = conn.execute(
        "SELECT * FROM source_documents WHERE file_id=%s AND sha256=%s FOR UPDATE",
        (metadata["file_id"], sha(raw)),
    ).fetchone()
    stored = None
    if old and original_path is None and original_parts is None:
        stored_parts = conn.execute(
            "SELECT * FROM source_original_parts WHERE document_id=%s ORDER BY part_number",
            (old["id"],),
        ).fetchall()
        stored = dict(
            original=bytes(old["original_content"])
            if old["original_content"]
            else None,
            suffix=".pdf" if old["metadata"].get("original_pdf_pages") else "",
            parts=[
                dict(
                    file_id=p["file_id"],
                    content=bytes(p["content"]),
                    page_offset=p["page_offset"],
                    expected_pages=p["pdf_pages"],
                    sha256=p["sha256"],
                )
                for p in stored_parts
            ],
        )
    prepared = prepare_source(
        path, metadata, original_path, original_parts, raw=raw, existing=stored
    )
    metadata, rows, pages = prepared["metadata"], prepared["rows"], prepared["pages"]
    if old:
        if (
            metadata == old["metadata"]
            and prepared["original_sha256"] == old["original_sha256"]
        ):
            return old["id"]
        if conn.execute(
            "SELECT 1 FROM source_units WHERE document_id=%s AND verified", (old["id"],)
        ).fetchone():
            raise ValueError(
                "Verified sources are immutable; import a corrected source revision"
            )
        existing_parts = conn.execute(
            "SELECT 1 FROM source_original_parts WHERE document_id=%s", (old["id"],)
        ).fetchone()
        if existing_parts and prepared["original_sha256"] != old["original_sha256"]:
            raise ValueError(
                "Original PDF parts are immutable; import a corrected source revision"
            )
        if conn.execute(
            "SELECT COUNT(*) AS n FROM source_units WHERE document_id=%s", (old["id"],)
        ).fetchone()["n"] != len(rows):
            raise ValueError(
                "Reindex would change unit identities; import a corrected source revision"
            )
        doc = old["id"]
        conn.execute(
            "UPDATE source_documents SET title=%s,edition=%s,subject=%s,metadata=%s,original_content=%s,original_sha256=%s WHERE id=%s",
            (
                metadata["title"],
                metadata["edition"],
                metadata["subject"],
                Jsonb(metadata),
                prepared["original"],
                prepared["original_sha256"],
                doc,
            ),
        )
    else:
        doc = conn.execute(
            "INSERT INTO source_documents(file_id,title,edition,subject,sha256,original_sha256,original_content,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                metadata["file_id"],
                metadata["title"],
                metadata["edition"],
                metadata["subject"],
                sha(raw),
                prepared["original_sha256"],
                prepared["original"],
                Jsonb(metadata),
            ),
        ).fetchone()["id"]
    for part in prepared["parts"]:
        conn.execute(
            "INSERT INTO source_original_parts(document_id,part_number,file_id,sha256,content,byte_size,pdf_pages,page_offset) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(document_id,part_number) DO NOTHING",
            (
                doc,
                part["part_number"],
                part["file_id"],
                part["sha256"],
                part["content"],
                part["byte_size"],
                part["pdf_pages"],
                part["page_offset"],
            ),
        )
    columns = (
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
    for ordinal, row in enumerate(rows, 1):
        values = tuple(
            Jsonb(row[k]) if k == "quality_flags" else row[k] for k in columns
        )
        if old:
            conn.execute(
                "UPDATE source_units SET chapter=%s,section=%s,text=%s,pdf_page=%s,printed_page=%s,ocr_page=%s,volume=%s,quality_flags=%s,ocr_global_page=%s,original_part=%s,original_page=%s WHERE document_id=%s AND ordinal=%s",
                values + (doc, ordinal),
            )
        else:
            conn.execute(
                "INSERT INTO source_units(chapter,section,text,pdf_page,printed_page,ocr_page,volume,quality_flags,ocr_global_page,original_part,original_page,document_id,ordinal) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                values + (doc, ordinal),
            )
    if old:
        conn.execute("DELETE FROM source_pages WHERE document_id=%s", (doc,))
    for page in pages:
        conn.execute(
            "INSERT INTO source_pages(document_id,ocr_global_page,volume,ocr_page,pdf_page,printed_page,original_part,original_page,text_characters,paragraph_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (doc,)
            + (
                tuple(
                    page[k]
                    for k in (
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
                )
            ),
        )
    return doc


def original_issues(conn, row, *, verify_hash=True):
    if row["metadata"].get("original_kind") != "pdf_parts":
        if verify_hash:
            return (
                []
                if row.get("original_content")
                and sha(bytes(row["original_content"])) == row["original_sha256"]
                else ["missing_or_changed_original"]
            )
        return [] if row.get("original_present") else ["missing_or_changed_original"]
    columns = (
        "*"
        if verify_hash
        else "part_number,file_id,sha256,byte_size,pdf_pages,page_offset"
    )
    parts = conn.execute(
        f"SELECT {columns} FROM source_original_parts WHERE document_id=%s ORDER BY part_number",
        (row["document_id"],),
    ).fetchall()
    if (
        not parts
        or part_manifest(parts) != row["metadata"].get("original_parts_manifest")
        or manifest_sha(parts) != row["original_sha256"]
    ):
        return ["missing_or_changed_original"]
    if verify_hash and any(
        sha(bytes(p["content"])) != p["sha256"] or len(p["content"]) != p["byte_size"]
        for p in parts
    ):
        return ["missing_or_changed_original"]
    if row["pdf_page"] is None:
        return ["textbook_page_mismatch"]
    matching = [
        p
        for p in parts
        if p["page_offset"] < row["pdf_page"] <= p["page_offset"] + p["pdf_pages"]
    ]
    if (
        len(matching) != 1
        or row["original_part"] != matching[0]["part_number"]
        or row["original_page"] != row["pdf_page"] - matching[0]["page_offset"]
    ):
        return ["textbook_page_mismatch"]
    return []


def verify_unit(conn, unit_id, reviewer, note):
    if not reviewer.strip() or len(note.strip()) < 20:
        raise ValueError("Textbook verification requires reviewer and substantive note")
    conn.execute(
        "SELECT id FROM source_documents WHERE id=(SELECT document_id FROM source_units WHERE id=%s) FOR UPDATE",
        (unit_id,),
    )
    row = conn.execute(
        "SELECT u.*,d.original_content,d.original_sha256,d.metadata FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=%s",
        (unit_id,),
    ).fetchone()
    if not row:
        raise LookupError("Source unit not found")
    if row["metadata"].get("material_status") == "synthetic_test":
        raise ValueError("Synthetic test material cannot be verified as a real source")
    if original_issues(conn, row):
        raise ValueError(
            "Original source is missing, checksum differs, or part mapping is invalid"
        )
    if "corrupt_ocr" in row["quality_flags"]:
        raise ValueError("Corrupt OCR cannot be verified; import a corrected revision")
    if row["pdf_page"] is not None and (
        not row["metadata"].get("original_pdf_pages")
        or row["pdf_page"] > row["metadata"]["original_pdf_pages"]
    ):
        raise ValueError("Physical page cannot be verified against the original PDF")
    conn.execute(
        "UPDATE source_units SET verified=true,reviewer=%s,verification_note=%s WHERE id=%s",
        (reviewer, note, unit_id),
    )


def evidence_issues(conn, content, *, verify_original=True):
    issues = []
    for e in content["evidence"]:
        original_columns = (
            "d.original_content,d.original_sha256"
            if verify_original
            else "d.original_sha256,"
            + original_available_sql()
            + " AS original_present"
        )
        row = conn.execute(
            "SELECT u.*,d.title,d.edition,d.subject,d.metadata,"
            + original_columns
            + " FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=%s",
            (e.get("source_unit_id"),),
        ).fetchone()
        if not row or not row["verified"]:
            issues.append("unverified_or_missing_textbook_unit")
            continue
        bibliography = row["metadata"]
        if bibliography.get("material_status") == "synthetic_test":
            issues.append("synthetic_material_not_publishable")
            continue
        if (
            not bibliography.get("authors")
            or e["authors"] != bibliography["authors"]
            or e.get("isbn") != bibliography.get("isbn")
        ):
            issues.append("textbook_bibliography_mismatch")
        if (
            e["textbook"] != row["title"]
            or e["edition"] != row["edition"]
            or e["chapter"] != row["chapter"]
            or content["subject"] != row["subject"]
            or e["excerpt"] not in row["text"]
        ):
            issues.append("textbook_evidence_mismatch")
        expected_locator = (
            f"PDF:{row['pdf_page']}"
            if row["pdf_page"] is not None
            else f"UNIT:{row['ordinal']}"
        )
        if e["locator"] != expected_locator:
            issues.append("textbook_page_mismatch")
        issues.extend(original_issues(conn, row, verify_hash=verify_original))
    if content.get("knowledge_tags"):
        links = set()
        for evidence in content["evidence"]:
            links.update(
                r["tag"]
                for r in conn.execute(
                    "SELECT tag FROM knowledge_links WHERE unit_id=%s",
                    (evidence.get("source_unit_id"),),
                ).fetchall()
            )
        if not set(content["knowledge_tags"]).issubset(links):
            issues.append("missing_knowledge_tag_links")
    return sorted(set(issues))


def search(conn, query, limit=20):
    if not query.strip() or len(query) > 500:
        raise ValueError("Search query must be 1-500 characters")
    # Substring fallback covers Chinese OCR without assuming an English tokenizer.
    return conn.execute(
        "SELECT u.id,u.document_id,u.chapter,u.section,u.pdf_page,u.printed_page,u.ocr_page,u.volume,u.ocr_global_page,u.original_part,u.original_page,u.verified,u.quality_flags,u.text "
        "FROM source_units u WHERE to_tsvector('simple',text) @@ plainto_tsquery('simple',%s) OR strpos(text,%s)>0 ORDER BY u.id LIMIT %s",
        (query, query, min(limit, 50)),
    ).fetchall()


def main():
    # Retain the old executable name without its implicit DATABASE_URL write path.
    import sys
    from app.source_intake import main as intake_main

    return intake_main(["import", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())


def annotate(conn, unit_id, tags):
    if (
        not isinstance(tags, list)
        or len(tags) > 100
        or any(not isinstance(t, str) or not 0 < len(t.strip()) <= 300 for t in tags)
    ):
        raise ValueError(
            "Knowledge tags must be explicit paths of at most 300 characters"
        )
    if not conn.execute(
        "SELECT 1 FROM source_units WHERE id=%s", (unit_id,)
    ).fetchone():
        raise LookupError("Source unit not found")
    for tag in tags:
        conn.execute(
            "INSERT INTO knowledge_links(unit_id,tag) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (unit_id, tag.strip()),
        )
