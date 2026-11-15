"""The practice centre: special-purpose sets of up to 10, none of which counts toward the daily score or the streak.

Six kinds (one implementation each of the old chapter / weak-point practice is merged in here):
  weak    the weakest chapters                      chapter  one chosen chapter        type   one chosen question type (all five)
  retry   questions from the mistake book           quick    two subjects mixed        year   one year of real past exams

WHERE THE QUESTIONS COME FROM (in this order; a practice set never takes a question the daily paper needs):
  1. questions the learner has already had, on an earlier paper or practice set (they cost the daily stock nothing);
  2. new questions that are surplus: more than the two-day reserve of their subject x type cell (the cell is counted on its own,
     e.g. a spare 名词解释 while 单选 is short);
  3. the two-day reserve of every cell is never given to practice.
"可练 N 题" is worked out from exactly these sources, for each kind with its own filter. A question that comes from 2 is USED from then
on (it never goes on a daily paper). Objective questions are checked at once; subjective ones (名词解释 / 简答 / 问答) are graded by the AI
after they are handed in.
"""
import random

from app import auto_generation, chapters, cost_control, learning_stats as stats
from app.auto_quality import ready_many
from app.training_spec_v5 import COUNTS

SET_SIZE = 10
MIN_SET = 1                # a set may be shorter than 10 when that is all there is
OBJECTIVE = tuple(stats.OBJECTIVE)
MODES = ("weak", "chapter", "type", "retry", "quick", "year")
TYPE_NAMES = {"single_choice": "单选", "fill_blank": "填空", "term": "名词解释", "short_answer": "简答", "essay": "问答"}
SUBJECT_NAMES = {"biochemistry": "生化", "cell_biology": "细胞"}


def _decorate(conn, rows):
    """Add year and the textbook chapter to question rows [(id, content)]."""
    years = {r["question_id"]: r["year"] for r in conn.execute(
        "SELECT o.question_id,pi.year FROM question_origins o JOIN past_exam_items pi ON pi.id=o.past_exam_item_id WHERE o.question_id=ANY(%s)",
        ([r["id"] for r in rows],)).fetchall()} if rows else {}
    mapper = chapters.ChapterMap(conn)
    out = []
    for r in rows:
        raw = (r["content"].get("evidence") or [{}])[0].get("chapter")
        out.append(dict(id=r["id"], content=r["content"], year=years.get(r["id"]), subject=r["content"]["subject"], type=r["content"]["question_type"],
                        chapter=mapper.name(r["content"]["subject"], raw) if raw else chapters.OTHER))
    return out


def reserve_of(subject, kind):
    """The two-day reserve of one cell: what the daily paper keeps for itself."""
    return cost_control.reserve_target(COUNTS.get(kind, 0), auto_generation.STARTUP_RESERVE_DAYS)       # single choice: no daily demand, so no reserve


def sources(conn, uid, kinds=None):
    """{'used': [rows], 'new': {(subject, type): [rows]}, 'surplus': {(subject, type): n}} for the given question types (default all five).
    `new` are the cell's stock questions (new to the learner, verified); `surplus` is how many of them may be taken without touching the reserve."""
    kinds = kinds or tuple(TYPE_NAMES)
    reported = "NOT EXISTS(SELECT 1 FROM question_reports qr WHERE qr.user_id=%s AND qr.question_id=q.id)"
    used_raw = conn.execute(
        "SELECT q.id,q.content FROM reviewed_questions q JOIN auto_question_states s ON s.question_id=q.id "
        "WHERE q.status='published' AND s.status='ready' AND q.content->>'question_type'=ANY(%s) AND " + reported + " AND ("
        "EXISTS(SELECT 1 FROM paper_items_v2 i JOIN papers_v2 p ON p.id=i.paper_id JOIN training_sessions ts ON ts.paper_id=p.id AND ts.submitted_at IS NOT NULL WHERE p.user_id=%s AND i.question_id=q.id) "     # (a paper not handed in yet is not "had": its questions stay hidden)
        "OR EXISTS(SELECT 1 FROM practice_sets ps WHERE ps.user_id=%s AND q.id=ANY(ps.question_ids))) ORDER BY q.id",
        (list(kinds), uid, uid, uid),
    ).fetchall()
    verdict = ready_many(conn, [r["id"] for r in used_raw])
    used = _decorate(conn, [r for r in used_raw if verdict.get(r["id"])])
    fresh, surplus = {}, {}
    reported_ids = {r["question_id"] for r in conn.execute("SELECT question_id FROM question_reports WHERE user_id=%s", (uid,)).fetchall()}
    for subject in stats.SUBJECTS:
        for kind in kinds:
            raw = [r for r in auto_generation.eligible_reserve_rows(conn, uid, subject, kind) if r["id"] not in reported_ids]
            fresh[(subject, kind)] = _decorate(conn, raw)
            surplus[(subject, kind)] = max(0, len(raw) - reserve_of(subject, kind))
    return dict(used=used, new=fresh, surplus=surplus)


def count_available(src, keep=lambda r: True):
    """Used questions that pass the filter, plus the new ones that pass it, never more from a cell than its surplus."""
    used = sum(1 for r in src["used"] if keep(r))
    fresh = sum(min(src["surplus"][cell], sum(1 for r in rows if keep(r))) for cell, rows in src["new"].items())
    return used + fresh, used, fresh


def take(src, size, rng, keep=lambda r: True):
    """[(row, 'used'|'new')]: used questions first, then new ones from the surplus of their cells."""
    used = [r for r in src["used"] if keep(r)]
    rng.shuffle(used)
    chosen = [(r, "used") for r in used[:size]]
    if len(chosen) < size:
        pool = []
        for cell, rows in src["new"].items():
            ok = [r for r in rows if keep(r)]
            rng.shuffle(ok)
            pool.append(ok[:src["surplus"][cell]])
        merged = [r for group in pool for r in group]
        rng.shuffle(merged)
        # never more than a cell's surplus: the per-cell lists above are already cut to it
        chosen += [(r, "new") for r in merged[:size - len(chosen)]]
    return chosen


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


def _reason(mode, available, extra=None):
    """Why a kind cannot start (Chinese, specific), or None."""
    if available > 0:
        return None
    return {
        "weak": "这些薄弱章节暂时没有可练的题，还差 10 道",
        "chapter": "各章节暂时没有可练的题，还差 10 道",
        "type": "这个题型暂时没有可练的题，还差 10 道",
        "quick": "两科都没有可练的客观题，还差 10 道",
        "year": "这一年的真题暂时没有可练的题，还差 10 道",
        "retry": "还没有可重做的错题",
    }[mode]


def overview(conn, uid):
    """What each kind of practice can offer right now, worked out from its own sources (see the top of this module)."""
    src = sources(conn, uid)
    objective = lambda r: r["type"] in OBJECTIVE
    weak = _weak_pairs(conn, uid)
    wanted = {(s, c) for s, c, _ in weak}
    weak_total, weak_used, weak_new = count_available(src, lambda r: objective(r) and (r["subject"], r["chapter"]) in wanted)
    chapters_seen = {}
    for r in src["used"] + [x for rows in src["new"].values() for x in rows]:
        if objective(r):
            chapters_seen[(r["subject"], r["chapter"])] = None
    by_chapter = []
    for (subject, chapter) in chapters_seen:
        n = count_available(src, lambda r, s=subject, c=chapter: objective(r) and r["subject"] == s and r["chapter"] == c)[0]
        if n:
            by_chapter.append(dict(subject=subject, chapter=chapter, available=n))
    # grouped by subject, each in the textbook's chapter order (not by how many questions there are)
    by_chapter.sort(key=lambda x: (stats.SUBJECTS.index(x["subject"]), chapters.number_of(x["chapter"]) or 999, x["chapter"]))
    types = []
    for t in TYPE_NAMES:
        n, used_n, new_n = count_available(src, lambda r, t=t: r["type"] == t)
        types.append(dict(value=t, name=TYPE_NAMES[t], available=n, used=used_n, new=new_n, supported=True, graded_by_ai=t not in OBJECTIVE))
    years = {}
    for r in src["used"] + [x for rows in src["new"].values() for x in rows]:
        if r["year"] and objective(r):
            years[r["year"]] = None
    year_rows = [dict(year=y, available=count_available(src, lambda r, y=y: objective(r) and r["year"] == y)[0]) for y in sorted(years, reverse=True)]
    quick_total = count_available(src, objective)[0]
    chapter_total = sum(c["available"] for c in by_chapter)
    retry = len(_retry_rows(conn, uid))
    year_total = sum(y["available"] for y in year_rows)
    type_total = sum(t["available"] for t in types)

    def card(mode, available, **extra):
        return dict(mode=mode, available=available, ready=available >= MIN_SET, size=min(SET_SIZE, available), reason=_reason(mode, available),
                    short_by=max(0, SET_SIZE - available) if 0 < available < SET_SIZE else 0, **extra)

    weak_card = card("weak", weak_total, chapters=[dict(subject=s, chapter=c, rate=r) for s, c, r in weak], used=weak_used, new=weak_new)
    if not weak:
        weak_card.update(reason="做完一套卷子后才能知道薄弱章节", ready=False, available=0, size=0, short_by=0)
    return dict(
        weak=weak_card,
        chapter=card("chapter", chapter_total, chapters=by_chapter),
        type=card("type", type_total, types=types),
        retry=card("retry", retry),
        quick=card("quick", quick_total),
        year=card("year", year_total, years=year_rows),
        set_size=SET_SIZE,
        reserve=dict(kept_for_daily_paper=True),
    )


def pick(conn, uid, mode, subject=None, chapter=None, question_type=None, year=None, seed=None, size=None):
    """The questions of a new set: [(id, content)], or raises ValueError(reason). Used questions first, then surplus new ones."""
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
    objective = lambda r: r["type"] in OBJECTIVE
    if mode == "weak":
        weak = _weak_pairs(conn, uid)
        if not weak:
            raise ValueError("no_weak_chapters_yet")
        wanted = {(s, c) for s, c, _ in weak}
        keep = lambda r: objective(r) and (r["subject"], r["chapter"]) in wanted
    elif mode == "chapter":
        if subject not in stats.SUBJECTS or not chapter:
            raise ValueError("subject_and_chapter_required")
        keep = lambda r: objective(r) and r["subject"] == subject and r["chapter"] == chapter
    elif mode == "type":
        if question_type not in TYPE_NAMES:
            raise ValueError("question_type_required")
        keep = lambda r: r["type"] == question_type
    elif mode == "year":
        if not year:
            raise ValueError("year_required")
        keep = lambda r: objective(r) and r["year"] == year
    else:
        keep = objective
    src = sources(conn, uid)
    chosen = take(src, size, rng, keep)
    if len(chosen) < MIN_SET:
        raise ValueError("no_practice_questions_in_these_chapters")
    return [(r["id"], r["content"]) for r, _ in chosen]


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
