"""Whole-paper review: a submitted paper (or practice set) re-opened question by question.

`summary` is light (header numbers and one line per question, enough to draw the answer card and the filters); `detail` loads one
question in full when it is opened, so a 60-question paper stays quick on a phone. Papers of the old 28-question specification are
read the same way and stay read-only (nothing here writes).
"""
import json
from collections import defaultdict

from app import chapters, grading_activity, grading_recovery, memory, quiz_v2
from app.learning_stats import SHANGHAI, TYPES
from app.mistake_book import plain_answer, sources_of

SPEC_NAMES = {
    "xiaohan-60q-300pt-v4": "60 题 · 300 分", "xiaohan-60q-150pt-v2": "60 题 · 150 分", "xiaohan-150-60-v1": "28 题 · 150 分（旧卷，只读）",
}


def _paper_rows(conn, uid, paper_id):
    paper = conn.execute("SELECT * FROM papers_v2 WHERE id=%s AND user_id=%s", (paper_id, uid)).fetchone()
    if not paper:
        raise LookupError("paper_not_found")
    attempt = conn.execute("SELECT * FROM attempts_v2 WHERE paper_id=%s", (paper_id,)).fetchone()
    if not attempt:
        raise LookupError("paper_not_submitted")
    rows = conn.execute(
        "SELECT i.position,i.question_id,i.snapshot,a.answer,a.points,a.feedback FROM paper_items_v2 i "
        "LEFT JOIN answers_v2 a ON a.question_id=i.question_id AND a.attempt_id=%s WHERE i.paper_id=%s ORDER BY i.position",
        (attempt["id"], paper_id),
    ).fetchall()
    return paper, attempt, rows


def status_of(snapshot, answer, points, reported):
    """right / partial / wrong / blank / pending / reported: the colour of a question on the answer card."""
    if reported:
        return "reported"
    if points is None:
        return "pending"
    if not plain_answer(answer):
        return "blank"
    if points >= snapshot["max_points"]:
        return "right"
    return "partial" if points > 0 else "wrong"


def summary(conn, uid, paper_id):
    paper, attempt, rows = _paper_rows(conn, uid, paper_id)
    reported = {r["question_id"] for r in conn.execute("SELECT question_id FROM question_reports WHERE paper_id=%s", (paper_id,)).fetchall()}
    session = conn.execute("SELECT started_at,submitted_at FROM training_sessions WHERE paper_id=%s", (paper_id,)).fetchone()
    mapper = chapters.ChapterMap(conn)
    lines = memory.lines(conn, uid)
    items, by_subject, by_type = [], defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0])
    pending = 0
    for r in rows:
        snap = r["snapshot"]
        is_reported = r["question_id"] in reported
        state = status_of(snap, r["answer"], r["points"], is_reported)
        pending += state == "pending"
        raw = (snap.get("evidence") or [{}])[0].get("chapter")
        kind = memory.classify(snap["question_type"], r["points"], snap["max_points"], *lines) if r["points"] is not None and not is_reported else None
        items.append(dict(
            position=r["position"], question_id=r["question_id"], subject=snap["subject"], question_type=snap["question_type"],
            chapter=mapper.name(snap["subject"], raw) if raw else chapters.OTHER, points=r["points"], max_points=snap["max_points"],
            status=state, flag=kind if kind in ("mistake", "consolidate") else None,
            stem=" ".join((snap.get("stem") or "").split())[:60],
        ))
        if not is_reported and r["points"] is not None:
            by_subject[snap["subject"]][0] += r["points"]
            by_type[(snap["subject"], snap["question_type"])][0] += r["points"]
            by_type[(snap["subject"], snap["question_type"])][1] += snap["max_points"]
        if not is_reported:
            by_subject[snap["subject"]][1] += snap["max_points"]
            by_subject[snap["subject"]][2] += 1 if r["points"] is None else 0
    total_points = sum(v[0] for v in by_subject.values())
    total_max = sum(v[1] for v in by_subject.values())
    obj_points = sum(i["points"] for i in items if i["status"] != "reported" and i["points"] is not None and i["question_type"] in quiz_v2.OBJECTIVE_TYPES)
    obj_max = sum(i["max_points"] for i in items if i["status"] != "reported" and i["points"] is not None and i["question_type"] in quiz_v2.OBJECTIVE_TYPES)
    texts = {}
    if pending:
        texts = {q["question_id"]: q.get("pending_text") for q in grading_activity.progress(conn, uid, paper_id)["questions"] if q["points"] is None and not q["excluded"]}
        for i in items:
            if i["status"] == "pending":
                i["pending_text"] = texts.get(i["question_id"]) or "待判分"
    spec = (paper["blueprint"] or {}).get("specification")
    seconds = int((session["submitted_at"] - session["started_at"]).total_seconds()) if session and session["submitted_at"] and session["started_at"] else None
    return dict(
        paper_id=paper_id, kind="paper", date=paper["paper_date"].isoformat(), specification=spec, specification_name=SPEC_NAMES.get(spec, spec),
        read_only=spec == "xiaohan-150-60-v1", pending=pending, score=None if pending else total_points, graded_points=total_points, max_points=total_max,
        subject_scores={s: dict(points=v[0], max_points=v[1], pending=v[2]) for s, v in sorted(by_subject.items())},
        seconds=seconds, objective_points=obj_points, objective_max=obj_max,
        grading_late=grading_recovery.late(conn, uid, paper_id) if pending else dict(count=0, minutes=grading_recovery.LATE_AFTER_MINUTES),
        type_rates=[dict(subject=s, question_type=t, points=v[0], max_points=v[1], rate=round(v[0] / v[1], 4) if v[1] else None)
                    for (s, t), v in sorted(by_type.items(), key=lambda kv: (kv[0][0], TYPES.index(kv[0][1]) if kv[0][1] in TYPES else 9))],
        items=items,
        counts={k: sum(1 for i in items if i["status"] == k) for k in ("right", "partial", "wrong", "blank", "pending", "reported")},
        flags=dict(mistake=sum(1 for i in items if i["flag"] == "mistake"), consolidate=sum(1 for i in items if i["flag"] == "consolidate")),
    )


def detail(conn, uid, paper_id, question_id):
    paper, attempt, rows = _paper_rows(conn, uid, paper_id)
    row = next((r for r in rows if r["question_id"] == question_id), None)
    if not row:
        raise LookupError("question_not_on_this_paper")
    snap = row["snapshot"]
    reported = bool(conn.execute("SELECT 1 FROM question_reports WHERE paper_id=%s AND question_id=%s", (paper_id, question_id)).fetchone())
    mine = plain_answer(row["answer"])
    options = snap.get("options") or {}
    letter = mine.strip().upper() if snap["question_type"] == "single_choice" else None
    feedback = row["feedback"] or {}
    raw = (snap.get("evidence") or [{}])[0].get("chapter")
    reference = snap.get("answer")
    return dict(
        paper_id=paper_id, question_id=question_id, position=row["position"], subject=snap["subject"], question_type=snap["question_type"],
        chapter=chapters.ChapterMap(conn).name(snap["subject"], raw) if raw else chapters.OTHER,
        stem=snap["stem"], options=options, your_choice=letter if letter in options else None, correct_choice=reference if snap["question_type"] == "single_choice" else None,
        your_answer=mine, reference=(f"{reference}. {options.get(reference, '')}" if snap["question_type"] == "single_choice" and reference in options else reference),
        accepted_answers=snap.get("accepted_answers") or [], points=row["points"], max_points=snap["max_points"], status=status_of(snap, row["answer"], row["points"], reported),
        reported=reported, criteria=feedback.get("criteria") or [], criterion_reasons=feedback.get("criterion_reasons") or [],
        rubric=[dict(description=c["description"], points=c["points"]) for c in snap.get("rubric") or []],
        blank_points=feedback.get("blank_points"), comment=feedback.get("comment"),
        concept_errors=feedback.get("concept_errors") or [], imprecisions=feedback.get("imprecisions") or [], suggestions=feedback.get("suggestions") or [],
        evidence=sources_of(snap),
        pending_text=next((q.get("pending_text") for q in grading_activity.progress(conn, uid, paper_id)["questions"] if q["question_id"] == question_id), None) if row["points"] is None and not reported else None,
    )


def practice_summary(conn, uid, set_id):
    """A practice set in the same shape as a paper summary (its questions in the order they were drawn)."""
    s = conn.execute("SELECT * FROM practice_sets WHERE id=%s AND user_id=%s", (set_id, uid)).fetchone()
    if not s:
        raise LookupError("practice_set_not_found")
    answered = {r["question_id"]: r for r in conn.execute("SELECT * FROM practice_answers WHERE set_id=%s", (set_id,)).fetchall()}
    mapper = chapters.ChapterMap(conn)
    items = []
    contents = {r["id"]: r["content"] for r in conn.execute("SELECT id,content FROM reviewed_questions WHERE id=ANY(%s)", (list(s["question_ids"]),)).fetchall()}
    for position, qid in enumerate(s["question_ids"], 1):
        content, a = contents.get(qid), answered.get(qid)
        if not content:
            continue
        raw = (content.get("evidence") or [{}])[0].get("chapter")
        state = "blank" if not a else "pending" if a["points"] is None else "right" if a["points"] >= a["max_points"] else "partial" if a["points"] > 0 else "wrong"
        items.append(dict(position=position, question_id=qid, subject=content["subject"], question_type=content["question_type"],
                          chapter=mapper.name(content["subject"], raw) if raw else chapters.OTHER, points=a["points"] if a else None,
                          max_points=content["max_points"], status=state, flag="mistake" if a and a["points"] is not None and a["points"] < a["max_points"] else None,
                          stem=" ".join((content.get("stem") or "").split())[:60]))
    got = [i for i in items if i["points"] is not None]
    seconds = sum(r["seconds"] or 0 for r in answered.values()) or None
    day = s["created_at"].astimezone(SHANGHAI).date().isoformat() if s.get("created_at") else None
    return dict(
        paper_id=set_id, kind="practice", date=day, specification=s["mode"], specification_name="专项练习", read_only=True, pending=sum(1 for i in items if i["status"] == "pending"),
        score=sum(i["points"] for i in got), graded_points=sum(i["points"] for i in got), max_points=sum(i["max_points"] for i in items),
        subject_scores={}, seconds=seconds, type_rates=[], items=items,
        counts={k: sum(1 for i in items if i["status"] == k) for k in ("right", "partial", "wrong", "blank", "pending", "reported")},
        flags=dict(mistake=sum(1 for i in items if i["flag"] == "mistake"), consolidate=0),
    )


def practice_detail(conn, uid, set_id, question_id):
    s = conn.execute("SELECT * FROM practice_sets WHERE id=%s AND user_id=%s", (set_id, uid)).fetchone()
    if not s or question_id not in s["question_ids"]:
        raise LookupError("question_not_in_this_set")
    content = conn.execute("SELECT content FROM reviewed_questions WHERE id=%s", (question_id,)).fetchone()["content"]
    a = conn.execute("SELECT * FROM practice_answers WHERE set_id=%s AND question_id=%s", (set_id, question_id)).fetchone()
    options = content.get("options") or {}
    mine = plain_answer(a["answer"]) if a else ""
    raw = (content.get("evidence") or [{}])[0].get("chapter")
    reference = content.get("answer")
    return dict(
        paper_id=set_id, question_id=question_id, subject=content["subject"], question_type=content["question_type"],
        chapter=chapters.ChapterMap(conn).name(content["subject"], raw) if raw else chapters.OTHER, stem=content["stem"], options=options,
        your_choice=mine.upper() if content["question_type"] == "single_choice" and mine.upper() in options else None,
        correct_choice=reference if content["question_type"] == "single_choice" else None, your_answer=mine,
        reference=(f"{reference}. {options.get(reference, '')}" if content["question_type"] == "single_choice" and reference in options else reference),
        accepted_answers=content.get("accepted_answers") or [], points=a["points"] if a else None, max_points=content["max_points"],
        status="blank" if not a else "pending" if a["points"] is None else "right" if a["points"] >= a["max_points"] else "partial" if a["points"] > 0 else "wrong", reported=False,
        criteria=((a["feedback"] or {}).get("criteria") or []) if a else [], criterion_reasons=((a["feedback"] or {}).get("criterion_reasons") or []) if a else [], rubric=[dict(description=c["description"], points=c["points"]) for c in content.get("rubric") or []],
        blank_points=a["answer"].get("blank_points") if a and isinstance(a["answer"], dict) else None, comment=((a["feedback"] or {}).get("comment") if a else None), concept_errors=[], imprecisions=[], suggestions=[],
        evidence=sources_of(content),
    )


PRACTICE_NAMES = {"weak": "薄弱章节练习", "chapter": "按章节练习", "type": "按题型专项", "retry": "错题重做", "quick": "随机快练", "year": "真题按年份"}


def recent(conn, uid, limit=5):
    """The latest trainings, daily papers and practice sets together, newest first: date, kind, score / full marks, time taken."""
    out = []
    for r in conn.execute(
        "SELECT p.id,p.paper_date,p.blueprint->>'specification' AS spec,t.submitted_at,ts.started_at,"
        "(SELECT sum(a.points) FROM answers_v2 a WHERE a.attempt_id=t.id) AS points,"
        "(SELECT count(*) FROM answers_v2 a WHERE a.attempt_id=t.id AND a.points IS NULL) AS pending,"
        "(SELECT sum((i.snapshot->>'max_points')::int) FROM paper_items_v2 i WHERE i.paper_id=p.id) AS max_points "
        "FROM papers_v2 p JOIN attempts_v2 t ON t.paper_id=p.id LEFT JOIN training_sessions ts ON ts.paper_id=p.id "
        "WHERE p.user_id=%s ORDER BY t.submitted_at DESC LIMIT %s",
        (uid, limit),
    ).fetchall():
        out.append(dict(
            kind="paper", id=r["id"], date=r["paper_date"].isoformat(), label="每日训练卷" if r["spec"] != "xiaohan-150-60-v1" else "每日训练卷（旧）",
            points=None if r["pending"] else int(r["points"] or 0), max_points=int(r["max_points"] or 0), pending=int(r["pending"] or 0),
            seconds=int((r["submitted_at"] - r["started_at"]).total_seconds()) if r["started_at"] else None, at=r["submitted_at"].isoformat(),
        ))
    for r in conn.execute(
        "SELECT s.id,s.mode,s.created_at,count(a.*) AS n,count(*) FILTER (WHERE a.points IS NULL) AS pending,sum(a.points) AS points,sum(a.max_points) AS max_points,sum(a.seconds) AS seconds,max(a.answered_at) AS last "
        "FROM practice_sets s JOIN practice_answers a ON a.set_id=s.id WHERE s.user_id=%s GROUP BY s.id ORDER BY max(a.answered_at) DESC LIMIT %s",
        (uid, limit),
    ).fetchall():
        out.append(dict(
            kind="practice", id=r["id"], date=r["last"].astimezone(SHANGHAI).date().isoformat(), label=PRACTICE_NAMES.get(r["mode"], "专项练习"),
            points=None if r["pending"] else int(r["points"] or 0), max_points=int(r["max_points"] or 0), pending=int(r["pending"]), seconds=int(r["seconds"]) if r["seconds"] else None, at=r["last"].isoformat(),
        ))
    out.sort(key=lambda x: x["at"], reverse=True)
    return out[:limit]
