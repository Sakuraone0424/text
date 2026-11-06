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
        return self._seen[key]

    def _map(self, subject, raw):
        chapters = textbook_chapters(self.conn, subject)
        if any(raw == name for name, _, _ in chapters):
            return raw                                          # already the textbook's own name
        wanted = compact(raw)
        for name, _, _ in chapters:
            if compact(name) == wanted:
                return name
        if not re.match(r"^\s*\d", raw):
            return raw        # not a past-exam label ("6 核酸"): it is already a chapter name (第六章 …, or whatever the question cites)
        number, words = number_of(raw), words_of(raw)
        same_number = [c for c in chapters if number is not None and c[1] == number]
        if len(same_number) == 1:
            return same_number[0][0]
        if len(same_number) > 1 and words:
            best = max(same_number, key=lambda c: _overlap(words, c[2]))
            if _overlap(words, best[2]):
                return best[0]
        return OTHER


def _overlap(a, b):
    return sum(1 for ch in set(a) if ch in b)


def unmapped_labels(conn):
    """How many distinct chapter labels of the bank's questions cannot be mapped (for the worker log and the stats page)."""
    mapper = ChapterMap(conn)
    for r in conn.execute(
        "SELECT content->>'subject' AS subject,content->'evidence'->0->>'chapter' AS chapter,count(*) AS n FROM reviewed_questions "
        "WHERE status='published' GROUP BY 1,2"
    ).fetchall():
        mapper.name(r["subject"], r["chapter"])
    return dict(count=len(mapper.unmapped), labels=sorted(mapper.unmapped)[:20])
