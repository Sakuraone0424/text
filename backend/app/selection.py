"""Rank verified candidates from recorded evidence; no guessed exam frequencies."""

from collections import Counter


def pick(pool, count, chapter_counts, performance, frequencies, due):
    chosen = []
    pool = list(pool)
    while pool and len(chosen) < count:

        def key(row):
            q = row["content"]
            chapter = (q["subject"], q["evidence"][0]["chapter"])
            actual = performance.get(row["id"], {"answered": 0, "wrong": 0})
            weakness = actual["wrong"] / max(1, actual["answered"])
            frequency = sum(
                frequencies.get((q["subject"], tag), 0)
                for tag in q.get("knowledge_tags", [])
            )
            core = {"core": 1, "mechanism": 0.3, "application": 0.15}[
                q.get("difficulty", "core")
            ]
            return (
                row["id"] not in due,
                chapter_counts[chapter],
                -weakness,
                -frequency,
                -core,
                row["id"],
            )

        row = min(pool, key=key)
        pool.remove(row)
        chosen.append(row)
        chapter_counts[
            (row["content"]["subject"], row["content"]["evidence"][0]["chapter"])
        ] += 1
    return chosen


def signals(conn, user_id):
    performance = {
        r["question_id"]: r
        for r in conn.execute(
            "SELECT a.question_id,COUNT(*) AS answered,COUNT(*) FILTER(WHERE a.points<(i.snapshot->>'max_points')::int) AS wrong "
            "FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id "
            "JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id "
            "WHERE p.user_id=%s AND a.points IS NOT NULL GROUP BY a.question_id",
            (user_id,),
        ).fetchall()
    }
    from app.exams import frequencies

    frequency = Counter()
    for row in frequencies(conn):
        frequency[(row["subject"], row["tag"])] += row["appearances"]
    due = {
        r["question_id"]
        for r in conn.execute(
            "SELECT question_id FROM fsrs_cards WHERE user_id=%s AND due<=clock_timestamp()",
            (user_id,),
        ).fetchall()
    }
    return performance, frequency, due
