"""Parse the user-designated genuine past-exam files into candidate questions.

Preserve supplied wording, year and type; parser uncertainty concerns boundaries,
OCR/images and answer support, not whether the supplied exams are real.
"""

import re

VERSION = "transcript-index/1.2"
STYLE_SOURCE_BLOCKERS = frozenset({
    "corrupt_ocr", "untranscribed_image", "image_dependent",
    "docx_table_requires_review", "complex_table", "ocr_uncertain",
    "transcription_gap_reported",
})
TYPES = {
    "多项选择题": "multiple_choice",
    "多项选择": "multiple_choice",
    "单项选择题": "single_choice",
    "单项选择": "single_choice",
    "单选题": "single_choice",
    "单选": "single_choice",
    "选择题": "single_choice",
    "填空题": "fill_blank",
    "填空": "fill_blank",
    "名词解释题": "term",
    "名词解释": "term",
    "简答题": "short_answer",
    "简答": "short_answer",
    "论述题": "essay",
    "论述": "essay",
    "问答题": "question_answer",
    "问答": "question_answer",
    "判断改错题": "true_false",
    "判断题": "true_false",
    "是非题": "true_false",
    "实验设计题": "experimental_design",
    "实验设计": "experimental_design",
    "计算题": "calculation",
    "综合题": "comprehensive",
}
FORMAL_TYPES = {"single_choice", "fill_blank", "term", "short_answer", "essay"}


def year_heading(text, item):
    if len(text) > 180 or re.search(
        r"[。？！?]|(?:19|20)\d{2}\s*[-~—至]\s*(?:19|20)\d{2}", text
    ):
        return None
    match = re.match(r"^\s*[(（]?((?:19|20)\d{2})\s*年", text)
    if not match or not re.search(r"真题|试题|试卷|考试|考研", text):
        return None
    value = int(match[1])
    return value if item["year_start"] <= value <= item["year_end"] else None


def section_heading(text):
    if len(text) > 180:
        return None
    value = re.sub(
        r"^\s*(?:[一二三四五六七八九十百IVX\d]+[、丶.．:：])\s*", "", text
    ).strip()
    for label in sorted(TYPES, key=len, reverse=True):
        if value.startswith(label) and (
            len(value) == len(label) or value[len(label)] in " （(:：，,"
        ):
            count = re.search(r"(\d+)\s*(?:小)?题", value[len(label) :])
            return {
                "type": TYPES[label],
                "label": label,
                "declared_questions": int(count[1]) if count else None,
            }
    return None


def _raw_candidates(units, item):
    year = None
    qtype = None
    segment = None
    section = None
    parent = None
    answer_mode = False
    for unit in units:
        text = unit["text"]
        uid = unit.get("id", unit.get("ordinal"))
        heading = year_heading(text, item)
        if re.search(r"年份不详|年份未知|年份待确认|未标年份", text):
            year = None
            qtype = None
            parent = None
            segment = None
        if heading is not None:
            year = heading
            segment = uid
            qtype = None
            parent = None
            section = None
            answer_mode = False
        sec = section_heading(text)
        if sec:
            qtype = sec["type"]
            section = text
            parent = None
            answer_mode = False
        answer_header = bool(
            re.match(r"^\s*(?:参考答案|答案(?:及解析)?|解答|解析)\s*(?:[:：]|$)", text)
        )
        if answer_header:
            answer_mode = True
            parent = None
        editorial = bool(re.match(r"^.{0,12}(?:说明|注释|校订说明)\d*[:：]", text))
        numbering_match = re.search(
            r"\|num:(\d+):(\d+):([A-Za-z]+):(\d+)$", unit.get("section", "")
        )
        native = (
            dict(
                id=numbering_match[1],
                level=int(numbering_match[2]),
                format=numbering_match[3],
                value=numbering_match[4],
            )
            if numbering_match
            else None
        )
        match = (
            None
            if heading is not None or sec or editorial
            else re.match(r"^\s*(\d{1,3})[.、．)）]\s*(.+)", text, re.S)
        )
        if match and re.match(r"^\s*\d+\.\d+[A-Za-z]", text):
            match = None  # Scientific names such as 5.8S rRNA are not question 5.
        numbered_native = bool(
            native
            and native["level"] == 0
            and native["format"] in ("decimal", "decimalZero")
        )
        option = bool(
            native and native["format"] in ("lowerLetter", "upperLetter", "bullet")
        )
        nested = bool(native and native["level"] > 0)
        role = (
            "year_heading"
            if heading is not None
            else "section_heading"
            if sec
            else "editorial_note"
            if editorial
            else "answer"
            if answer_mode
            else "option"
            if option
            else "subquestion"
            if nested
            else "question_start"
            if match or numbered_native
            else "continuation"
            if parent
            else "unassigned"
        )
        if role == "question_start":
            parent = uid
        concerns = [
            *unit.get("quality_flags", []),
        ]
        if year is None:
            concerns.append("year_unassigned")
        if qtype is None:
            concerns.append("question_type_unknown")
        elif qtype not in FORMAL_TYPES:
            concerns.append("type_requires_automatic_mapping")
        if role == "unassigned":
            concerns.append("question_boundary_unconfirmed")
        if role == "continuation":
            concerns.append("continuation_not_separate_question")
        if role == "answer":
            concerns.append("answer_requires_independent_textbook_check")
        yield {
            "unit_id": uid,
            "year": year,
            "question_number": (native["value"] if numbered_native else match[1])
            if role == "question_start"
            else None,
            "question_type": qtype,
            "transcription": text,
            "concerns": sorted(set(concerns)),
            "analysis": {
                "version": VERSION,
                "word_numbering": native,
                "role": role,
                "year_heading_unit_id": segment,
                "section": section,
                "parent_question_unit_id": parent,
                "declared_questions": sec["declared_questions"] if sec else None,
            },
        }


def candidates(units, item):
    """Block style anchors whose separately extracted continuation is a figure.

    DOCX can store question wording in one paragraph and its drawing in the
    following image-only paragraph. Checking only the question-start text
    would incorrectly admit an incomplete real question as a clean style
    example. Do not infer image content; attach the unresolved source flags to
    its parent candidate while retaining the exact original transcription.
    """
    parsed = list(_raw_candidates(units, item))
    beginnings = {
        row["unit_id"]: row
        for row in parsed if row["analysis"]["role"] == "question_start"
    }
    for row in parsed:
        if row["analysis"]["role"] not in (
            "continuation", "subquestion", "option"
        ):
            continue
        parent = row["analysis"]["parent_question_unit_id"]
        if parent not in beginnings:
            continue
        risks = set(row["concerns"]) & STYLE_SOURCE_BLOCKERS
        if risks:
            original = beginnings[parent]
            original["concerns"] = sorted(set(original["concerns"]) | risks)
    yield from parsed
