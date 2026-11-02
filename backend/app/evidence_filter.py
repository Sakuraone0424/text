"""Which textbook paragraphs are worth writing a question from (or answering one from).

A paragraph that is mostly a figure caption, a table, a table of contents or a chapter heading, that is very short,
or whose OCR carries a quality flag, cannot support an answer: a request built on it ends as
`insufficient_textbook_evidence` after the model call has been paid for. These are filtered out BEFORE the call.
"""

import re

MIN_CHARS = 80
_CAPTION = re.compile(r"^\s*(?:图|表|附图|附表|Figure|Fig\.|Table)\s*[\dA-Za-z一二三四五六七八九十]+[\-．.\d]*", re.I)
_HEADING = re.compile(r"^\s*(?:第\s*[\d一二三四五六七八九十百]+\s*[章节篇部]|[\d]+(?:\.\d+){0,3}\s+\S{1,30}$|[一二三四五六七八九十]+[、．.]\s*\S{1,30}$)")
_TOC_LINE = re.compile(r"(?:\.{3,}|…{2,}|·{3,}|\s{3,})\s*\d{1,4}\s*$")


def _lines(text):
    return [ln for ln in re.split(r"[\r\n]+", text) if ln.strip()]


def reason_unusable(text, quality_flags=(), min_chars=MIN_CHARS):
    """None if the paragraph is usable, else a short code saying why not."""
    body = (text or "").strip()
    if quality_flags:
        return "ocr_quality_flag"
    if len(body) < min_chars:
        return "too_short"
    if re.match(r"^\s*(?:目\s*录|Contents|CONTENTS)\s*$", body.splitlines()[0]):
        return "table_of_contents"
    lines = _lines(body)
    if len(lines) >= 3 and sum(1 for ln in lines if _TOC_LINE.search(ln)) >= max(2, len(lines) // 2):
        return "table_of_contents"
    if lines and sum(1 for ln in lines if _CAPTION.match(ln)) * 2 >= len(lines) and len(body) < 400:
        return "figure_or_table_caption"
    if body.count("|") >= 3 or body.count("\t") >= 3 or len(re.findall(r"\s{3,}", body)) >= 4:
        return "table"
    letters = sum(1 for c in body if c.isalpha())
    if letters / max(1, len(body)) < 0.5:
        return "numbers_and_symbols"  # a data table or formula block, not prose
    if len(lines) <= 2 and all(_HEADING.match(ln) for ln in lines):
        return "heading"
    return None


def usable(text, quality_flags=(), min_chars=MIN_CHARS):
    return reason_unusable(text, quality_flags, min_chars) is None
