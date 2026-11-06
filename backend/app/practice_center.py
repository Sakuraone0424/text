"""The practice centre: special-purpose sets of 10, none of which counts toward the daily score or the streak.

Six kinds (one implementation each of the old chapter / weak-point practice is merged in here):
  weak    the weakest chapters, 10 questions        chapter  one chosen chapter        type   one chosen question type
  retry   10 questions from the mistake book        quick    two subjects mixed        year   one year of real past exams
Every question drawn from the bank is NEW (never on a paper or practice set of this learner) and is USED from then on: it never goes
on a daily paper. Questions of the mistake book are the exception (they were used long ago). Only objective questions can be
checked at once; subjective types need AI grading and are shown as not available for practice yet.
"""
import random

from app import auto_generation, chapters, learning_stats as stats
from app.auto_quality import ready_many

SET_SIZE = 10
MIN_SET = 5                # fewer new questions than this: "题库准备中", not clickable
OBJECTIVE = tuple(stats.OBJECTIVE)
MODES = ("weak", "chapter", "type", "retry", "quick", "year")
TYPE_NAMES = {"single_choice": "单选", "fill_blank": "填空", "term": "名词解释", "short_answer": "简答", "essay": "问答"}


def _rows(conn, uid, kinds=OBJECTIVE):
    """Ready, published, new (never used), unreported questions of the given types, with their year when they come from a past exam."""
    rows = conn.execute(
        "SELECT q.id,q.content,pi.year FROM reviewed_questions q JOIN auto_question_states s ON s.question_id=q.id "
        "LEFT JOIN question_origins o ON o.question_id=q.id LEFT JOIN past_exam_items pi ON pi.id=o.past_exam_item_id "
        "WHERE q.status='published' AND s.status='ready' AND q.content->>'question_type'=ANY(%s) AND " + auto_generation.UNUSED_SQL + " "
        "AND NOT EXISTS(SELECT 1 FROM question_reports qr WHERE qr.user_id=%s AND qr.question_id=q.id) ORDER BY q.id",
        (list(kinds), uid, uid, uid),
    ).fetchall()
    verdict = ready_many(conn, [r["id"] for r in rows])
    mapper = chapters.ChapterMap(conn)
    out = []
    for r in rows:
        if not verdict.get(r["id"]):
            continue
        raw = (r["content"].get("evidence") or [{}])[0].get("chapter")
        out.append(dict(id=r["id"], content=r["content"], year=r["year"], subject=r["content"]["subject"], type=r["content"]["question_type"],
                        chapter=mapper.name(r["content"]["subject"], raw) if raw else chapters.OTHER))
    return out


def _weak_pairs(conn, uid, limit=3):
    rows = stats.graded_rows(conn, uid)
    practice = stats.practice_rows(conn, uid)
    for r in list(rows) + list(practice):
        r["chapter"] = chapters.ChapterMap(conn).name(r["subject"], r["chapter"]) if r.get("chapter") else r.get("chapter")
    table = [c for c in stats.chapter_table(rows, practice) if c["answers"] >= stats.MIN_CHAPTER_ANSWERS and c["rate"] < 1]
    return [(c["subject"], c["chapter"], c["rate"]) for c in table[:limit]]


def _retry_rows(conn, uid):
    """Questions of the mistake book that can be checked at once (objective, not mastered)."""
    from app import mistake_book

    return [dict(id=i["question_id"], subject=i["subject"], type=i["question_type"], chapter=i["chapter"])
            for i in mistake_book.book(conn, uid)["items"] if i["group"] != "mastered" and i["question_type"] in OBJECTIVE]


def overview(conn, uid):
    """What each kind of practice can offer right now (counts of new questions), for the practice centre's cards."""
    rows = _rows(conn, uid)
    weak = _weak_pairs(conn, uid)
    weak_count = sum(1 for r in rows if any(r["subject"] == s and r["chapter"] == c for s, c, _ in weak))
    by_chapter, by_type, by_year = {}, {}, {}
    for r in rows:
        by_chapter[(r["subject"], r["chapter"])] = by_chapter.get((r["subject"], r["chapter"]), 0) + 1
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        if r["year"]:
            by_year[r["year"]] = by_year.get(r["year"], 0) + 1
    retry = len(_retry_rows(conn, uid))
    card = lambda mode, available, minimum=MIN_SET: dict(mode=mode, available=available, ready=available >= minimum, size=min(SET_SIZE, available))
    return dict(
        weak=dict(**card("weak", weak_count), chapters=[dict(subject=s, chapter=c, rate=r) for s, c, r in weak]),
        chapter=dict(**card("chapter", len(rows)), chapters=[dict(subject=k[0], chapter=k[1], available=n) for k, n in sorted(by_chapter.items(), key=lambda kv: (-kv[1], kv[0])) if n >= 1]),
        type=dict(**card("type", len(rows)), types=[dict(value=t, name=TYPE_NAMES[t], available=by_type.get(t, 0), supported=t in OBJECTIVE) for t in TYPE_NAMES]),
        retry=card("retry", retry, 1),
        quick=card("quick", len(rows)),
        year=dict(**card("year", sum(by_year.values())), years=[dict(year=y, available=n) for y, n in sorted(by_year.items(), reverse=True)]),
        set_size=SET_SIZE,
    )


def pick(conn, uid, mode, subject=None, chapter=None, question_type=None, year=None, seed=None, size=None):
    """The questions of a new set: [(id, content)], or raises ValueError(reason)."""
    if mode not in MODES:
        raise ValueError("unknown_practice_mode")
    size = max(1, min(int(size or SET_SIZE), SET_SIZE * 2))
    rng = random.Random(seed if seed is not None else None)
    if mode == "retry":
        from_book = _retry_rows(conn, uid)
        if not from_book:
            raise ValueError("no_mistakes_to_redo")
        rng.shuffle(from_book)
        picked = from_book[:size]
        contents = {r["id"]: r["content"] for r in conn.execute("SELECT id,content FROM reviewed_questions WHERE id=ANY(%s)", ([p["id"] for p in picked],)).fetchall()}
        return [(p["id"], contents[p["id"]]) for p in picked]
    rows = _rows(conn, uid)
    if mode == "weak":
        weak = _weak_pairs(conn, uid)
        if not weak:
            raise ValueError("no_weak_chapters_yet")
        wanted = {(s, c) for s, c, _ in weak}
        rows = [r for r in rows if (r["subject"], r["chapter"]) in wanted]
    elif mode == "chapter":
        if subject not in stats.SUBJECTS or not chapter:
            raise ValueError("subject_and_chapter_required")
        rows = [r for r in rows if r["subject"] == subject and r["chapter"] == chapter]
    elif mode == "type":
        if question_type not in TYPE_NAMES:
            raise ValueError("question_type_required")
        if question_type not in OBJECTIVE:
            raise ValueError("subjective_practice_needs_ai_grading")
        rows = [r for r in rows if r["type"] == question_type]
    elif mode == "year":
        if not year:
            raise ValueError("year_required")
        rows = [r for r in rows if r["year"] == year]
    if len(rows) < MIN_SET:
        raise ValueError("no_practice_questions_in_these_chapters")
    rng.shuffle(rows)
    return [(r["id"], r["content"]) for r in rows[:size]]


def start(conn, uid, mode, subject=None, chapter=None, question_type=None, year=None, seconds=None, size=None):
    from app import practice

    picked = pick(conn, uid, mode, subject, chapter, question_type, year, size=size)
    detail = question_type or (str(year) if year else None)
    set_id = conn.execute(
        "INSERT INTO practice_sets(user_id,mode,subject,chapter,question_ids,detail,time_limit_seconds) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (uid, mode, subject, chapter, [p[0] for p in picked], detail, seconds),
    ).fetchone()["id"]
    return dict(set_id=set_id, mode=mode, counts_toward_daily_paper=False, time_limit_seconds=seconds,
                questions=[practice.public(content, qid) for qid, content in picked])
