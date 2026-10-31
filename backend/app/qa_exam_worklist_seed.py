"""Idempotently prepare pending genuine-exam review work in isolated QA only.

A worklist entry is a transcription candidate, NOT an official original,
scientific answer, OCR acceptance, or published training question.
"""
from app.evidence_work import VERSION, build, persist


def ensure(conn, builder=build, persister=persist):
    existing = conn.execute(
        "SELECT count(*) AS n FROM evidence_review_work "
        "WHERE algorithm=%s AND kind='exam_question'", (VERSION,),
    ).fetchone()["n"]
    if existing:
        return {"algorithm": VERSION, "new": 0, "already_present": existing}
    source = conn.execute(
        "SELECT count(*) AS n FROM exam_transcript_candidates",
    ).fetchone()["n"]
    if not source:
        return {"algorithm": VERSION, "new": 0, "already_present": 0}
    # Only this kind is persisted. Building reports and pending proposals
    # from real six-source bytes never promotes scientific trust or authenticity.
    tasks = [
        work for work in builder(conn)["work"]
        if work["algorithm"] == VERSION and work["kind"] == "exam_question"
        and work["status"] == "pending"
    ]
    if not tasks:
        return {"algorithm": VERSION, "new": 0, "already_present": 0}
    return {"algorithm": VERSION, "new": persister(conn, tasks),
            "already_present": 0}
