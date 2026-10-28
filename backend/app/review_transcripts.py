"""Versioned structural overlay on immutable transcription; never human approval."""

import re
from collections import Counter
from app.transcript_analysis import candidates, TYPES, section_heading

VERSION = "transcript-review/1.5"


def heading(text):
    cleaned = re.sub(r"^\s*([一二三四五六七八九十]+)\s+(?=\S)", r"\1、", text)
    result = section_heading(cleaned)
    if not result and re.match(
        r"^[一二三四五六七八九十]+[、丶.．]\s*(?:填充和选择题|叙述题和计算题|选择填空)",
        cleaned,
    ):
        result = dict(type="mixed", label="mixed", declared_questions=None)
    if not result:
        # Some compilations express section names as instructions, including
        # Word-generated Chinese list numbers absent from the extracted text.
        # Require a per-question and total score declaration: an ordinary
        # question asking to explain terms must not become a section header.
        body = re.sub(r"^[一二三四五六七八九十]+[、丶.．]\s*", "", cleaned)
        label = re.match(
            r"^(?:简要)?(解释(?:以下|下列)名词|回答(?:以下|下列)问题)\s*[（(]", body
        )
        if (
            label
            and re.search(r"每(?:小)?题\s*\d+\s*分", body)
            and re.search(r"共\s*\d+\s*分", body)
        ):
            result = dict(
                type="term" if label[1].startswith("解释") else "short_answer",
                label=label[1],
                declared_questions=None,
            )
    if not result:
        return None
    # "第1、2题各..." is not a declaration of total question count.
    count = re.search(
        r"(?:共\s*|[（(]\s*)(\d+)\s*(?:小)?题(?=[，,）)；;\s]|$)", cleaned
    )
    result["declared_questions"] = int(count[1]) if count else None
    return result


def analyze(units, item):
    altered = []
    for unit in units:
        sec = heading(unit["text"])
        # Feed canonical heading to base role parser, retain original text below.
        if sec:
            label = next((k for k, v in TYPES.items() if v == sec["type"]), "综合题")
            altered.append({**unit, "text": label})
        else:
            altered.append(unit)
    rows = list(candidates(altered, item))
    originals = {u["id"]: u for u in units}
    active = None
    for i, row in enumerate(rows):
        original = originals[row["unit_id"]]
        row["transcription"] = original["text"]
        sec = heading(original["text"])
        row["analysis"]["version"] = VERSION
        row["analysis"]["structural_review"] = "automated_pending"
        if (
            re.fullmatch(r"[+−-]?\d+(?:\.\d+)?", original["text"].strip())
            and "/cell" in original["section"]
        ):
            row["question_number"] = None
            row["analysis"]["role"] = "table_value"
        if sec:
            active = sec
            row["analysis"]["declared_questions"] = sec["declared_questions"]
        elif row["analysis"]["role"] == "year_heading":
            active = None
        if active:
            row["question_type"] = active["type"]
        if (
            row["analysis"]["role"] in ("unassigned", "continuation")
            and not row["question_number"]
        ):
            m = re.match(r"^\s*(\d{1,3})\s*(?=[\u4e00-\u9fff])", original["text"])
            if m and active:
                prior = next(
                    (r for r in reversed(rows[:i]) if r["question_number"]), None
                )
                if prior and int(prior["question_number"]) + 1 == int(m[1]):
                    row["question_number"] = m[1]
                    row["analysis"]["role"] = "question_start"
                    row["analysis"]["number_recovery"] = (
                        "missing_delimiter_sequence_supported"
                    )
    # A distinct level-zero list between two members of the same main list is
    # a proposed sub-list. Keep this inference visible for reviewer confirmation.
    section = None
    main = None
    parent = None
    for i, row in enumerate(rows):
        role = row["analysis"]["role"]
        if role in ("year_heading", "section_heading"):
            section = i
            main = None
            parent = None
        n = row["analysis"]["word_numbering"]
        if role == "question_start" and n:
            if main is None:
                main = n["id"]
            if n["id"] != main and parent is not None:
                later = []
                for r in rows[i + 1 :]:
                    if r["analysis"]["role"] in ("year_heading", "section_heading"):
                        break
                    later.append(r)
                parent_row = next((r for r in rows if r["unit_id"] == parent), None)
                explicit_children = parent_row is not None and bool(
                    re.search(r"[:：]\s*$|问[：:]?\s*$", parent_row["transcription"])
                )
                if explicit_children or any(
                    r["analysis"]["word_numbering"]
                    and r["analysis"]["word_numbering"]["id"] == main
                    for r in later
                ):
                    row["analysis"]["role"] = "probable_subquestion"
                    row["analysis"]["inferred_parent"] = parent
                    row["analysis"]["original_question_number"] = row["question_number"]
                    row["question_number"] = None
                    row["concerns"].append("subquestion_grouping_requires_review")
        if row["analysis"]["role"] == "question_start":
            parent = row["unit_id"]
        row["analysis"]["parent_question_unit_id"] = (
            parent
            if role
            not in ("year_heading", "section_heading", "answer", "editorial_note")
            else None
        )
        row["analysis"]["section_index"] = section
    return rows


def questions_and_years(units, item):
    rows = analyze(units, item)
    questions = []
    for row in rows:
        if row["analysis"]["role"] != "question_start":
            continue
        members = [
            r
            for r in rows
            if r["analysis"]["parent_question_unit_id"] == row["unit_id"]
        ]
        text = "\n".join(r["transcription"] for r in members)
        blank_pattern = r"_{2,}|＿{2,}|[（(]\s+[）)]"
        if row["question_type"] == "fill_blank":
            blank_pattern += r"|[（(]\s*\d{0,3}\s*[）)]"
        blanks = [
            dict(
                index=i + 1,
                start=m.start(),
                end=m.end(),
                marker=m[0],
                source_number=int(re.search(r"\d+", m[0])[0])
                if re.search(r"\d+", m[0])
                else None,
            )
            for i, m in enumerate(re.finditer(blank_pattern, text))
        ]
        questions.append(
            dict(
                unit_id=row["unit_id"],
                year=row["year"],
                paper=row["analysis"]["year_heading_unit_id"],
                section=row["analysis"]["section_index"],
                number=row["question_number"],
                question_type=row["question_type"],
                text=text,
                unit_ids=[r["unit_id"] for r in members],
                blanks=blanks,
                concerns=sorted({f for r in members for f in r["concerns"]}),
                status="pending",
                original_verified=False,
            )
        )
    years = []
    for year in range(item["year_start"], item["year_end"] + 1):
        qs = [q for q in questions if q["year"] == year]
        sections = []
        for i, r in enumerate(rows):
            if r["year"] != year or r["analysis"]["role"] != "section_heading":
                continue
            selected = [q for q in qs if q["section"] == i]
            numbers = [int(q["number"]) for q in selected]
            count = r["analysis"]["declared_questions"]
            sections.append(
                dict(
                    unit_id=r["unit_id"],
                    type=r["question_type"],
                    declared=count,
                    candidates=len(selected),
                    missing_numbers=sorted(
                        set(range(1, max(numbers) + 1)) - set(numbers)
                    )
                    if numbers
                    else [],
                    repeated_numbers=[n for n, c in Counter(numbers).items() if c > 1],
                    declared_count_conflict=count is not None
                    and count != len(selected),
                )
            )
        years.append(
            dict(
                year=year,
                candidate_questions=len(qs),
                types=dict(Counter(q["question_type"] or "unknown" for q in qs)),
                unknown_types=sum(q["question_type"] is None for q in qs),
                sections=sections,
                blank_markers=sum(len(q["blanks"]) for q in qs),
                human_text_checked=0,
                original_verified=0,
                official_answers_verified=0,
                trusted_frequency_eligible=0,
                source_level="ocr_compilation",
                status="pending",
            )
        )
    return questions, years, rows
