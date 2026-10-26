"""Immutable six-file OCR versions, separate human reviews, and exact OCR citations."""

import json
import re
from pathlib import Path
from psycopg.types.json import Jsonb
from app.source_files import sha, read_bounded, MAX_OCR_BYTES, source_index_sha
from app.source_policy import COLLECTION, entry, active_document


def unit_sha(row):
    keys = (
        "document_id",
        "ordinal",
        "chapter",
        "section",
        "text",
        "ocr_global_page",
        "quality_flags",
    )
    return sha(
        json.dumps(
            {k: row.get(k) for k in keys},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    )


def flags(text, initial=None):
    result = set(initial or [])
    for pattern, flag in [
        (r"\ufffd|\x00", "corrupt_ocr"),
        (r"见图|如图|图像|照片|原图|图示|封面图|\[图片\]|\[图\]", "image_dependent"),
        (r"结构式|化学结构", "chemical_structure"),
        (r"见表|如下表|\t|\|.+\|", "complex_table"),
        (r"\?\?\?|□|待辨|无法辨认|OCR不确定", "ocr_uncertain"),
        (r"缺页|缺题|转录缺失|扫描缺失|题目缺失", "transcription_gap_reported"),
        (
            r"[αβγΔμ₀-₉⁰-⁹→⇌]|ATP|ADP|Vmax|Km",
            "scientific_notation_requires_source_review",
        ),
    ]:
        if re.search(pattern, text):
            result.add(flag)
    return sorted(result)


def prepare(path, metadata, raw=None):
    from app.sources import extract
    from app.source_paging import parse_pages

    path = Path(path)
    for field in ("title", "edition"):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            raise ValueError(
                "Explicit title and edition labels are required; unknown must remain pending"
            )
    item = entry(metadata, path)
    raw = read_bounded(path, MAX_OCR_BYTES) if raw is None else raw
    if metadata.get("expected_sha256") is not None and metadata[
        "expected_sha256"
    ] != sha(raw):
        raise ValueError(
            "Actual source SHA-256 differs from the private handoff manifest"
        )
    metadata = dict(metadata)
    if any(
        metadata.get(k)
        for k in (
            "pdf_pages",
            "printed_pages",
            "original_pdf_pages",
            "original_parts_manifest",
        )
    ):
        raise ValueError(
            "OCR-only sources cannot assert PDF or printed-page correspondence"
        )
    for k in (
        "original_kind",
        "physical_mapping",
        "original_pdf_pages",
        "original_parts_manifest",
    ):
        metadata.pop(k, None)
    metadata.update(
        source_mode="ocr_only",
        source_kind=item["kind"],
        indexer_version="ocr-index/1.1",
        page_label=item.get("page_label"),
        volume_offsets=[0],
    )
    if item["kind"] == "textbook":
        rows, pages = parse_pages(extract(raw, ".txt"), metadata)
    else:
        rows, pages = [], []
        chapter = "unclassified"
        section = ""
        from app.transcript_analysis import year_heading

        blocks = []
        for (
            text,
            position,
            in_table,
            has_image,
            untranscribed,
            numbering,
        ) in docx_blocks(raw):
            lines = text.splitlines()
            for line_number, line in enumerate(lines, 1):
                if line.strip():
                    blocks.append(
                        (
                            line.strip(),
                            position
                            + (f"/line{line_number}" if len(lines) > 1 else ""),
                            in_table,
                            has_image,
                            untranscribed,
                            numbering if line_number == 1 else None,
                        )
                    )
        for text, position, in_table, has_image, untranscribed, numbering in blocks:
            section = position
            if numbering:
                section += f"|num:{numbering['id']}:{numbering['level']}:{numbering['format']}:{numbering['value']}"
            if year_heading(text, item) is not None:
                chapter = text
            rows.append(
                dict(
                    chapter=chapter,
                    section=section,
                    text=text,
                    pdf_page=None,
                    printed_page=None,
                    ocr_page=None,
                    volume=None,
                    ocr_global_page=None,
                    quality_flags=(["docx_table_requires_review"] if in_table else [])
                    + (["image_dependent"] if has_image else [])
                    + (["untranscribed_image"] if untranscribed else []),
                )
            )
        if not rows:
            raise ValueError("No DOCX transcription text extracted")
    for page in pages:
        page.update(original_part=None, original_page=None)
    for row in rows:
        row.update(
            original_part=None, original_page=None, pdf_page=None, printed_page=None
        )
        row["quality_flags"] = flags(row["text"], row["quality_flags"])
    metadata["source_index_sha256"] = source_index_sha(rows, pages)
    return dict(
        metadata=metadata,
        rows=rows,
        pages=pages,
        parts=[],
        original=None,
        sha256=sha(raw),
        original_sha256=None,
        raw=raw,
    )


def import_source(conn, path, metadata):
    prepared = prepare(path, metadata)
    metadata = prepared["metadata"]
    checksum = prepared["sha256"]
    conn.execute(
        "SELECT pg_advisory_xact_lock(%s)",
        (int(sha(metadata["file_id"].encode())[:15], 16),),
    )
    old = conn.execute(
        "SELECT * FROM source_documents WHERE file_id=%s AND sha256=%s",
        (metadata["file_id"], checksum),
    ).fetchone()
    if old:
        if not active_document(conn, old["id"]):
            raise ValueError(
                "A superseded file hash cannot silently reactivate old reviews"
            )
        if (
            old["metadata"] != metadata
            or not conn.execute(
                "SELECT 1 FROM source_versions WHERE document_id=%s", (old["id"],)
            ).fetchone()
        ):
            raise ValueError(
                "Existing byte identity has different metadata; do not silently overwrite evidence"
            )
        return old["id"]
    previous = conn.execute(
        "SELECT id FROM source_documents WHERE file_id=%s ORDER BY id DESC LIMIT 1",
        (metadata["file_id"],),
    ).fetchone()
    doc = conn.execute(
        "INSERT INTO source_documents(file_id,title,edition,subject,sha256,metadata) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            metadata["file_id"],
            metadata["title"],
            metadata["edition"],
            metadata["subject"],
            checksum,
            Jsonb(metadata),
        ),
    ).fetchone()["id"]
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
    units = []
    for ordinal, row in enumerate(prepared["rows"], 1):
        values = tuple(
            Jsonb(row[k]) if k == "quality_flags" else row[k] for k in columns
        )
        uid = conn.execute(
            "INSERT INTO source_units(chapter,section,text,pdf_page,printed_page,ocr_page,volume,quality_flags,ocr_global_page,original_part,original_page,document_id,ordinal) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            values + (doc, ordinal),
        ).fetchone()["id"]
        units.append({**row, "id": uid})
    for page in prepared["pages"]:
        conn.execute(
            "INSERT INTO source_pages(document_id,ocr_global_page,volume,ocr_page,pdf_page,printed_page,original_part,original_page,text_characters,paragraph_count,marker_text,marker_label) VALUES (%s,%s,%s,%s,NULL,NULL,NULL,NULL,%s,%s,%s,%s)",
            (
                doc,
                page["ocr_global_page"],
                page["volume"],
                page["ocr_page"],
                page["text_characters"],
                page["paragraph_count"],
                page["marker_text"],
                page["marker_label"],
            ),
        )
    item = entry(metadata)
    conn.execute(
        "INSERT INTO source_versions(document_id,previous_document_id,collection_version,material_key,folder_id,source_sha256,source_format,source_content) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            doc,
            previous["id"] if previous else None,
            COLLECTION["version"],
            item["key"],
            COLLECTION["folder_id"],
            checksum,
            "txt" if item["kind"] == "textbook" else "docx",
            prepared["raw"],
        ),
    )
    if item["kind"] == "exam_transcription":
        index_transcript(conn, doc, units, item)
    return doc


def latest_review(conn, row, kind):
    event = conn.execute(
        "SELECT * FROM source_review_events WHERE unit_id=%s AND review_kind=%s ORDER BY id DESC LIMIT 1",
        (row["id"], kind),
    ).fetchone()
    return bool(
        event
        and event["status"] == "approved"
        and event["unit_sha256"] == unit_sha(row)
    )


def review_unit(conn, unit_id, kind, status, reviewer, note, details=None):
    details = details or {}
    if (
        kind not in ("text", "science", "uncertainty", "exam_style")
        or status not in ("approved", "rejected")
        or not reviewer.strip()
        or len(note.strip()) < 20
    ):
        raise ValueError(
            "Explicit review kind, status, reviewer and substantive note are required"
        )
    row = conn.execute(
        "SELECT u.*,d.metadata,d.sha256 FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE u.id=%s FOR UPDATE OF d",
        (unit_id,),
    ).fetchone()
    if not row or row["metadata"].get("source_mode") != "ocr_only":
        raise ValueError("OCR-only review requires an OCR source unit")
    if not active_document(conn, row["document_id"]):
        raise ValueError("Source is outside the current collection or superseded")
    if row["metadata"].get("material_status") == "synthetic_test":
        raise ValueError("Synthetic material cannot be verified as real")
    if status == "approved":
        if "corrupt_ocr" in row["quality_flags"]:
            raise ValueError("Corrupt OCR requires a new corrected file version")
        if "untranscribed_image" in row["quality_flags"]:
            raise ValueError(
                "Untranscribed image needs corrected source text; never guess its content"
            )
        if integrity_issues(conn, row):
            raise ValueError("OCR bytes or indexed identity differ")
        if kind != "text" and not latest_review(conn, row, "text"):
            raise ValueError("Text review must precede other approvals")
        if kind == "uncertainty" and (
            set(details.get("resolved_flags", [])) != set(row["quality_flags"])
            or details.get("resolution") != "text_sufficient"
            or len(details.get("explanation", "")) < 20
        ):
            raise ValueError(
                "All uncertainty flags need a substantive text-sufficient resolution; never guess missing images"
            )
        if (
            kind in ("science", "exam_style")
            and row["quality_flags"]
            and not latest_review(conn, row, "uncertainty")
        ):
            raise ValueError("Unresolved OCR, figure, structure or table uncertainty")
        if (
            kind == "exam_style"
            and row["metadata"].get("source_kind") != "exam_transcription"
        ):
            raise ValueError("Style review requires an exam transcript")
    conn.execute(
        "INSERT INTO source_review_events(unit_id,review_kind,status,unit_sha256,reviewer,note,details) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (unit_id, kind, status, unit_sha(row), reviewer, note, Jsonb(details)),
    )
    if kind == "text":
        conn.execute(
            "UPDATE source_units SET verified=%s,reviewer=%s,verification_note=%s WHERE id=%s",
            (status == "approved", reviewer, note, unit_id),
        )
    return {
        "unit_id": unit_id,
        "review_kind": kind,
        "status": status,
        "unit_sha256": unit_sha(row),
    }


def integrity_issues(conn, row, *, verify_hash=True):
    columns = "source_sha256,source_content" if verify_hash else "source_sha256"
    version = conn.execute(
        f"SELECT {columns} FROM source_versions WHERE document_id=%s",
        (row["document_id"],),
    ).fetchone()
    if not version or version["source_sha256"] != row.get(
        "sha256", row.get("document_sha256")
    ):
        return ["missing_or_changed_ocr_file"]
    if (
        verify_hash
        and sha(bytes(version["source_content"])) != version["source_sha256"]
    ):
        return ["missing_or_changed_ocr_file"]
    if row.get("pdf_page") is not None or row.get("printed_page") is not None:
        return ["ocr_source_cannot_assert_pdf_or_printed_page"]
    return []


def evidence_issues(conn, row, evidence, verify_hash=True):
    issues = integrity_issues(conn, row, verify_hash=verify_hash)
    if (
        row["metadata"].get("source_kind") == "textbook"
        and row["chapter"] == "unclassified"
    ):
        issues.append("ocr_chapter_not_indexed")
    if not latest_review(conn, row, "text"):
        issues.append("ocr_text_not_human_reviewed")
    if not latest_review(conn, row, "science"):
        issues.append("ocr_science_not_reviewed")
    if row["quality_flags"] and not latest_review(conn, row, "uncertainty"):
        issues.append("unresolved_ocr_uncertainty")
    locator = (
        f"OCR:{row['ocr_global_page']}:UNIT:{row['id']}"
        if row["ocr_global_page"]
        else f"DOCX:UNIT:{row['id']}"
    )
    if (
        evidence.get("locator") != locator
        or evidence.get("file_id") != row["file_id"]
        or evidence.get("source_sha256") != row["sha256"]
        or evidence.get("paragraph_id") != row["id"]
        or evidence.get("ocr_page") != row["ocr_global_page"]
    ):
        issues.append("ocr_evidence_identity_mismatch")
    return issues


def index_transcript(conn, doc, units, item):
    from app.transcript_analysis import candidates

    for candidate in candidates(units, item):
        conn.execute(
            "INSERT INTO exam_transcript_candidates(document_id,unit_id,year,question_number,question_type,transcription,concerns) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                doc,
                candidate["unit_id"],
                candidate["year"],
                candidate["question_number"],
                candidate["question_type"],
                candidate["transcription"],
                Jsonb(candidate["concerns"]),
            ),
        )


def docx_blocks(raw):
    """Preserve paragraph/table positions; table text never silently loses its risk flag."""
    import io, zipfile
    from docx import Document
    from docx.table import Table

    if len(raw) > MAX_OCR_BYTES:
        raise ValueError("DOCX exceeds source size limit")
    from lxml.etree import XMLSyntaxError

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("DOCX expanded size exceeds limit")
        document = Document(io.BytesIO(raw))
    except (
        zipfile.BadZipFile,
        KeyError,
        ValueError,
        XMLSyntaxError,
        RuntimeError,
    ) as exc:
        raise ValueError(
            "Invalid DOCX container, XML, encryption or expansion limit"
        ) from exc

    from docx.oxml.ns import qn

    definitions = document.part.numbering_part.element
    abstracts = {
        a.get(qn("w:abstractNumId")): a
        for a in definitions.findall(qn("w:abstractNum"))
    }
    nums = {n.get(qn("w:numId")): n for n in definitions.findall(qn("w:num"))}
    counters = {}

    def numbering_for(paragraph):
        props = paragraph._p.xpath("./w:pPr/w:numPr")
        style = paragraph.style
        seen = set()
        while not props and style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            props = style.element.xpath("./w:pPr/w:numPr")
            style = style.base_style
        if not props:
            return None
        numid = props[0].find(qn("w:numId"))
        ilvl = props[0].find(qn("w:ilvl"))
        if numid is None:
            return None
        numid = numid.get(qn("w:val"))
        level = int(ilvl.get(qn("w:val"))) if ilvl is not None else 0
        definition = nums.get(numid)
        if definition is None or numid == "0":
            return None
        aid = definition.find(qn("w:abstractNumId"))
        abstract = abstracts.get(aid.get(qn("w:val"))) if aid is not None else None
        lvl = (
            next(
                (
                    x
                    for x in abstract.findall(qn("w:lvl"))
                    if int(x.get(qn("w:ilvl"))) == level
                ),
                None,
            )
            if abstract is not None
            else None
        )
        fmt = lvl.find(qn("w:numFmt")) if lvl is not None else None
        start = lvl.find(qn("w:start")) if lvl is not None else None
        initial = int(start.get(qn("w:val"))) if start is not None else 1
        for override in definition.findall(qn("w:lvlOverride")):
            if int(override.get(qn("w:ilvl"))) == level:
                begin = override.find(qn("w:startOverride"))
                if begin is not None:
                    initial = int(begin.get(qn("w:val")))
        key = (numid, level)
        counters[key] = counters.get(key, initial - 1) + 1
        for child in list(counters):
            if child[0] == numid and child[1] > level:
                counters.pop(child)
        return dict(
            id=numid,
            level=level,
            format=fmt.get(qn("w:val")) if fmt is not None else "unknown",
            value=counters[key],
        )

    def visit(container, path="body", depth=0, in_table=False):
        if depth > 8:
            raise ValueError("DOCX table nesting exceeds limit")
        for n, part in enumerate(container.iter_inner_content(), 1):
            position = f"{path}/{n}"
            if isinstance(part, Table):
                seen = set()
                for r, row in enumerate(part.rows, 1):
                    for c, cell in enumerate(row.cells, 1):
                        if cell._tc in seen:
                            continue
                        seen.add(cell._tc)
                        yield from visit(
                            cell, f"{position}/row{r}/cell{c}", depth + 1, True
                        )
            else:
                has_image = bool(part._p.xpath(".//w:drawing | .//w:pict"))
                text = part.text.strip()
                if text or has_image:
                    yield (
                        text or "[DOCX image without transcribed text]",
                        position,
                        in_table,
                        has_image,
                        has_image and not text,
                        numbering_for(part),
                    )

    return list(visit(document))


def review_bibliography(conn, document_id, bibliography, reviewer, note):
    from app.question_quality import Evidence

    if (
        not reviewer.strip()
        or len(note.strip()) < 20
        or set(bibliography) != {"title", "authors", "edition", "isbn"}
        or any(
            not isinstance(bibliography.get(k), str) or not bibliography[k].strip()
            for k in ("title", "authors", "edition")
        )
    ):
        raise ValueError(
            "A complete explicit bibliography and signed review are required"
        )
    Evidence.valid_isbn(bibliography["isbn"])
    row = conn.execute(
        "SELECT d.* FROM source_documents d JOIN source_versions v ON v.document_id=d.id WHERE d.id=%s FOR UPDATE OF d",
        (document_id,),
    ).fetchone()
    if not row or not active_document(conn, document_id):
        raise ValueError("Current OCR version required")
    if row["metadata"].get("material_status") == "synthetic_test":
        raise ValueError("Synthetic material cannot be approved as real")
    return conn.execute(
        "INSERT INTO source_bibliography_reviews(document_id,source_sha256,bibliography,reviewer,note) VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (document_id, row["sha256"], Jsonb(bibliography), reviewer, note),
    ).fetchone()


def bibliography(conn, document_id):
    return conn.execute(
        "SELECT * FROM source_bibliography_reviews WHERE document_id=%s ORDER BY id DESC LIMIT 1",
        (document_id,),
    ).fetchone()


def review_snapshot(conn, rows):
    events = conn.execute(
        "SELECT DISTINCT ON(unit_id,review_kind) * FROM source_review_events WHERE unit_id=ANY(%s) ORDER BY unit_id,review_kind,id DESC",
        ([r["id"] for r in rows],),
    ).fetchall()
    fingerprints = {r["id"]: unit_sha(r) for r in rows}
    result = {r["id"]: {} for r in rows}
    for event in events:
        result[event["unit_id"]][event["review_kind"]] = (
            event["status"] == "approved"
            and event["unit_sha256"] == fingerprints[event["unit_id"]]
        )
    return result
