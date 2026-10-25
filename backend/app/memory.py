"""FSRS-6 scheduling; existing fixed-interval history is never fabricated."""

import json
from datetime import timezone
from fsrs import Card, Rating, Scheduler
from psycopg.types.json import Jsonb
from app.training_sessions import server_now

SCHEDULER = Scheduler(enable_fuzzing=False)


def record_failure(conn, attempt_id, question_id):
    row = conn.execute(
        "SELECT p.user_id,a.answer FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id "
        "JOIN answers_v2 a ON a.attempt_id=t.id WHERE t.id=%s AND a.question_id=%s",
        (attempt_id, question_id),
    ).fetchone()
    uid = row["user_id"]
    now = server_now(conn).astimezone(timezone.utc)
    conn.execute(
        "INSERT INTO fsrs_cards(user_id,question_id,card,due) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (uid, question_id, Jsonb(Card(card_id=question_id, due=now).to_dict()), now),
    )
    try:
        value = json.loads(row["answer"])
    except ValueError:
        value = {"text": row["answer"]}
    if value.get("text") or any(value.get("blanks", [])) or value.get("image_ids"):
        review(
            conn,
            uid,
            question_id,
            1,
            f"graded-failure:{attempt_id}:{question_id}",
            None,
            require_due=False,
        )


def review(
    conn, user_id, question_id, rating, event_key, expected_version, *, require_due=True
):
    row = conn.execute(
        "SELECT * FROM fsrs_cards WHERE user_id=%s AND question_id=%s FOR UPDATE",
        (user_id, question_id),
    ).fetchone()
    if not row:
        raise LookupError("Review card not found")
    prior = conn.execute(
        "SELECT * FROM fsrs_events WHERE user_id=%s AND event_key=%s",
        (user_id, event_key),
    ).fetchone()
    if prior:
        if prior["question_id"] != question_id or prior["rating"] != rating:
            raise ValueError("Review event key reused with different content")
        return {"card": prior["after_card"], "replayed": True}
    if type(rating) is not int or rating not in (1, 2, 3, 4):
        raise ValueError("FSRS rating must be Again=1, Hard=2, Good=3 or Easy=4")
    now = server_now(conn).astimezone(timezone.utc)
    if require_due and row["due"] > now:
        raise ValueError("Review is not yet due")
    if expected_version is not None and row["version"] != expected_version:
        raise ValueError("version_conflict")
    card, _ = SCHEDULER.review_card(Card.from_dict(row["card"]), Rating(rating), now)
    conn.execute(
        "UPDATE fsrs_cards SET card=%s,due=%s,version=version+1 WHERE user_id=%s AND question_id=%s",
        (Jsonb(card.to_dict()), card.due, user_id, question_id),
    )
    conn.execute(
        "INSERT INTO fsrs_events(user_id,question_id,event_key,rating,before_card,after_card,reviewed_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (
            user_id,
            question_id,
            event_key,
            rating,
            Jsonb(row["card"]),
            Jsonb(card.to_dict()),
            now,
        ),
    )
    return {"card": card.to_dict(), "version": row["version"] + 1, "replayed": False}


def due(conn, user_id):
    return conn.execute(
        "SELECT c.question_id,c.card,c.version,c.due,q.content->>'stem' AS stem "
        "FROM fsrs_cards c JOIN reviewed_questions q ON q.id=c.question_id "
        "WHERE c.user_id=%s AND c.due<=clock_timestamp() ORDER BY c.due,c.question_id",
        (user_id,),
    ).fetchall()
