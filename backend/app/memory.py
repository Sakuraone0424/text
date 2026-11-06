"""FSRS-6 scheduling; existing fixed-interval history is never fabricated."""

import json
from datetime import timezone
from fsrs import Card, Rating, Scheduler
from psycopg.types.json import Jsonb
from app.training_sessions import server_now

SCHEDULER = Scheduler(enable_fuzzing=False)


SUBJECTIVE = ("term", "short_answer", "essay")
DEFAULT_LINES = (0.60, 0.85)


def lines(conn, user_id):
    """(mistake_below, consolidate_below): the two score-rate lines for subjective answers (settings page)."""
    row = conn.execute("SELECT mistake_below,consolidate_below FROM ai_settings WHERE user_id=%s", (user_id,)).fetchone()
    return (float(row["mistake_below"]), float(row["consolidate_below"])) if row else DEFAULT_LINES


def classify(question_type, points, max_points, mistake_below=0.60, consolidate_below=0.85):
    """What one graded answer is: 'mistake' (review rating Again), 'consolidate' (rating Hard) or 'mastered' (no review card).

    Single choice and fill-in-the-blank stay right or wrong (anything short of full marks is a mistake). The three subjective
    types go by their score rate: below the first line a mistake, up to the second still to be consolidated, from there on mastered."""
    if points is None or not max_points:
        return None
    if question_type not in SUBJECTIVE:
        return "mastered" if points >= max_points else "mistake"
    rate = points / max_points
    if rate < mistake_below:
        return "mistake"
    return "consolidate" if rate < consolidate_below else "mastered"


def record_failure(conn, attempt_id, question_id):
    row = conn.execute(
        "SELECT p.user_id,a.answer,a.points,(i.snapshot->>'max_points')::int AS max_points,i.snapshot->>'question_type' AS question_type FROM attempts_v2 t JOIN papers_v2 p ON p.id=t.paper_id "
        "JOIN answers_v2 a ON a.attempt_id=t.id JOIN paper_items_v2 i ON i.paper_id=t.paper_id AND i.question_id=a.question_id WHERE t.id=%s AND a.question_id=%s",
        (attempt_id, question_id),
    ).fetchone()
    uid = row["user_id"]
    now = server_now(conn).astimezone(timezone.utc)
    try:
        value = json.loads(row["answer"])
    except ValueError:
        value = {"text": row["answer"]}
    if not isinstance(value, dict):
        value = {"text": row["answer"]}
    answered = bool(
        value.get("text", "").strip()
        or any(v.strip() for v in value.get("blanks", []))
        or value.get("image_ids")
    )
    if not answered or row["points"] is None:
        return
    kind = classify(row["question_type"], row["points"], row["max_points"], *lines(conn, uid))
    if kind in (None, "mastered"):
        return                                                      # right, or a subjective answer at/above the second line: no card
    if conn.execute("SELECT 1 FROM question_reports WHERE user_id=%s AND question_id=%s", (uid, question_id)).fetchone():
        return  # the learner reported this question: no review card for it
    conn.execute(
        "INSERT INTO fsrs_cards(user_id,question_id,card,due,status) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (uid, question_id, Jsonb(Card(card_id=question_id, due=now).to_dict()), now, kind),
    )
    if answered:
        review(
            conn,
            uid,
            question_id,
            1 if kind == "mistake" else 2,                          # Again for a mistake, Hard for "to consolidate"
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
        "SELECT c.question_id,c.card,c.version,c.due,q.content->>'stem' AS stem, "
        "q.content->>'question_type' AS question_type,q.content->'options' AS options "
        "FROM fsrs_cards c JOIN reviewed_questions q ON q.id=c.question_id "
        "WHERE c.user_id=%s AND c.due<=clock_timestamp() AND c.status<>'mastered' "
        "AND NOT EXISTS(SELECT 1 FROM question_reports qr WHERE qr.user_id=c.user_id AND qr.question_id=c.question_id) "
        "ORDER BY c.due,c.question_id",
        (user_id,),
    ).fetchall()


STATE_KEY = "mistake_reclassify_v1"


def reclassify_existing(conn):
    """Once: sort the review cards that exist by the new rule, using the learner's latest graded answer to each question.
    A card whose answer is now a mistake stays one, one at 60%-85% becomes 'consolidate', one above is marked 'mastered' (left out of
    the reviews and the mistake list). Nothing is deleted: the cards and every review event stay. Returns the counts, or None
    when it already ran."""
    from collections import Counter

    from app import legacy_reuse

    if legacy_reuse._state(conn, STATE_KEY):
        return None
    before, after = Counter(), Counter()
    thresholds = {}
    for card in conn.execute(
        "SELECT c.user_id,c.question_id,c.status,q.content->>'question_type' AS question_type FROM fsrs_cards c "
        "JOIN reviewed_questions q ON q.id=c.question_id"
    ).fetchall():
        before[card["status"]] += 1
        latest = conn.execute(
            "SELECT a.points,(i.snapshot->>'max_points')::int AS max_points FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id "
            "JOIN papers_v2 p ON p.id=t.paper_id JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id "
            "WHERE p.user_id=%s AND a.question_id=%s AND a.points IS NOT NULL ORDER BY t.submitted_at DESC NULLS LAST,t.id DESC LIMIT 1",
            (card["user_id"], card["question_id"]),
        ).fetchone()
        kind = card["status"]
        if latest:
            thresholds.setdefault(card["user_id"], lines(conn, card["user_id"]))
            kind = classify(card["question_type"], latest["points"], latest["max_points"], *thresholds[card["user_id"]]) or kind
        if kind != card["status"]:
            conn.execute("UPDATE fsrs_cards SET status=%s WHERE user_id=%s AND question_id=%s", (kind, card["user_id"], card["question_id"]))
        after[kind] += 1
    result = dict(before=dict(before), after=dict(after))
    legacy_reuse._set_state(conn, STATE_KEY, result)
    return result
