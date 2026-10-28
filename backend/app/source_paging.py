"""OCR page identity is separate from physical PDF and printed page identity."""

import re
from collections import Counter

PAGE = re.compile(
    r"^\s*(?:\[PAGE\s*(\d+)\]|\[第\s*(\d+)\s*页\]|[-=]*\s*第\s*(\d+)\s*页\s*[-=]*|P(?:AGE)?\s*0*(\d+)|\\?={3,}\s*(?:(上册|下册)\s+)?page-(\d+)\s*={3,})\s*$",
    re.I,
)
HEADING = re.compile(r"^(第[一二三四五六七八九十百\d]+[章节]|\d+(?:\.\d+){0,3}[、.\s])")


def parse_pages(text, metadata):
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
    if lengths is not None:
        if (
            not isinstance(lengths, list)
            or len(lengths) != len(offsets)
            or any(type(n) is not int or n < 1 for n in lengths)
            or offsets != [sum(lengths[:i]) for i in range(len(lengths))]
        ):
            raise ValueError(
                "Declared OCR volume lengths and offsets must be contiguous and consistent"
            )
    expected = metadata.get("expected_pages")
    if expected is not None and (
        type(expected) is not int or not 1 <= expected <= 4000
    ):
        raise ValueError(
            "expected_pages must be a positive bounded whole-book page count"
        )
    if expected and lengths and sum(lengths) != expected:
        raise ValueError("OCR volume lengths differ from expected whole-book pages")
    for name in ("pdf_pages", "printed_pages"):
        mapping = metadata.get(name, {})
        if not isinstance(mapping, dict) or any(
            not isinstance(k, str) or not k.isdecimal() or int(k) < 1 for k in mapping
        ):
            raise ValueError(
                f"{name} must use explicit positive page-number string keys"
            )
        if name == "pdf_pages" and any(
            type(v) is not int or not 1 <= v <= 4000 for v in mapping.values()
        ):
            raise ValueError(
                "Physical PDF page mapping must use bounded positive integers"
            )
        if name == "printed_pages" and any(
            not isinstance(v, str) or not v.strip() or len(v) > 100
            for v in mapping.values()
        ):
            raise ValueError(
                "Printed page labels must be explicit nonempty strings, never guessed"
            )
    layout = None
    if metadata.get("source_mode") == "ocr_only":
        from app.source_structure import book_layout

        layout = book_layout(text, PAGE)
    marker_index = 0
    volume, previous, page = 0, 0, None
    chapter, section = metadata.get("chapter", "unclassified"), ""
    rows, pages, buffer = [], [], []
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
                global_page = offsets[volume] + page if page else None
                physical = metadata.get("pdf_pages", {}).get(
                    str(global_page), global_page
                )
                rows.append(
                    dict(
                        chapter=chapter,
                        section=section,
                        text=paragraph,
                        pdf_page=physical,
                        printed_page=metadata.get("printed_pages", {}).get(
                            str(physical)
                        ),
                        ocr_page=page,
                        volume=volume + 1 if page else None,
                        ocr_global_page=global_page,
                        quality_flags=flags,
                    )
                )
            buffer.clear()

    for line_index, line in enumerate(text.splitlines()):
        match = PAGE.fullmatch(line)
        if match:
            flush()
            marker_index += 1
            if layout:
                if marker_index in layout["toc_pages"]:
                    chapter = "unclassified"
                    section = "contents"
                elif marker_index - 1 in layout["toc_pages"]:
                    chapter = "unclassified"
                    section = ""
            current = int(
                next(v for v in match.groups() if v is not None and v.isdecimal())
            )
            label = match.group(5)
            if "page_label" in metadata and label != metadata["page_label"]:
                raise ValueError(
                    "OCR volume label differs from declared corrected file"
                )
            if current < 1:
                raise ValueError("Page numbers start at one")
            if current <= previous:
                if current != 1 or volume + 1 >= len(offsets):
                    raise ValueError(
                        "Page reset requires explicit volume offsets and a restart at page 1"
                    )
                if lengths and previous != lengths[volume]:
                    raise ValueError("Page reset before declared OCR volume end")
                volume += 1
            if lengths and current > lengths[volume]:
                raise ValueError("OCR page outside declared volume")
            page, previous = current, current
            global_page = offsets[volume] + page
            if global_page in observed:
                raise ValueError("Duplicate whole-book OCR page marker")
            observed.add(global_page)
            physical = (
                None
                if metadata.get("source_mode") == "ocr_only"
                else metadata.get("pdf_pages", {}).get(str(global_page), global_page)
            )
            pages.append(
                dict(
                    marker_text=line,
                    marker_label=label,
                    ocr_global_page=global_page,
                    volume=volume + 1,
                    ocr_page=page,
                    pdf_page=physical,
                    printed_page=metadata.get("printed_pages", {}).get(str(physical)),
                )
            )
            continue
        if re.match(r"^\s*\\?=+.*page", line, re.I):
            raise ValueError(
                "Malformed OCR page delimiter; expected ===== page-001 ====="
            )
        in_toc = bool(layout and marker_index in layout["toc_pages"])
        if layout and line_index in layout["headings"] and not in_toc:
            new_chapter = layout["headings"][line_index]
            if chapter != new_chapter:
                flush()
                chapter = new_chapter
                section = ""
        if HEADING.match(line.strip()) and not in_toc:
            flush()
            section = line.strip()
            if not layout and re.match(r"^第.+章", line.strip()):
                chapter = section
        if not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()
    if observed and any(r["ocr_page"] is None for r in rows):
        raise ValueError("Unpaged content before the first OCR page marker")
    if expected is not None and observed != set(range(1, expected + 1)):
        missing = sorted(set(range(1, expected + 1)) - observed)
        extra = sorted(observed - set(range(1, expected + 1)))
        raise ValueError(
            f"Page coverage does not match declared whole-book pages: missing={missing[:20]}, extra={extra[:20]}"
        )
    if set(metadata.get("pdf_pages", {})) - set(map(str, observed)):
        raise ValueError("Physical mapping contains unobserved OCR page keys")
    physical_pages = {p["pdf_page"] for p in pages if p["pdf_page"] is not None}
    if metadata.get("source_mode") != "ocr_only" and len(physical_pages) != len(pages):
        raise ValueError("Multiple OCR pages map to the same physical PDF page")
    if set(metadata.get("printed_pages", {})) - set(map(str, physical_pages)):
        raise ValueError("Printed mapping contains unobserved physical page keys")
    if not rows:
        raise ValueError(
            "No text extracted; supply existing OCR, do not silently invent content"
        )
    counts = Counter(r["ocr_global_page"] for r in rows)
    characters = Counter()
    for r in rows:
        characters[r["ocr_global_page"]] += len(r["text"])
    for p in pages:
        p["paragraph_count"] = counts[p["ocr_global_page"]]
        p["text_characters"] = characters[p["ocr_global_page"]]
    return rows, pages
