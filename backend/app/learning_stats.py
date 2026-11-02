"""Read-only learning statistics per user: numbers only, computed on the server.

Papers are never mixed on one scale: every rate is points / that paper's own maximum, and score
trends are shown per paper in percent. Questions reported by the learner are excluded everywhere.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
EXAM_DATE = date(2026, 12, 19)
SUBJECTS = ("biochemistry", "cell_biology")
TYPES = ("single_choice", "fill_blank", "term", "short_answer", "essay")
OBJECTIVE = ("single_choice", "fill_blank")
MIN_CHAPTER_ANSWERS = 3  # fewer graded answers than this say nothing about a chapter


def today_cn(conn):
    return conn.execute("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS d").fetchone()["d"]


def graded_rows(conn, uid):
    """Every answer of a submitted paper, minus reported questions, oldest first."""
    return conn.execute(
        "SELECT p.id AS paper_id,p.paper_date,t.id AS attempt_id,t.submitted_at,"
        "p.blueprint->>'specification' AS spec,"
        "i.snapshot->>'subject' AS subject,i.snapshot->>'question_type' AS qtype,"
        "(i.snapshot->>'max_points')::int AS max_points,i.snapshot->'evidence'->0->>'chapter' AS chapter,"
        "a.question_id,a.points,a.answer,tm.seconds "
        "FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id "
        "JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id "
        "LEFT JOIN answer_timings tm ON tm.attempt_id=a.attempt_id AND tm.question_id=a.question_id "
        "WHERE p.user_id=%s AND NOT EXISTS(SELECT 1 FROM question_reports qr WHERE qr.paper_id=p.id AND qr.question_id=a.question_id) "
        "ORDER BY t.submitted_at,p.id,i.position",
        (uid,),
    ).fetchall()


def practice_rows(conn, uid):
    return conn.execute(
        "SELECT pa.question_id,pa.points,pa.max_points,pa.seconds,pa.answered_at,"
        "q.content->>'subject' AS subject,q.content->>'question_type' AS qtype,q.content->'evidence'->0->>'chapter' AS chapter "
        "FROM practice_answers pa JOIN practice_sets ps ON ps.id=pa.set_id JOIN reviewed_questions q ON q.id=pa.question_id "
        "WHERE ps.user_id=%s ORDER BY pa.answered_at",
        (uid,),
    ).fetchall()


def papers(rows):
    """Fully graded papers (no pending answer), each with totals per subject and type."""
    by = {}
    for r in rows:
        p = by.setdefault(r["paper_id"], dict(
            paper_id=r["paper_id"], date=r["paper_date"], submitted_at=r["submitted_at"], spec=r["spec"],
            pending=0, points=0, max=0, subjects=defaultdict(lambda: [0, 0]), types=defaultdict(lambda: [0, 0, 0, 0]),
        ))
        if r["points"] is None:
            p["pending"] += 1
            continue
        p["points"] += r["points"]
        p["max"] += r["max_points"]
        p["subjects"][r["subject"]][0] += r["points"]
        p["subjects"][r["subject"]][1] += r["max_points"]
        t = p["types"][r["qtype"]]
        t[0] += r["points"]
        t[1] += r["max_points"]
        t[2] += 1
        t[3] += r["seconds"] or 0
    return sorted((p for p in by.values() if not p["pending"] and p["max"]), key=lambda p: (p["submitted_at"], p["paper_id"]))


def rate(points, maximum):
    return round(points / maximum, 4) if maximum else None


def streaks(days, today):
    """(current, longest) run of consecutive days with any practice; today may still be empty."""
    days = sorted(set(days))
    longest = run = 0
    previous = None
    for d in days:
        run = run + 1 if previous and (d - previous).days == 1 else 1
        longest = max(longest, run)
        previous = d
    current = 0
    cursor = today if today in set(days) else today - timedelta(days=1)
    have = set(days)
    while cursor in have:
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


def activity_days(rows, practice):
    days = [r["submitted_at"].astimezone(SHANGHAI).date() for r in rows]
    days += [r["answered_at"].astimezone(SHANGHAI).date() for r in practice]
    return days


def chapter_table(rows, practice):
    """Accuracy per (subject, chapter) over papers AND practice answers; lowest first."""
    acc = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        if r["points"] is not None and r["chapter"]:
            a = acc[(r["subject"], r["chapter"])]
            a[0] += r["points"]; a[1] += r["max_points"]; a[2] += 1
    for r in practice:
        if r["chapter"]:
            a = acc[(r["subject"], r["chapter"])]
            a[0] += r["points"]; a[1] += r["max_points"]; a[2] += 1
    out = [dict(subject=s, chapter=c, rate=rate(v[0], v[1]), answers=v[2]) for (s, c), v in acc.items() if v[1]]
    return sorted(out, key=lambda x: (x["rate"], -x["answers"], x["chapter"]))


def due_reviews(conn, uid, today):
    due_now = conn.execute("SELECT count(*) AS n FROM fsrs_cards WHERE user_id=%s AND due<=clock_timestamp()", (uid,)).fetchone()["n"]
    days = {
        r["d"]: r["n"]
        for r in conn.execute(
            "SELECT (due AT TIME ZONE 'Asia/Shanghai')::date AS d,count(*) AS n FROM fsrs_cards WHERE user_id=%s GROUP BY 1",
            (uid,),
        ).fetchall()
    }
    # Overdue cards count for today; the next 7 days follow.
    overdue = sum(n for d, n in days.items() if d < today)
    upcoming = [dict(day=(today + timedelta(days=k)).isoformat(),
                     count=days.get(today + timedelta(days=k), 0) + (overdue if k == 0 else 0)) for k in range(7)]
    mastered = conn.execute(
        "SELECT count(*) AS n FROM (SELECT question_id,array_agg(rating ORDER BY reviewed_at DESC) AS r FROM fsrs_events "
        "WHERE user_id=%s GROUP BY question_id) x WHERE array_length(r,1)>=2 AND r[1]>=3 AND r[2]>=3",
        (uid,),
    ).fetchone()["n"]
    return dict(due_now=due_now, next_7_days=upcoming, mastered=mastered)


def tomorrow_ready(conn, uid):
    """Is there a whole paper's worth of approved questions in the bank, and when will there be?"""
    from app import progress

    buckets = progress.buckets(conn, uid)
    ready = sum(min(b["stocked"], b["required"]) for b in buckets)
    needed = sum(b["required"] for b in buckets)
    paper = progress.next_paper(conn, buckets, conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"])
    return dict(ready=ready, needed=needed, missing=needed - ready, complete=ready >= needed,
                eta_at=paper["eta_at"], eta_hours=paper["eta_hours"], eta_state=paper["eta_state"], per_hour=paper["per_hour"], budget=paper["budget"])


def overview(conn, uid):
    """Everything the home page shows."""
    today = today_cn(conn)
    rows = graded_rows(conn, uid)
    practice = practice_rows(conn, uid)
    done = papers(rows)
    current, longest = streaks(activity_days(rows, practice), today)
    paper = conn.execute(
        "SELECT p.id,p.blueprint->>'specification' AS spec,s.started_at,s.submitted_at FROM papers_v2 p "
        "LEFT JOIN training_sessions s ON s.paper_id=p.id WHERE p.user_id=%s AND p.paper_date=%s",
        (uid, today),
    ).fetchone()
    today_paper = dict(state="none")
    if paper:
        state = "submitted" if paper["submitted_at"] else "in_progress" if paper["started_at"] else "prepared"
        today_paper = dict(state=state, paper_id=paper["id"], spec=paper["spec"])
        finished = next((p for p in done if p["paper_id"] == paper["id"]), None)
        if finished:
            today_paper.update(points=finished["points"], max=finished["max"])
    last7, before7 = done[-7:], done[-14:-7]
    same = [p for p in last7 if done and p["max"] == done[-1]["max"]]
    objective = [0, 0]
    subjective = [0, 0]
    window = {p["paper_id"] for p in last7}
    for r in rows:
        if r["paper_id"] in window and r["points"] is not None:
            bucket = objective if r["qtype"] in OBJECTIVE else subjective
            bucket[0] += r["points"]; bucket[1] += r["max_points"]
    avg = lambda ps: round(sum(p["points"] / p["max"] for p in ps) / len(ps), 4) if ps else None
    latest_max = done[-1]["max"] if done else None
    this_avg, prev_avg = avg(last7), avg(before7)
    return dict(
        today=today.isoformat(), exam_date=EXAM_DATE.isoformat(), days_left=max(0, (EXAM_DATE - today).days),
        streak=dict(current=current, longest=longest),
        today_paper=today_paper,
        last7=dict(
            papers=len(last7), avg_rate=this_avg, avg_points=round(this_avg * latest_max, 1) if this_avg is not None else None,
            max_points=latest_max,
            delta_points=round((this_avg - prev_avg) * latest_max, 1) if this_avg is not None and prev_avg is not None else None,
        ),
        objective_rate=rate(*objective), subjective_rate=rate(*subjective),
        weak_chapters=[c for c in chapter_table(rows, practice) if c["answers"] >= MIN_CHAPTER_ANSWERS][:3],
        reviews=due_reviews(conn, uid, today),
        tomorrow=tomorrow_ready(conn, uid),
        has_data=bool(done),
    )


def profile(conn, uid):
    """The 'my learning data' page."""
    today = today_cn(conn)
    rows = graded_rows(conn, uid)
    practice = practice_rows(conn, uid)
    done = papers(rows)
    submitted = conn.execute(
        "SELECT count(*) AS n,COALESCE(sum(EXTRACT(EPOCH FROM (t.submitted_at-s.started_at))),0) AS seconds "
        "FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id JOIN training_sessions s ON s.paper_id=p.id WHERE p.user_id=%s",
        (uid,),
    ).fetchone()
    answered = sum(1 for r in rows if (r["answer"] or "").strip() and r["points"] is not None) + len(practice)
    current, longest = streaks(activity_days(rows, practice), today)
    trend = [
        dict(paper_id=p["paper_id"], date=p["date"].isoformat(), spec=p["spec"], points=p["points"], max=p["max"], rate=rate(p["points"], p["max"]),
             subjects={s: dict(points=v[0], max=v[1], rate=rate(*v)) for s, v in p["subjects"].items()})
        for p in done[-30:]
    ]
    types = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["points"] is not None:
            types[(r["subject"], r["qtype"])][0] += r["points"]
            types[(r["subject"], r["qtype"])][1] += r["max_points"]
    type_rates = [dict(subject=s, question_type=t, rate=rate(*types[(s, t)]), max=types[(s, t)][1]) for s in SUBJECTS for t in TYPES if types[(s, t)][1]]
    # calendar: last 8 weeks, one cell per day (questions done and score rate)
    start = today - timedelta(days=55)
    cells = defaultdict(lambda: [0, 0, 0])
    for r in rows:
        d = r["submitted_at"].astimezone(SHANGHAI).date()
        if d >= start and r["points"] is not None:
            c = cells[d]; c[0] += 1; c[1] += r["points"]; c[2] += r["max_points"]
    for r in practice:
        d = r["answered_at"].astimezone(SHANGHAI).date()
        if d >= start:
            c = cells[d]; c[0] += 1; c[1] += r["points"]; c[2] += r["max_points"]
    calendar = [dict(day=(start + timedelta(days=k)).isoformat(), answered=cells[start + timedelta(days=k)][0],
                     rate=rate(cells[start + timedelta(days=k)][1], cells[start + timedelta(days=k)][2])) for k in range(56)]
    return dict(
        today=today.isoformat(),
        summary=dict(papers=submitted["n"], answered=answered, total_seconds=int(submitted["seconds"]), streak_current=current, streak_longest=longest),
        trend=trend,
        type_rates=type_rates,
        chapters=chapter_table(rows, practice),
        calendar=calendar,
        timing=timing(conn, uid, rows),
        reviews=due_reviews(conn, uid, today),
    )


def timing(conn, uid, rows):
    """Actual time per paper, and average seconds per question by type (needs recorded dwell time)."""
    durations = [
        dict(paper_id=r["paper_id"], date=r["paper_date"].isoformat(), seconds=int(r["seconds"]))
        for r in conn.execute(
            "SELECT p.id AS paper_id,p.paper_date,EXTRACT(EPOCH FROM (t.submitted_at-s.started_at)) AS seconds "
            "FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id JOIN training_sessions s ON s.paper_id=p.id "
            "WHERE p.user_id=%s ORDER BY p.paper_date DESC,p.id DESC LIMIT 14",
            (uid,),
        ).fetchall()
    ][::-1]
    per = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["seconds"]:
            per[r["qtype"]][0] += r["seconds"]
            per[r["qtype"]][1] += 1
    from app.training_spec_v4 import POINTS, TOTAL_POINTS

    # The hour is split by points: a question's suggested time is 3600 s x its points / the paper's total points.
    by_type = [dict(question_type=t, avg_seconds=round(per[t][0] / per[t][1]), questions=per[t][1],
                    suggested_seconds=round(3600 * POINTS[t] / TOTAL_POINTS)) for t in TYPES if per[t][1]]
    # Types where the learner spends clearly more than their share of the hour (share of points x 3600 s).
    return dict(papers=durations, by_type=by_type, recorded_questions=sum(v[1] for v in per.values()))


def overtime_types(conn, uid, paper_id):
    """For the result page: question types that took far longer than their share of the hour."""
    rows = conn.execute(
        "SELECT i.snapshot->>'question_type' AS qtype,(i.snapshot->>'max_points')::int AS max_points,tm.seconds "
        "FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id JOIN paper_items_v2 i ON i.paper_id=p.id "
        "LEFT JOIN answer_timings tm ON tm.attempt_id=t.id AND tm.question_id=i.question_id "
        "WHERE p.user_id=%s AND p.id=%s",
        (uid, paper_id),
    ).fetchall()
    total_points = sum(r["max_points"] for r in rows)
    if not rows or not total_points:
        return dict(recorded=False, types=[])
    spent = defaultdict(int)
    share = defaultdict(int)
    for r in rows:
        spent[r["qtype"]] += r["seconds"] or 0
        share[r["qtype"]] += r["max_points"]
    recorded = sum(spent.values())
    if not recorded:
        return dict(recorded=False, types=[])
    out = []
    for t in TYPES:
        if t not in share:
            continue
        budget = round(3600 * share[t] / total_points)
        out.append(dict(question_type=t, seconds=spent[t], budget_seconds=budget, over=spent[t] > budget * 1.25 and spent[t] - budget >= 60))
    return dict(recorded=True, types=out)


def mistakes(conn, uid, subject=None, question_type=None, chapter=None, limit=50, offset=0):
    """Wrong or partly wrong graded answers (reported ones excluded), newest first, with filter counts."""
    wrong = [r for r in graded_rows(conn, uid) if r["points"] is not None and r["points"] < r["max_points"]]
    # one entry per question, with how many times it was missed (papers and practice together)
    by_question = {}
    for r in wrong:
        entry = by_question.setdefault(r["question_id"], dict(r, wrong_times=0))
        entry["wrong_times"] += 1
        if (r["submitted_at"], r["paper_id"]) >= (entry["submitted_at"], entry["paper_id"]):
            entry.update({k: r[k] for k in ("submitted_at", "paper_id", "paper_date", "points", "max_points")})
    for r in practice_rows(conn, uid):
        if r["points"] < r["max_points"] and r["question_id"] in by_question:
            by_question[r["question_id"]]["wrong_times"] += 1
        elif r["points"] < r["max_points"]:
            by_question[r["question_id"]] = dict(
                paper_id=None, paper_date=r["answered_at"].astimezone(SHANGHAI).date(), submitted_at=r["answered_at"], subject=r["subject"],
                qtype=r["qtype"], chapter=r["chapter"], points=r["points"], max_points=r["max_points"], question_id=r["question_id"], wrong_times=1)
    rows = list(by_question.values())
    facets = dict(subject=defaultdict(int), question_type=defaultdict(int), chapter=defaultdict(int))
    for r in rows:
        facets["subject"][r["subject"]] += 1
        facets["question_type"][r["qtype"]] += 1
        facets["chapter"][(r["subject"], r["chapter"] or "")] += 1
    chosen = [r for r in rows if (not subject or r["subject"] == subject) and (not question_type or r["qtype"] == question_type)
              and (not chapter or (r["chapter"] or "") == chapter)]
    chosen.sort(key=lambda r: (r["submitted_at"], r["question_id"]), reverse=True)
    page = chosen[offset: offset + limit]
    stems = {
        r["id"]: r["content"]
        for r in conn.execute("SELECT id,content FROM reviewed_questions WHERE id=ANY(%s)", ([r["question_id"] for r in page],)).fetchall()
    }
    return dict(
        total=len(chosen),
        items=[dict(question_id=r["question_id"], paper_id=r["paper_id"], date=r["paper_date"].isoformat(), subject=r["subject"],
                    question_type=r["qtype"], chapter=r["chapter"], points=r["points"], max_points=r["max_points"],
                    wrong_times=r["wrong_times"], stem=(stems.get(r["question_id"], {}).get("stem") or "")[:200]) for r in page],
        facets=dict(
            subject=[dict(value=k, count=v) for k, v in sorted(facets["subject"].items())],
            question_type=[dict(value=k, count=v) for k, v in sorted(facets["question_type"].items(), key=lambda kv: TYPES.index(kv[0]) if kv[0] in TYPES else 9)],
            chapter=[dict(subject=k[0], value=k[1], count=v) for k, v in sorted(facets["chapter"].items(), key=lambda kv: (-kv[1], kv[0])) if k[1]],
        ),
    )
