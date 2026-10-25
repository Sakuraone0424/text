"""Read-only access to historical records. No generation, seeding or scoring."""

from typing import Any

Connection = Any
TOTAL_QUESTIONS = 60  # Historical score scale only; never a training quota.


def load_paper(conn: Connection, user_id: int, paper_id: int) -> dict:
    paper = conn.execute(
        """
        SELECT p.id, p.paper_date, att.id AS attempt_id, att.score
        FROM papers p
        LEFT JOIN attempts att ON att.paper_id = p.id
        WHERE p.id = %s AND p.user_id = %s
        """,
        (paper_id, user_id),
    ).fetchone()
    if not paper:
        raise LookupError("Paper not found")

    questions = conn.execute(
        """
        SELECT
            q.id,
            t.subject,
            t.name AS topic,
            q.stem,
            q.options,
            q.difficulty,
            q.source,
            pq.position
        FROM paper_questions pq
        JOIN questions q ON q.id = pq.question_id
        JOIN topics t ON t.id = q.topic_id
        WHERE pq.paper_id = %s
        ORDER BY pq.position
        """,
        (paper_id,),
    ).fetchall()

    return {
        "paper_id": int(paper["id"]),
        "date": paper["paper_date"].isoformat(),
        "count": len(questions),
        "submitted": paper["attempt_id"] is not None,
        "score": int(paper["score"]) if paper["score"] is not None else None,
        "questions": [dict(row) for row in questions],
    }


def history(conn: Connection, user_id: int, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.paper_date, att.score, att.submitted_at
        FROM attempts att
        JOIN papers p ON p.id = att.paper_id
        WHERE att.user_id = %s
        ORDER BY p.paper_date DESC
        LIMIT %s
        """,
        (user_id, limit),
    ).fetchall()
    return [
        {
            "date": row["paper_date"].isoformat(),
            "score": int(row["score"]),
            "total": TOTAL_QUESTIONS,
            "percent": round(int(row["score"]) / TOTAL_QUESTIONS * 100, 1),
            "submitted_at": row["submitted_at"].isoformat(),
        }
        for row in rows
    ]


def stats_summary(conn: Connection, user_id: int) -> dict:
    overall = conn.execute(
        """
        SELECT
            COUNT(*) AS answered,
            COUNT(*) FILTER (WHERE a.is_correct) AS correct
        FROM answers a
        JOIN attempts att ON att.id = a.attempt_id
        WHERE att.user_id = %s
        """,
        (user_id,),
    ).fetchone()

    topic_rows = conn.execute(
        """
        SELECT
            t.subject,
            t.name AS topic,
            COUNT(*) AS answered,
            COUNT(*) FILTER (WHERE a.is_correct) AS correct
        FROM answers a
        JOIN attempts att ON att.id = a.attempt_id
        JOIN questions q ON q.id = a.question_id
        JOIN topics t ON t.id = q.topic_id
        WHERE att.user_id = %s
        GROUP BY t.subject, t.name
        ORDER BY
            CASE WHEN COUNT(*) = 0 THEN 1
                 ELSE COUNT(*) FILTER (WHERE a.is_correct)::float / COUNT(*) END,
            COUNT(*) DESC,
            t.name
        """,
        (user_id,),
    ).fetchall()

    answered = int(overall["answered"] or 0)
    correct = int(overall["correct"] or 0)
    topics = []
    for row in topic_rows:
        total = int(row["answered"])
        right = int(row["correct"])
        topics.append(
            {
                "subject": row["subject"],
                "topic": row["topic"],
                "answered": total,
                "correct": right,
                "accuracy": round(right / total * 100, 1) if total else None,
            }
        )

    return {
        "answered": answered,
        "correct": correct,
        "accuracy": round(correct / answered * 100, 1) if answered else None,
        "weak_topics": topics[:10],
    }
