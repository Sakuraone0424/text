"""One chapter name everywhere: the textbook's own (「第六章 核酸」).

Past-exam entries carry their own chapter labels ("6 核酸", "2 蛋白质 结构": a number and a few words); the textbook names its chapters
"第六章 …". This maps the first onto the second so mistakes, weak chapters, chapter mastery and the paper review all use one name.
A label that cannot be mapped is shown as 「其他」 and counted, so it can be looked at (never guessed).
"""
import re
import time
from collections import Counter

OTHER = "其他"
_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CACHE = {}          # subject -> (loaded at, [(name, number, compact)])
CACHE_SECONDS = 300
_NUMBER = re.compile(r"第\s*([0-9]+|[零〇一二两三四五六七八九十百]+)\s*[章篇讲]")
_LEADING = re.compile(r"^\s*(?:第\s*)?([0-9]+)\s*[章篇讲.、．:：)）\s-]*")


def chinese_number(text):
    """'十二' -> 12, '三十' -> 30, '九' -> 9, '百' not supported beyond 99."""
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        head, _, tail = text.partition("十")
        return (_DIGITS.get(head, 1) if head else 1) * 10 + (_DIGITS.get(tail, 0) if tail else 0)
    return _DIGITS.get(text)


def number_of(label):
    """The chapter number a label carries ('第六章 核酸', '6 核酸', '第 6 章'), or None."""
    m = _NUMBER.search(label or "")
    if m:
        return chinese_number(m.group(1))
    m = _LEADING.match(label or "")
    return int(m.group(1)) if m else None


def words_of(label):
    """The words of a label without its number and 第…章 decoration."""
    text = _NUMBER.sub("", label or "")
    text = _LEADING.sub("", text)
    return re.sub(r"[\s·\-—:：、，,（）()]+", "", text)


def to_chinese(n):
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n - 10] if n > 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    return str(n)


def canonical(name):
    """The textbook's way of writing a chapter: 「第二十三章 脂质合成」. A name that is only a number and words ('23 脂质合成') is written that way."""
    if _NUMBER.search(name or "") or not re.match(r"^\s*\d", name or ""):
        return name
    number, words = number_of(name), re.sub(r"^\s*(?:第\s*)?[0-9]+\s*[章篇讲.、．:：)）\s-]*", "", name).strip()
    return f"第{to_chinese(number)}章 {words}" if number and words else name


_GENERIC = ("合成", "分解", "代谢", "作用", "调节", "基础", "生物")
_FILLER = set("的与和及之在中对以其等")


def _keys(words):
    text = words
    for g in _GENERIC:
        text = text.replace(g, "")
    return {c for c in text if c not in _FILLER} or {c for c in words if c not in _FILLER}


def _similarity(a, b):
    ka, kb = _keys(a), _keys(b)
    return 2 * len(ka & kb) / (len(ka) + len(kb)) if ka and kb else 0.0


def compact(label):
    return re.sub(r"\s+", "", label or "")


def textbook_chapters(conn, subject):
    """[(name, number, words)] of the textbook chapters of one subject (cached for a few minutes)."""
    hit = _CACHE.get(subject)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    from app.source_policy import current_source_sql

    rows = conn.execute(
        "SELECT DISTINCT u.chapter FROM source_units u JOIN source_documents d ON d.id=u.document_id WHERE "
        + current_source_sql("d", kind="textbook")
        + " AND d.subject=%s AND u.chapter NOT IN ('','unclassified','contents') ORDER BY u.chapter",
        (subject,),
    ).fetchall()
    names = [(r["chapter"], number_of(r["chapter"]), words_of(r["chapter"])) for r in rows]
    _CACHE[subject] = (time.monotonic(), names)
    return names


def clear_cache():
    _CACHE.clear()


class ChapterMap:
    """Maps a chapter label to the textbook's name for it; remembers what it could not map."""

    def __init__(self, conn):
        self.conn = conn
        self.unmapped = Counter()
        self._seen = {}

    def name(self, subject, raw):
        raw = (raw or "").strip()
        if not raw:
            return OTHER
        key = (subject, raw)
        if key not in self._seen:
            self._seen[key] = self._map(subject, raw)
        if self._seen[key] == OTHER:
            self.unmapped[raw] += 1
        return canonical(self._seen[key])          # never a bare "14 糖类": always the textbook's way of writing a chapter

    def _map(self, subject, raw):
        chapters = textbook_chapters(self.conn, subject)
        if any(raw == name for name, _, _ in chapters):
            return canonical(raw)                               # already the textbook's own name
        wanted = compact(raw)
        for name, _, _ in chapters:
            if compact(name) == wanted:
                return canonical(name)
        if _NUMBER.search(raw) and not re.match(r"^\s*\d", raw):
            # '第 6 章 核酸': the textbook's own wording with other spacing or digits
            number = number_of(raw)
            same = [c for c in chapters if number is not None and c[1] == number]
            if len(same) == 1:
                return same[0][0]
            return raw
        if not re.match(r"^\s*\d", raw):
            return raw        # not a past-exam label ("6 核酸"): it is already a chapter name (第六章 …, or whatever the question cites)
        number, words = number_of(raw), words_of(raw)
        same_number = [c for c in chapters if number is not None and c[1] == number]
        # the number is the exam's own numbering: it only counts when the words agree a little (or there are no words to compare)
        agreeing = [c for c in same_number if not words or _similarity(words, c[2]) >= 0.3]
        if len(agreeing) == 1:
            return canonical(agreeing[0][0])
        if len(agreeing) > 1:
            best = max(agreeing, key=lambda c: _similarity(words, c[2]))
            return canonical(best[0])
        # otherwise by the words alone: the one chapter that clearly fits best
        if words:
            scored = sorted(((_similarity(words, c[2]), c) for c in chapters), key=lambda x: -x[0])
            if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
                return canonical(scored[0][1][0])
        return OTHER


def _overlap(a, b):
    return sum(1 for ch in set(a) if ch in b)


def mapping_report(conn):
    """Before and after: how many distinct chapter labels of the bank's questions (and how many questions) are only 'number + words'
    (the past exams' own style), how many of those now map to a textbook chapter, and which ones still cannot (shown as 其他)."""
    mapper = ChapterMap(conn)
    labels = questions = still = still_questions = 0
    left = []
    for r in conn.execute(
        "SELECT content->>'subject' AS subject,content->'evidence'->0->>'chapter' AS chapter,count(*) AS n FROM reviewed_questions "
        "WHERE status='published' GROUP BY 1,2"
    ).fetchall():
        raw = (r["chapter"] or "").strip()
        if not raw:
            continue
        digit_style = bool(re.match(r"^\s*\d", raw))
        name = mapper.name(r["subject"], raw)
        if digit_style:
            labels += 1
            questions += r["n"]
        if name == OTHER:
            still += 1
            still_questions += r["n"]
            left.append(f"{r['subject']}:{raw}({r['n']})")
    return dict(number_style_labels=labels, number_style_questions=questions, unmapped_labels=still, unmapped_questions=still_questions, left=sorted(left)[:30])


def unmapped_labels(conn):
    """How many distinct chapter labels of the bank's questions cannot be mapped (for the worker log and the stats page)."""
    mapper = ChapterMap(conn)
    for r in conn.execute(
        "SELECT content->>'subject' AS subject,content->'evidence'->0->>'chapter' AS chapter,count(*) AS n FROM reviewed_questions "
        "WHERE status='published' GROUP BY 1,2"
    ).fetchall():
        mapper.name(r["subject"], r["chapter"])
    return dict(count=len(mapper.unmapped), labels=sorted(mapper.unmapped)[:20])
