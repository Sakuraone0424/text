"""Conservative TOC/heading alignment; never treats TOC page numbers as PDF/OCR maps."""

from collections import defaultdict
import re

CHINESE = re.compile(r"^第\s*([一二三四五六七八九十百零〇\d]+)\s*章\s*(.*)$")
NUMERIC_TOC = re.compile(r"^(\d{1,2})\s+(.{1,110}?)\s+\d{1,4}$")
SECTION_TOC = re.compile(r"^(\d{1,2}\.\d{1,2}(?:\.\d+)*)\s+(.{1,170}?)\s+\d{1,4}$")
LEADER = re.compile(r"(?:\.{3,}|…{1,}|·{3,})\s*\d+\s*$")


def compact(value):
    return re.sub(r"\s+", "", value)


def chinese_number(value):
    if value.isdecimal():
        return int(value)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["〇"] = 0
    total = current = 0
    for char in value:
        if char == "百":
            total += (current or 1) * 100
            current = 0
        elif char == "十":
            total += (current or 1) * 10
            current = 0
        else:
            current = digits.get(char, 0)
    return total + current


def book_layout(text, page_pattern):
    lines = text.splitlines()
    grouped = defaultdict(list)
    page = 0
    line_page = {}
    for n, line in enumerate(lines):
        if page_pattern.fullmatch(line):
            page += 1
        grouped[page].append((n, line.strip()))
        line_page[n] = page

    def score(rows):
        return sum(
            bool(
                LEADER.search(s) or NUMERIC_TOC.fullmatch(s) or SECTION_TOC.fullmatch(s)
            )
            for _, s in rows
        )

    candidates = [p for p, rows in grouped.items() if p and score(rows) >= 3]
    toc = set()
    if candidates:
        p = min(candidates)
        while p in grouped and score(grouped[p]) >= 1:
            toc.add(p)
            p += 1
    chapters = {}
    sections = {}
    catalogue = []
    for p in sorted(toc):
        for n, line in grouped[p]:
            match = CHINESE.fullmatch(line)
            numeric = NUMERIC_TOC.fullmatch(line)
            section = SECTION_TOC.fullmatch(line)
            if match and LEADER.search(line):
                title = LEADER.sub("", line).strip()
                key = chinese_number(match[1])
                chapters.setdefault(key, title)
                catalogue.append(
                    {
                        "chapter_number": key,
                        "title": title,
                        "toc_ocr_page": p,
                        "line": n + 1,
                    }
                )
            elif numeric:
                key = int(numeric[1])
                title = f"{key} {numeric[2].strip()}"
                chapters.setdefault(key, title)
                catalogue.append(
                    {
                        "chapter_number": key,
                        "title": title,
                        "toc_ocr_page": p,
                        "line": n + 1,
                    }
                )
            elif section:
                sections[section[1]] = section[2].strip()
    headings = {}
    for n, line in enumerate(lines):
        s = line.strip()
        if line_page[n] in toc or not s or len(s) > 190 or re.search(r"[。！？；]", s):
            continue
        chinese = CHINESE.fullmatch(s)
        if chinese:
            key = chinese_number(chinese[1])
            headings[n] = chapters.get(key, s)
            continue
        numeric = re.fullmatch(r"(\d{1,2})\s+(.+)", s)
        if numeric and int(numeric[1]) in chapters:
            key = int(numeric[1])
            expected = compact(re.sub(r"^\d+\s+", "", chapters[key]))
            body = compact(re.sub(r"\s+\d{1,4}$", "", numeric[2]))
            if body == expected:
                headings[n] = chapters[key]
                continue
        if s.isdecimal() and int(s) in chapters:
            key = int(s)
            following = []
            for later in lines[n + 1 : n + 5]:
                if page_pattern.fullmatch(later):
                    break
                if later.strip():
                    following.append(later.strip())
            expected = compact(re.sub(r"^\d+\s+", "", chapters[key]))
            if any(
                compact("".join(following[:k])) == expected
                for k in range(1, len(following) + 1)
            ):
                headings[n] = chapters[key]
                continue
        section = re.match(r"^(\d{1,2}\.\d{1,2}(?:\.\d+)*)\s+(.+)", s)
        if section and section[1] in sections:
            actual = compact(section[2])
            expected = compact(sections[section[1]])
            if min(len(actual), len(expected)) >= 5 and (
                actual.startswith(expected) or expected.startswith(actual)
            ):
                key = int(section[1].split(".")[0])
                if key in chapters:
                    headings[n] = chapters[key]
    return {
        "toc_pages": toc,
        "chapters": chapters,
        "headings": headings,
        "catalogue": catalogue,
    }
