"""Private OCR import and exact evidence checks; no Drive downloader or OCR job."""

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from psycopg.types.json import Jsonb

PAGE = re.compile(
    r"^\s*(?:\[PAGE\s*(\d+)\]|\[第\s*(\d+)\s*页\]|[-=]*\s*第\s*(\d+)\s*页\s*[-=]*|P(?:AGE)?\s*0*(\d+))\s*$",
    re.I,
)
HEADING = re.compile(r"^(第[一二三四五六七八九十百\d]+[章节]|\d+(?:\.\d+){0,3}[、.\s])")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


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
    offsets = metadata.get("volume_offsets", [0])
    if (
        not isinstance(offsets, list)
        or not offsets
        or offsets[0] != 0
        or any(type(n) is not int or n < 0 for n in offsets)
        or offsets != sorted(set(offsets))
    ):
        raise ValueError(
            "Volume offsets must be unique increasing nonnegative integers beginning at zero"
        )
    lengths = metadata.get("volume_pages")
    volume = 0
    previous = 0
    page = None
    chapter = metadata.get("chapter", "unclassified")
    section = ""
    rows = []
    buffer = []
    observed = set()

    def flush():
        if buffer:
            paragraph = "\n".join(buffer).strip()
            if paragraph:
                flags = []
                if "\ufffd" in paragraph or "\x00" in paragraph:
                    flags.append("corrupt_ocr")
                if re.search(
                    r"[αβγΔμ]|[₀-₉⁰-⁹]|[53][′’']|ATP|ADP|AMP|Vmax|Km|→|⇌", paragraph
                ):
                    flags.append("scientific_notation_requires_source_review")
                global_ocr_page = offsets[volume] + page if page is not None else None
                pdf_page = metadata.get("pdf_pages", {}).get(
                    str(global_ocr_page), global_ocr_page
                )
                if pdf_page is not None and (type(pdf_page) is not int or pdf_page < 1):
                    raise ValueError(
                        "Physical PDF page mapping must use positive integers"
                    )
                rows.append(
                    dict(
                        chapter=chapter,
                        section=section,
                        text=paragraph,
                        pdf_page=pdf_page,
                        printed_page=metadata.get("printed_pages", {}).get(
                            str(pdf_page)
                        ),
                        ocr_page=page,
                        volume=volume + 1 if page else None,
                        quality_flags=flags,
                    )
                )
            buffer.clear()

    for line in text.splitlines():
        match = PAGE.fullmatch(line)
        if match:
            flush()
            current = int(next(v for v in match.groups() if v is not None))
            if current < 1:
                raise ValueError("Page numbers start at one")
            if current <= previous:
                volume += 1
                if volume >= len(offsets):
                    raise ValueError("Page reset requires explicit volume offsets")
            if lengths and (volume >= len(lengths) or current > lengths[volume]):
                raise ValueError("OCR page outside declared volume")
            page = current
            previous = current
            observed.add(offsets[volume] + page)
            continue
        if HEADING.match(line.strip()):
            flush()
            section = line.strip()
            if re.match(r"^第.+章", line.strip()):
                chapter = section
        if not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    expected = metadata.get("expected_pages")
    if expected is not None and observed != set(range(1, expected + 1)):
        raise ValueError("Page coverage does not match declared whole-book pages")
    if not rows:
        raise ValueError(
            "No text extracted; supply existing OCR, do not silently invent content"
        )
    return rows


def import_source(conn, path, metadata, original_path=None):
    for field in ("file_id", "title", "edition", "subject"):
        if not metadata.get(field):
            raise ValueError(f"Missing source metadata: {field}")
    path = Path(path)
    raw = path.read_bytes()
    if original_path:
        original = Path(original_path).read_bytes()
        if len(original) > 100 * 1024 * 1024:
            raise ValueError("Original source exceeds 100 MiB")
    else:
        original = raw if path.suffix.lower() in (".pdf", ".docx") else None
    metadata = dict(metadata)
    original_suffix = (
        Path(original_path).suffix.lower() if original_path else path.suffix.lower()
    )
    if original and original_suffix == ".pdf":
        from pypdf import PdfReader

        metadata["original_pdf_pages"] = len(PdfReader(io.BytesIO(original)).pages)
    rows = parse(extract(raw, path.suffix.lower()), metadata)
    if metadata.get("original_pdf_pages") and any(
        r["pdf_page"] and r["pdf_page"] > metadata["original_pdf_pages"] for r in rows
    ):
        raise ValueError("OCR mapping points outside the actual original PDF")
    conn.execute(
        "SELECT pg_advisory_xact_lock(%s)",
        (int(sha((metadata["file_id"] + sha(raw)).encode())[:15], 16),),
    )
    old = conn.execute(
        "SELECT * FROM source_documents WHERE file_id=%s AND sha256=%s FOR UPDATE",
        (metadata["file_id"], sha(raw)),
    ).fetchone()
    if old:
        if not original and old["original_content"]:
            original = bytes(old["original_content"])
            if "original_pdf_pages" in old["metadata"]:
                metadata["original_pdf_pages"] = old["metadata"]["original_pdf_pages"]
        if (
            metadata == old["metadata"]
            and (sha(original) if original else None) == old["original_sha256"]
        ):
            return old["id"]
        if conn.execute(
            "SELECT 1 FROM source_units WHERE document_id=%s AND verified", (old["id"],)
        ).fetchone():
            raise ValueError(
                "Verified sources are immutable; import a corrected source revision"
            )
        if conn.execute(
            "SELECT COUNT(*) AS n FROM source_units WHERE document_id=%s", (old["id"],)
        ).fetchone()["n"] != len(rows):
            raise ValueError(
                "Reindex would change unit identities; import a corrected source revision"
            )
        conn.execute(
            "UPDATE source_documents SET title=%s,edition=%s,subject=%s,metadata=%s,original_content=%s,original_sha256=%s WHERE id=%s",
            (
                metadata["title"],
                metadata["edition"],
                metadata["subject"],
                Jsonb(metadata),
                original,
                sha(original) if original else None,
                old["id"],
            ),
        )
        for ordinal, row in enumerate(rows, 1):
            conn.execute(
                "UPDATE source_units SET chapter=%s,section=%s,text=%s,pdf_page=%s,printed_page=%s,ocr_page=%s,volume=%s,quality_flags=%s WHERE document_id=%s AND ordinal=%s",
                (
                    row["chapter"],
                    row["section"],
                    row["text"],
                    row["pdf_page"],
                    row["printed_page"],
                    row["ocr_page"],
                    row["volume"],
                    Jsonb(row["quality_flags"]),
                    old["id"],
                    ordinal,
                ),
            )
        return old["id"]
    doc = conn.execute(
        "INSERT INTO source_documents(file_id,title,edition,subject,sha256,original_sha256,original_content,metadata) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            metadata["file_id"],
            metadata["title"],
            metadata["edition"],
            metadata["subject"],
            sha(raw),
            sha(original) if original else None,
            original,
            Jsonb(metadata),
        ),
    ).fetchone()["id"]
    for ordinal, row in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO source_units(document_id,ordinal,chapter,section,text,pdf_page,printed_page,ocr_page,volume,quality_flags) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                doc,
                ordinal,
                row["chapter"],
                row["section"],
                row["text"],
                row["pdf_page"],
                row["printed_page"],
                row["ocr_page"],
                row["volume"],
                Jsonb(row["quality_flags"]),
            ),
        )
    return doc


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
    if (
        not row["original_content"]
        or sha(bytes(row["original_content"])) != row["original_sha256"]
    ):
        raise ValueError("Original source is missing or checksum differs")
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
            else "d.original_sha256,(d.original_content IS NOT NULL) AS original_present"
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
        if verify_original:
            if (
                not row["original_content"]
                or sha(bytes(row["original_content"])) != row["original_sha256"]
            ):
                issues.append("missing_or_changed_original")
        elif not row["original_present"]:
            issues.append("missing_or_changed_original")
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
        "SELECT u.id,u.document_id,u.chapter,u.section,u.pdf_page,u.printed_page,u.ocr_page,u.volume,u.verified,u.quality_flags,u.text "
        "FROM source_units u WHERE to_tsvector('simple',text) @@ plainto_tsquery('simple',%s) OR strpos(text,%s)>0 ORDER BY u.id LIMIT %s",
        (query, query, min(limit, 50)),
    ).fetchall()


def main():
    import argparse
    from app.db import get_db

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--original")
    args = parser.parse_args()
    with get_db() as conn:
        document_id = import_source(
            conn, args.path, json.loads(Path(args.metadata).read_text()), args.original
        )
    print(
        f"Imported private source {document_id}; units remain unverified until source review"
    )


if __name__ == "__main__":
    main()


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
