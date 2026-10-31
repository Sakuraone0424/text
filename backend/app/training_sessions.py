"""Server-clock timed sessions. All writes lock the paper then its session."""

import json
from datetime import timedelta

from psycopg.types.json import Jsonb

from app.training_spec import DURATION_SECONDS, validate_items
from app import training_spec_v2 as new_spec
from app.question_quality import normalize_answer


def validate_paper_items(conn, paper_id, items):
    row = conn.execute("SELECT blueprint FROM papers_v2 WHERE id=%s", (paper_id,)).fetchone()
    if not row:
        raise LookupError("Paper not found")
    if row["blueprint"].get("specification") == new_spec.SPECIFICATION:
        return new_spec.validate_items(items)
    return validate_items(items)


def server_now(conn):
    return conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]


def locked_session(conn, user_id, paper_id):
    from app.quiz_v2 import owned_paper

    owned_paper(conn, user_id, paper_id, lock=True)
    return conn.execute(
        "SELECT * FROM training_sessions WHERE paper_id=%s FOR UPDATE", (paper_id,)
    ).fetchone()


def start(conn, user_id, paper_id):
    session = locked_session(conn, user_id, paper_id)
    if session is None:
        items = conn.execute(
            "SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s", (paper_id,)
        ).fetchall()
        validate_paper_items(conn, paper_id, [i["snapshot"] for i in items])
        if conn.execute(
            "SELECT 1 FROM attempts_v2 WHERE paper_id=%s", (paper_id,)
        ).fetchone():
            raise ValueError("Historical submitted paper cannot be restarted")
        now = server_now(conn)
        conn.execute(
            "INSERT INTO training_sessions(paper_id,started_at,deadline) VALUES (%s,%s,%s)",
            (paper_id, now, now + timedelta(seconds=DURATION_SECONDS)),
        )
    return state(conn, user_id, paper_id)


def state(conn, user_id, paper_id):
    session = locked_session(conn, user_id, paper_id)
    now = server_now(conn)
    if session is None:
        return {
            "status": "not_started",
            "server_now": now,
            "duration_seconds": DURATION_SECONDS,
            "version": 0,
            "remaining_seconds": DURATION_SECONDS,
            "drafts": {},
        }
    if session["submitted_at"] is None and now >= session["deadline"]:
        finalize(conn, user_id, paper_id, session, timed_out=True)
        session["submitted_at"] = session["deadline"]
    drafts = {
        str(r["question_id"]): r["value"]
        for r in conn.execute(
            "SELECT question_id,value FROM answer_drafts WHERE paper_id=%s", (paper_id,)
        ).fetchall()
    }
    return {
        "status": "submitted" if session["submitted_at"] else "in_progress",
        "server_now": now,
        "started_at": session["started_at"],
        "deadline": session["deadline"],
        "remaining_seconds": max(0, int((session["deadline"] - now).total_seconds())),
        "duration_seconds": DURATION_SECONDS,
        "version": session["version"],
        "drafts": drafts,
    }


def valid_value(conn, paper_id, question_id, value):
    item = conn.execute(
        "SELECT snapshot FROM paper_items_v2 WHERE paper_id=%s AND question_id=%s",
        (paper_id, question_id),
    ).fetchone()
    if not item:
        raise ValueError("Answer does not belong to this paper")
    if not isinstance(value, dict) or set(value) - {
        "text",
        "blanks",
        "image_ids",
        "flagged",
    }:
        raise ValueError("Invalid draft shape")
    text = value.get("text", "")
    blanks = value.get("blanks", [])
    images = value.get("image_ids", [])
    if not isinstance(text, str) or len(text) > 20000:
        raise ValueError("Text must be at most 20000 characters")
    if (
        not isinstance(blanks, list)
        or len(blanks) > 3
        or any(not isinstance(v, str) or len(v) > 2000 for v in blanks)
    ):
        raise ValueError("Invalid blank answers")
    q = item["snapshot"]
    if blanks and (
        q["question_type"] != "fill_blank" or len(blanks) != len(q.get("blanks", []))
    ):
        raise ValueError("Blank count must match question")
    if (
        not isinstance(images, list)
        or len(images) > 5
        or any(type(i) is not int for i in images)
    ):
        raise ValueError("At most five image IDs are allowed")
    if images and q["question_type"] in ("single_choice", "fill_blank"):
        raise ValueError("Images are only supported for subjective answers")
    for image_id in images:
        if not conn.execute(
            "SELECT 1 FROM answer_images WHERE id=%s AND paper_id=%s AND question_id=%s",
            (image_id, paper_id, question_id),
        ).fetchone():
            raise ValueError("Image does not belong to this answer")
    if (
        q["question_type"] == "single_choice"
        and text.strip()
        and text.strip().upper() not in ("A", "B", "C", "D")
    ):
        raise ValueError("Single choice answer must be A-D")
    if type(value.get("flagged", False)) is not bool:
        raise ValueError("Flag must be boolean")
    return {
        "text": text,
        "blanks": blanks,
        "image_ids": images,
        "flagged": value.get("flagged", False),
    }


def assert_writable(conn, user_id, paper_id, expected_version):
    session = locked_session(conn, user_id, paper_id)
    if session is None:
        raise ValueError("Start the training before answering")
    if session["submitted_at"]:
        raise ValueError("Paper already submitted")
    if server_now(conn) >= session["deadline"]:
        finalize(conn, user_id, paper_id, session, timed_out=True)
        return None
    if type(expected_version) is not int or expected_version != session["version"]:
        raise ValueError("version_conflict: reload server drafts before merging")
    return session


def save(conn, user_id, paper_id, question_id, value, expected_version):
    session = assert_writable(conn, user_id, paper_id, expected_version)
    if session is None:
        return {"accepted": False, **state(conn, user_id, paper_id)}
    value = valid_value(conn, paper_id, question_id, value)
    if server_now(conn) >= session["deadline"]:
        finalize(conn, user_id, paper_id, session, timed_out=True)
        return {"accepted": False, **state(conn, user_id, paper_id)}
    conn.execute(
        "INSERT INTO answer_drafts VALUES (%s,%s,%s) ON CONFLICT(paper_id,question_id) DO UPDATE SET value=EXCLUDED.value",
        (paper_id, question_id, Jsonb(value)),
    )
    conn.execute(
        "UPDATE training_sessions SET version=version+1 WHERE paper_id=%s", (paper_id,)
    )
    return {"accepted": True, **state(conn, user_id, paper_id)}


def submit_training(conn, user_id, paper_id, answers, expected_version):
    from app.quiz_v2 import result

    existing = locked_session(conn, user_id, paper_id)
    if existing is not None and existing["submitted_at"]:
        # A lost response or process restart must not turn an accepted submit
        # into an error. Ignore later answer payloads; never regrade/relearn.
        return result(conn, user_id, paper_id)
    session = assert_writable(conn, user_id, paper_id, expected_version)
    if session is None:
        return result(conn, user_id, paper_id)

    # Validate and merge in a savepoint so crossing the deadline never credits
    # newly submitted answers or destroys the previously persisted drafts.
    class DeadlineCrossed(Exception):
        pass

    try:
        with conn.transaction():
            for qid, answer in answers.items():
                value = answer if isinstance(answer, dict) else {"text": answer}
                value = valid_value(conn, paper_id, qid, value)
                conn.execute(
                    "INSERT INTO answer_drafts VALUES (%s,%s,%s) ON CONFLICT(paper_id,question_id) DO UPDATE SET value=EXCLUDED.value",
                    (paper_id, qid, Jsonb(value)),
                )
            if server_now(conn) >= session["deadline"]:
                raise DeadlineCrossed()
    except DeadlineCrossed:
        finalize(conn, user_id, paper_id, session, timed_out=True)
        return result(conn, user_id, paper_id)
    finalize(conn, user_id, paper_id, session, timed_out=False)
    return result(conn, user_id, paper_id)


def finalize(conn, user_id, paper_id, session, *, timed_out):
    from app.quiz_v2 import schedule_review

    if session["submitted_at"]:
        return
    items = conn.execute(
        "SELECT question_id,snapshot FROM paper_items_v2 WHERE paper_id=%s ORDER BY position",
        (paper_id,),
    ).fetchall()
    validate_paper_items(conn, paper_id, [i["snapshot"] for i in items])
    drafts = {
        r["question_id"]: r["value"]
        for r in conn.execute(
            "SELECT * FROM answer_drafts WHERE paper_id=%s", (paper_id,)
        ).fetchall()
    }
    submitted_at = session["deadline"] if timed_out else server_now(conn)
    aid = conn.execute(
        "INSERT INTO attempts_v2(paper_id,submitted_at) VALUES (%s,%s) RETURNING id",
        (paper_id, submitted_at),
    ).fetchone()["id"]
    for item in items:
        qid, q = item["question_id"], item["snapshot"]
        value = drafts.get(qid, {"text": "", "blanks": [], "image_ids": []})
        text = value.get("text", "").strip()
        answered = bool(
            text
            or any(v.strip() for v in value.get("blanks", []))
            or value.get("image_ids")
        )
        points = None if answered else 0
        feedback = None
        if q["question_type"] == "single_choice":
            points = q["max_points"] if text.upper() == q["answer"] else 0
        elif q["question_type"] == "fill_blank":
            if q.get("blanks"):
                supplied = value.get("blanks", [])
                per_blank = [
                    b["points"]
                    if n < len(supplied)
                    and normalize_answer(supplied[n])
                    in {normalize_answer(a) for a in b["accepted_answers"]}
                    else 0
                    for n, b in enumerate(q["blanks"])
                ]
                points = sum(per_blank)
                feedback = {"blank_points": per_blank}
            else:
                points = (
                    q["max_points"]
                    if normalize_answer(text)
                    in {normalize_answer(a) for a in q["accepted_answers"]}
                    else 0
                )
        if (
            q["question_type"] == "fill_blank"
            and answered
            and points < q["max_points"]
            and conn.execute(
                "SELECT 1 FROM auto_question_states WHERE question_id=%s", (qid,)
            ).fetchone()
        ):
            feedback = {
                **(feedback or {}),
                "deterministic_points": points,
                "semantic_judgment_pending": True,
            }
            points = None
        conn.execute(
            "INSERT INTO answers_v2(attempt_id,question_id,answer,points,feedback) VALUES (%s,%s,%s,%s,%s)",
            (
                aid,
                qid,
                json.dumps(value, ensure_ascii=False),
                points,
                Jsonb(feedback) if feedback else None,
            ),
        )
        if answered and points is not None and points < q["max_points"]:
            schedule_review(conn, aid, qid)
    conn.execute(
        "UPDATE training_sessions SET submitted_at=%s,version=version+1 WHERE paper_id=%s",
        (submitted_at, paper_id),
    )
    from app.archiving import enqueue

    enqueue(conn, user_id, paper_id)
    if timed_out:
        # Timeout finalization can run from the expiry worker, a draft retry
        # or a read-only API request. It must have the same conditional grading
        # dispatch as an explicit submit, even when the learner is offline.
        # try_queue enforces provider/private-data authorization and never
        # performs a paid model call in this transaction. Job keys are
        # idempotent; a later submit retry cannot duplicate accepted grades.
        from app.grading_activity import try_queue

        try_queue(conn, user_id, paper_id)


def expire_due(conn):
    rows = conn.execute(
        "SELECT s.paper_id,p.user_id FROM training_sessions s JOIN papers_v2 p ON p.id=s.paper_id "
        "WHERE s.submitted_at IS NULL AND s.deadline<=clock_timestamp() ORDER BY s.deadline LIMIT 50"
    ).fetchall()
    for row in rows:
        state(conn, row["user_id"], row["paper_id"])
    return len(rows)
