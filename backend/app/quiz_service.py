"""Database-backed quiz generation, scoring, history and adaptive review logic."""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

Connection = Any

SHANGHAI = ZoneInfo("Asia/Shanghai")
SUBJECTS = ("biochemistry", "cell_biology")
TARGET_PER_SUBJECT = 30
TOTAL_QUESTIONS = 60


def today_cn() -> date:
    return datetime.now(SHANGHAI).date()


def ensure_user(conn: Connection, username: str) -> int:
    row = conn.execute(
        """
        INSERT INTO users (username)
        VALUES (%s)
        ON CONFLICT (username) DO UPDATE SET username = EXCLUDED.username
        RETURNING id
        """,
        (username,),
    ).fetchone()
    return int(row["id"])


def normalize_answer(value: str | None) -> str | None:
    if value is None:
        return None
    answer = value.strip().upper()
    return answer if answer in {"A", "B", "C", "D"} else None


def score_questions(question_rows: list[dict], answers: dict[int, str]) -> tuple[int, list[dict]]:
    score = 0
    details: list[dict] = []
    for row in question_rows:
        qid = int(row["id"])
        selected = normalize_answer(answers.get(qid))
        correct_answer = row["correct_answer"]
        is_correct = selected == correct_answer
        if is_correct:
            score += 1
        details.append(
            {
                "id": qid,
                "subject": row["subject"],
                "topic": row["topic"],
                "selected_answer": selected,
                "correct_answer": correct_answer,
                "correct": is_correct,
                "explanation": row["explanation"],
            }
        )
    return score, details


def _weighted_sample_without_replacement(
    items: list[dict], count: int, seed_text: str, excluded: set[int]
) -> list[int]:
    rng = random.Random(int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16))
    pool = [item.copy() for item in items if int(item["id"]) not in excluded]
    selected: list[int] = []
    while pool and len(selected) < count:
        total = sum(max(0.05, float(item.get("weight", 1.0))) for item in pool)
        pick = rng.random() * total
        cursor = 0.0
        chosen_idx = len(pool) - 1
        for idx, item in enumerate(pool):
            cursor += max(0.05, float(item.get("weight", 1.0)))
            if cursor >= pick:
                chosen_idx = idx
                break
        chosen = pool.pop(chosen_idx)
        selected.append(int(chosen["id"]))
    return selected


def _candidate_rows(conn: Connection, user_id: int, subject: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            q.id,
            COALESCE(stats.total, 0) AS total_attempts,
            COALESCE(stats.wrong, 0) AS wrong_attempts
        FROM questions q
        JOIN topics t ON t.id = q.topic_id
        LEFT JOIN (
            SELECT
                a.question_id,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE NOT a.is_correct) AS wrong
            FROM answers a
            JOIN attempts att ON att.id = a.attempt_id
            WHERE att.user_id = %s
            GROUP BY a.question_id
        ) stats ON stats.question_id = q.id
        WHERE t.subject = %s
        ORDER BY q.id
        """,
        (user_id, subject),
    ).fetchall()
    out = []
    for row in rows:
        total = int(row["total_attempts"])
        wrong = int(row["wrong_attempts"])
        wrong_rate = (wrong / total) if total else 0.0
        unseen_bonus = 1.4 if total == 0 else 0.0
        out.append(
            {
                "id": int(row["id"]),
                "weight": 1.0 + unseen_bonus + 4.0 * wrong_rate,
            }
        )
    return out


def _due_review_ids(conn: Connection, user_id: int, subject: str, day: date) -> list[int]:
    rows = conn.execute(
        """
        SELECT DISTINCT rs.question_id
        FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        JOIN topics t ON t.id = q.topic_id
        WHERE rs.user_id = %s
          AND rs.completed_at IS NULL
          AND rs.due_date <= %s
          AND t.subject = %s
        ORDER BY rs.question_id
        """,
        (user_id, day, subject),
    ).fetchall()
    return [int(row["question_id"]) for row in rows]


def get_or_create_today_paper(conn: Connection, user_id: int, day: date | None = None) -> dict:
    day = day or today_cn()
    existing = conn.execute(
        "SELECT id FROM papers WHERE user_id = %s AND paper_date = %s",
        (user_id, day),
    ).fetchone()
    if existing:
        paper_id = int(existing["id"])
    else:
        subject_choices: dict[str, list[int]] = {}
        used: set[int] = set()
        for subject in SUBJECTS:
            due = _due_review_ids(conn, user_id, subject, day)
            due = [qid for qid in due if qid not in used][:TARGET_PER_SUBJECT]
            used.update(due)
            need = TARGET_PER_SUBJECT - len(due)
            candidates = _candidate_rows(conn, user_id, subject)
            filler = _weighted_sample_without_replacement(
                candidates,
                need,
                f"{user_id}:{day.isoformat()}:{subject}",
                used,
            )
            selected = due + filler
            if len(selected) < TARGET_PER_SUBJECT:
                raise ValueError(
                    f"题库不足：{subject} 需要 {TARGET_PER_SUBJECT} 题，当前只能选出 {len(selected)} 题"
                )
            subject_choices[subject] = selected
            used.update(selected)

        paper_row = conn.execute(
            """
            INSERT INTO papers (user_id, paper_date)
            VALUES (%s, %s)
            RETURNING id
            """,
            (user_id, day),
        ).fetchone()
        paper_id = int(paper_row["id"])

        ordered = subject_choices["biochemistry"] + subject_choices["cell_biology"]
        for position, qid in enumerate(ordered, start=1):
            conn.execute(
                """
                INSERT INTO paper_questions (paper_id, question_id, position)
                VALUES (%s, %s, %s)
                """,
                (paper_id, qid, position),
            )

    return load_paper(conn, user_id, paper_id)


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


def submit_paper(
    conn: Connection,
    user_id: int,
    answers: dict[int, str],
    paper_id: int | None = None,
    day: date | None = None,
) -> dict:
    day = day or today_cn()
    if paper_id is None:
        paper = get_or_create_today_paper(conn, user_id, day)
        paper_id = int(paper["paper_id"])

    existing = conn.execute(
        "SELECT id, score FROM attempts WHERE paper_id = %s AND user_id = %s",
        (paper_id, user_id),
    ).fetchone()
    if existing:
        raise ValueError("This paper has already been submitted")

    rows = conn.execute(
        """
        SELECT
            q.id,
            q.correct_answer,
            q.explanation,
            t.subject,
            t.name AS topic
        FROM paper_questions pq
        JOIN papers p ON p.id = pq.paper_id
        JOIN questions q ON q.id = pq.question_id
        JOIN topics t ON t.id = q.topic_id
        WHERE pq.paper_id = %s AND p.user_id = %s
        ORDER BY pq.position
        """,
        (paper_id, user_id),
    ).fetchall()
    if len(rows) != TOTAL_QUESTIONS:
        raise ValueError(f"Paper is incomplete: expected {TOTAL_QUESTIONS} questions, found {len(rows)}")

    question_rows = [dict(row) for row in rows]
    score, details = score_questions(question_rows, answers)

    attempt = conn.execute(
        """
        INSERT INTO attempts (paper_id, user_id, score)
        VALUES (%s, %s, %s)
        RETURNING id, submitted_at
        """,
        (paper_id, user_id, score),
    ).fetchone()
    attempt_id = int(attempt["id"])

    detail_by_id = {int(d["id"]): d for d in details}
    for row in question_rows:
        qid = int(row["id"])
        detail = detail_by_id[qid]
        conn.execute(
            """
            INSERT INTO answers (attempt_id, question_id, selected_answer, is_correct)
            VALUES (%s, %s, %s, %s)
            """,
            (attempt_id, qid, detail["selected_answer"], detail["correct"]),
        )

        conn.execute(
            """
            UPDATE review_schedule
            SET completed_at = NOW()
            WHERE user_id = %s
              AND question_id = %s
              AND completed_at IS NULL
              AND due_date <= %s
            """,
            (user_id, qid, day),
        )

        if not detail["correct"]:
            for stage in (1, 3, 7):
                conn.execute(
                    """
                    INSERT INTO review_schedule
                        (user_id, question_id, review_stage, due_date)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, question_id, review_stage, due_date)
                    DO NOTHING
                    """,
                    (user_id, qid, stage, day + timedelta(days=stage)),
                )

    return {
        "attempt_id": attempt_id,
        "paper_id": paper_id,
        "score": score,
        "total": TOTAL_QUESTIONS,
        "percent": round(score / TOTAL_QUESTIONS * 100, 1),
        "submitted_at": attempt["submitted_at"].isoformat(),
        "details": details,
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


def due_reviews(conn: Connection, user_id: int, day: date | None = None) -> list[dict]:
    day = day or today_cn()
    rows = conn.execute(
        """
        SELECT
            rs.id,
            rs.review_stage,
            rs.due_date,
            q.id AS question_id,
            q.stem,
            t.subject,
            t.name AS topic
        FROM review_schedule rs
        JOIN questions q ON q.id = rs.question_id
        JOIN topics t ON t.id = q.topic_id
        WHERE rs.user_id = %s
          AND rs.completed_at IS NULL
          AND rs.due_date <= %s
        ORDER BY rs.due_date, rs.review_stage, q.id
        """,
        (user_id, day),
    ).fetchall()
    return [dict(row) for row in rows]


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
