"""The mistake page: one card per question the learner has missed (or has still to consolidate), grouped by when it is due.

A card exists for a question when an answer was a mistake (rated Again) or "to consolidate" (a subjective answer at 60%-85%, rated Hard).
Daily papers never contain these questions again: they are reviewed here, on the FSRS schedule. Reviews, intervals and mastery are the
scheduler's (memory.py); this module only reads and arranges them.
"""
import json
import re
from collections import defaultdict
from datetime import timedelta

from app import chapters, memory
from app.learning_stats import SHANGHAI, TYPES, today_cn

GROUPS = (("today", "已到期"), ("tomorrow", "今天稍后及明天"), ("later", "以后"), ("consolidate", "待巩固"), ("mastered", "已掌握"))
MASTERED_AFTER = 3          # successful reviews (Good or Easy) after which a question counts as mastered: the three dots


def plain_answer(raw):
    """What the learner wrote, as short text: the letter, the filled blanks, or the first words of an essay."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        value = {"text": raw}
    if not isinstance(value, dict):
        value = {"text": str(raw or "")}
    blanks = [b for b in value.get("blanks") or [] if str(b).strip()]
    text = "、".join(blanks) if blanks else (value.get("text") or "")
    if not text.strip() and value.get("image_ids"):
        return "（拍照作答）"
    return text.strip()


def stem_for_card(content):
    """The stem as a card shows it: single choice without its blank placeholder, whitespace flattened."""
    stem = content.get("stem") or ""
    if content.get("question_type") == "single_choice":
        stem = re.sub(r"[_＿]{2,}|（\s*）|\(\s*\)", "", stem)
    return re.sub(r"\s+", " ", stem).strip()


def correct_text(content):
    kind = content.get("question_type")
    if kind == "single_choice":
        letter = content.get("answer")
        return letter
    if kind == "fill_blank":
        blanks = content.get("blanks") or []
        if blanks:
            return "、".join((b.get("accepted_answers") or [""])[0] for b in blanks)
        return "、".join((content.get("accepted_answers") or [])[:2])
    return None            # subjective: the reference answer is long; the detail page shows it


def histories(conn, uid, question_ids):
    """{question id: every graded answer to it, newest first} for many questions at once (two queries, whatever the number of cards)."""
    out = {qid: [] for qid in question_ids}
    if not out:
        return out
    ids = list(out)
    for r in conn.execute(
        "SELECT a.question_id,p.id AS paper_id,p.paper_date AS day,a.points,(i.snapshot->>'max_points')::int AS max_points,a.answer,t.submitted_at "
        "FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id "
        "JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id "
        "WHERE p.user_id=%s AND a.question_id=ANY(%s) AND a.points IS NOT NULL ORDER BY t.submitted_at DESC",
        (uid, ids),
    ).fetchall():
        out[r["question_id"]].append(dict(paper_id=r["paper_id"], date=r["day"].isoformat(), points=r["points"], max_points=r["max_points"], answer=r["answer"], kind="paper"))
    for r in conn.execute(
        "SELECT pa.question_id,pa.set_id,pa.points,pa.max_points,pa.answer,pa.answered_at FROM practice_answers pa JOIN practice_sets ps ON ps.id=pa.set_id "
        "WHERE ps.user_id=%s AND pa.question_id=ANY(%s) AND pa.points IS NOT NULL ORDER BY pa.answered_at DESC",
        (uid, ids),
    ).fetchall():
        out[r["question_id"]].append(dict(paper_id=None, set_id=r["set_id"], date=r["answered_at"].astimezone(SHANGHAI).date().isoformat(),
                                          points=r["points"], max_points=r["max_points"], answer=json.dumps(r["answer"]) if not isinstance(r["answer"], str) else r["answer"], kind="practice"))
    for rows in out.values():
        rows.sort(key=lambda h: h["date"], reverse=True)          # (stable: papers stay before practice on the same day)
    return out


def history_of(conn, uid, question_id):
    """Every graded answer to this question, newest first: (date, points, max_points, paper_id or None, answer)."""
    return histories(conn, uid, [question_id])[question_id]


def book(conn, uid, today=None):
    today = today or today_cn(conn)
    tomorrow = today + timedelta(days=1)
    now = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    mapper = chapters.ChapterMap(conn)
    cards = conn.execute(
        "SELECT c.question_id,c.status,c.due,c.version,q.content FROM fsrs_cards c JOIN reviewed_questions q ON q.id=c.question_id "
        "WHERE c.user_id=%s AND NOT EXISTS(SELECT 1 FROM question_reports qr WHERE qr.user_id=c.user_id AND qr.question_id=c.question_id)",
        (uid,),
    ).fetchall()
    good = {
        r["question_id"]: r["n"]
        for r in conn.execute(
            "SELECT question_id,count(*) AS n FROM fsrs_events WHERE user_id=%s AND rating>=3 AND event_key NOT LIKE 'graded-failure:%%' GROUP BY 1",
            (uid,),
        ).fetchall()
    }
    items = []
    every = histories(conn, uid, [c["question_id"] for c in cards])        # all histories in two queries, not two per card
    lines = memory.lines(conn, uid)                                        # (and the two lines once, not once per answer)
    for c in cards:
        content = c["content"]
        history = every[c["question_id"]]
        last = history[0] if history else {}
        wrong_times = sum(1 for h in history if memory.classify(content["question_type"], h["points"], h["max_points"], *lines) in ("mistake", "consolidate"))
        dots = min(MASTERED_AFTER, good.get(c["question_id"], 0))
        due_day = c["due"].astimezone(SHANGHAI).date()
        if c["status"] == "mastered" or dots >= MASTERED_AFTER:
            group = "mastered"
        elif c["status"] == "consolidate" and c["due"] > now:
            group = "consolidate"             # not due yet; one that is due is in the due group (the count on top and the group agree)
        else:
            # the same test as the count on top and the review queue: due by now
            group = "today" if c["due"] <= now else "tomorrow" if due_day <= tomorrow else "later"
        subjective = content["question_type"] in memory.SUBJECTIVE
        items.append(dict(
            question_id=c["question_id"], subject=content["subject"], question_type=content["question_type"],
            chapter=mapper.name(content["subject"], (content.get("evidence") or [{}])[0].get("chapter")) if (content.get("evidence") or [{}])[0].get("chapter") else chapters.OTHER,
            stem=stem_for_card(content)[:160], your_answer=plain_answer(last.get("answer"))[:60] if last else "", correct=correct_text(content),
            points=last.get("points"), max_points=last.get("max_points") or content.get("max_points"), subjective=subjective,
            wrong_times=wrong_times, due=c["due"].isoformat(), due_day=due_day.isoformat(), dots=dots, status=c["status"], group=group,
            last_date=last.get("date"), version=c["version"],
        ))
    order = {key: i for i, (key, _) in enumerate(GROUPS)}
    items.sort(key=lambda x: (order[x["group"]], x["due"], x["question_id"]))
    counts = defaultdict(int)
    subjects = defaultdict(int)
    for it in items:
        counts[it["group"]] += 1
        if it["group"] != "mastered":
            subjects[it["subject"]] += 1
    by_chapter = defaultdict(int)
    by_type = defaultdict(int)
    for it in items:
        by_chapter[(it["subject"], it["chapter"])] += 1
        by_type[it["question_type"]] += 1
    return dict(
        today=today.isoformat(),
        groups=[dict(key=key, label=label, count=counts.get(key, 0)) for key, label in GROUPS],
        items=items,
        total=len(items),
        open=sum(1 for it in items if it["group"] != "mastered"),
        subjects=[dict(value=k, count=v) for k, v in sorted(subjects.items())],
        types=[dict(value=t, count=by_type[t]) for t in TYPES if by_type.get(t)],
        chapters=[dict(subject=k[0], value=k[1], count=v) for k, v in sorted(by_chapter.items(), key=lambda kv: (-kv[1], kv[0]))],
        unmapped=dict(mapper.unmapped),
    )


def detail(conn, uid, question_id):
    """Everything on one mistake: the full question, the learner's answer, the reference answer, the criteria with their scores,
    the textbook source (no internal unit numbers) and the history of answers."""
    row = conn.execute("SELECT content FROM reviewed_questions WHERE id=%s", (question_id,)).fetchone()
    card = conn.execute("SELECT status,due,version FROM fsrs_cards WHERE user_id=%s AND question_id=%s", (uid, question_id)).fetchone()
    if not row or not card:
        raise LookupError("mistake_not_found")
    content = row["content"]
    history = history_of(conn, uid, question_id)
    last = history[0] if history else None
    scores = None
    if last and last["kind"] == "paper":
        fb = conn.execute(
            "SELECT a.feedback FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id WHERE t.paper_id=%s AND a.question_id=%s",
            (last["paper_id"], question_id),
        ).fetchone()
        scores = (fb or {}).get("feedback")
    mapper = chapters.ChapterMap(conn)
    raw_chapter = (content.get("evidence") or [{}])[0].get("chapter")
    return dict(
        question_id=question_id, subject=content["subject"], question_type=content["question_type"],
        chapter=mapper.name(content["subject"], raw_chapter) if raw_chapter else chapters.OTHER,
        stem=content["stem"], options=content.get("options") or {}, max_points=content["max_points"],
        your_answer=plain_answer(last["answer"]) if last else "", points=last["points"] if last else None,
        reference=content.get("answer"), accepted_answers=content.get("accepted_answers") or [],
        rubric=[dict(description=c["description"], points=c["points"]) for c in content.get("rubric") or []],
        scores=scores, evidence=sources_of(content), history=[dict(date=h["date"], points=h["points"], max_points=h["max_points"], kind=h["kind"]) for h in history],
        status=card["status"], due=card["due"].isoformat(), version=card["version"],
    )


def sources_of(content):
    """Textbook basis without internal numbers: chapter name, the title of the book and the excerpt."""
    out = []
    for e in content.get("evidence") or []:
        out.append(dict(chapter=e.get("chapter"), textbook=e.get("textbook"), excerpt=re.sub(r"UNIT:\d+", "", e.get("excerpt") or "").strip()))
    return out
