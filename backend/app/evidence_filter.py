"""Which textbook paragraphs are worth writing a question from (or answering one from).

A paragraph that is mostly a figure caption, a table, a table of contents or a chapter heading, that is very short,
or whose OCR carries a quality flag, cannot support an answer: a request built on it ends as
`insufficient_textbook_evidence` after the model call has been paid for. These are filtered out BEFORE the call.
"""

import re

MIN_CHARS = 80
# OCR quality flags that make a paragraph unusable as evidence (an unreadable character, a figure or table the text
# depends on, a reported gap). The mild ones only say the paragraph holds Greek letters, ATP/Km or a structure formula:
# the paragraph stays usable.
SEVERE_FLAGS = frozenset({
    "corrupt_ocr", "untranscribed_image", "image_dependent", "docx_table_requires_review",
    "complex_table", "ocr_uncertain", "transcription_gap_reported",
})
MILD_FLAGS = frozenset({"chemical_structure", "scientific_notation_requires_source_review"})
SEVERE_SQL = "NOT (u.quality_flags ?| ARRAY[" + ",".join(f"'{f}'" for f in sorted(SEVERE_FLAGS)) + "])"


def severe(flags):
    return bool(set(flags or ()) & SEVERE_FLAGS)
_CAPTION = re.compile(r"^\s*(?:图|表|附图|附表|Figure|Fig\.|Table)\s*[\dA-Za-z一二三四五六七八九十]+[\-．.\d]*", re.I)
_HEADING = re.compile(r"^\s*(?:第\s*[\d一二三四五六七八九十百]+\s*[章节篇部]|[\d]+(?:\.\d+){0,3}\s+\S{1,30}$|[一二三四五六七八九十]+[、．.]\s*\S{1,30}$)")
_TOC_LINE = re.compile(r"(?:\.{3,}|…{2,}|·{3,}|\s{3,})\s*\d{1,4}\s*$")


# A bibliography entry ("Izawa D, Pines J. How ... mitosis. Nature Cell Biology, 2011, 13(3): 223-233.") says which paper exists,
# not what is true: a question written from it asks about authors and titles. Year + volume(issue) + page range, a Chinese
# 「[J]」 citation, or a 「参考文献」 heading marks one.
_JOURNAL_REF = re.compile(r"(?:19|20)\d{2}\s*[,;，；.]?\s*\d{1,4}\s*(?:\(\s*\d+\s*\))?\s*[:：]\s*[A-Za-z]?\d+\s*[-–—~]\s*[A-Za-z]?\d+")
_CN_CITATION = re.compile(r"\[\s*[JMDCRN]\s*(?:/[A-Z]+)?\s*\]")
_REFERENCES_HEADING = re.compile(r"^\s*(?:参考文献|主要参考文献|References|REFERENCES|Bibliography)\s*[:：]?\s*$", re.M)


def is_reference_entry(text):
    """True for a paragraph that is (or consists of) literature-list entries."""
    body = (text or "").strip()
    return bool(_JOURNAL_REF.search(body) or _CN_CITATION.search(body) or _REFERENCES_HEADING.search(body))


def _lines(text):
    return [ln for ln in re.split(r"[\r\n]+", text) if ln.strip()]


def reason_unusable(text, quality_flags=(), min_chars=MIN_CHARS):
    """None if the paragraph is usable, else a short code saying why not."""
    body = (text or "").strip()
    if severe(quality_flags):
        return "ocr_quality_flag"
    if len(body) < min_chars:
        return "too_short"
    if is_reference_entry(body):
        return "reference_list"
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
