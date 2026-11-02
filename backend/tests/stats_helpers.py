"""Synthetic submitted papers for the statistics tests (rows inserted directly; no model, no bank)."""
from datetime import datetime, timedelta, timezone
from itertools import count

from psycopg.types.json import Jsonb

SPEC = "xiaohan-60q-300pt-v4"
_ids = count(1)


def question(db, subject, qtype, max_points, chapter, stem=None, published=True, **content_overrides):
    content = dict(subject=subject, question_type=qtype, stem=stem or f"合成题干 {next(_ids)}", max_points=max_points,
                   answer="A", accepted_answers=["答"], options={}, blanks=[], rubric=[dict(description="得分点", points=max_points)],
                   evidence=[dict(chapter=chapter, textbook="合成", excerpt="原文")])
    content.update(content_overrides)
    qid = db.execute("INSERT INTO reviewed_questions(content) VALUES (%s) RETURNING id", (Jsonb(content),)).fetchone()["id"]
    if published:
        publish(db, [qid])
    return qid


def publish(db, ids):
    """Publication normally needs the whole review chain (tested elsewhere); rows are flagged directly here."""
    db.execute("ALTER TABLE reviewed_questions DISABLE TRIGGER USER")
    db.execute("UPDATE reviewed_questions SET status='published' WHERE id=ANY(%s)", (list(ids),))
    db.execute("ALTER TABLE reviewed_questions ENABLE TRIGGER USER")


def paper(db, uid, day, items, when=None, spec=SPEC, minutes=50):
    """items: dicts of subject, qtype, max_points, chapter, points (None = pending), seconds, answer."""
    when = when or datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc)
    pid = db.execute("INSERT INTO papers_v2(user_id,paper_date,blueprint) VALUES (%s,%s,%s) RETURNING id",
                     (uid, day, Jsonb({"specification": spec}))).fetchone()["id"]
    started = when - timedelta(minutes=minutes)
    db.execute("INSERT INTO training_sessions(paper_id,started_at,deadline,submitted_at) VALUES (%s,%s,%s,%s)",
               (pid, started, started + timedelta(minutes=60), when))
    aid = db.execute("INSERT INTO attempts_v2(paper_id,submitted_at) VALUES (%s,%s) RETURNING id", (pid, when)).fetchone()["id"]
    qids = []
    for position, it in enumerate(items, 1):
        qid = it.get("question_id") or question(db, it["subject"], it["qtype"], it["max_points"], it["chapter"])
        content = db.execute("SELECT content FROM reviewed_questions WHERE id=%s", (qid,)).fetchone()["content"]
        db.execute("INSERT INTO paper_items_v2(paper_id,question_id,position,snapshot) VALUES (%s,%s,%s,%s)", (pid, qid, position, Jsonb(content)))
        db.execute("INSERT INTO answers_v2(attempt_id,question_id,answer,points) VALUES (%s,%s,%s,%s)",
                   (aid, qid, it.get("answer", '{"text":"x"}'), it["points"]))
        if it.get("seconds"):
            db.execute("INSERT INTO answer_timings(attempt_id,question_id,seconds) VALUES (%s,%s,%s)", (aid, qid, it["seconds"]))
        qids.append(qid)
    return dict(paper_id=pid, attempt_id=aid, question_ids=qids)


def item(subject="biochemistry", qtype="single_choice", points=4, got=4, chapter="第一章 测试", seconds=None, **extra):
    return dict(subject=subject, qtype=qtype, max_points=points, points=got, chapter=chapter, seconds=seconds, **extra)
