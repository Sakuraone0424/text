"""Conservative, automatic-only inventory for the new 60-question contract.

Only published, independently approved (current evidence version) 60q-score
questions can be counted. These totals are an UPPER BOUND: final selection
additionally filters previously seen stems, duplicate/cross-answer cues, FSRS
policy and within-paper coverage. Never promise a whole paper from these counts.
"""
from app.training_spec_v2 import SUBJECTS, COUNTS, POINTS


def report(conn, user_id):
    from app.auto_quality import ready

    candidates = conn.execute(
        "SELECT q.id,q.content,s.status FROM reviewed_questions q "
        "JOIN auto_question_states s ON s.question_id=q.id "
        "WHERE q.status='published' AND s.status='ready' "
        "ORDER BY q.id"
    ).fetchall()
    counts = {(sub, kind): 0 for sub in SUBJECTS for kind in COUNTS}
    rejected = {}
    for item in candidates:
        content = item["content"]
        sub = content.get("subject")
        kind = content.get("question_type")
        if sub not in SUBJECTS or kind not in COUNTS:
            rejected["wrong_subject_or_type"] = rejected.get("wrong_subject_or_type", 0) + 1
            continue
        if content.get("max_points") != POINTS[kind]:
            rejected["not_v2_scoring"] = rejected.get("not_v2_scoring", 0) + 1
            continue
        if not ready(conn, item["id"]):
            rejected["stale_or_incomplete_AI_verification"] = (
                rejected.get("stale_or_incomplete_AI_verification", 0) + 1
            )
            continue
        # Each published question must be new to this student, unless its
        # FSRS card is due. The final selector also checks stem similarity.
        used = conn.execute(
            "SELECT 1 FROM paper_items_v2 i JOIN papers_v2 p ON p.id=i.paper_id "
            "WHERE p.user_id=%s AND i.question_id=%s LIMIT 1",
            (user_id, item["id"]),
        ).fetchone()
        if used:
            due = conn.execute(
                "SELECT 1 FROM fsrs_cards f WHERE f.user_id=%s AND f.question_id=%s "
                "AND f.due<=clock_timestamp() LIMIT 1",
                (user_id, item["id"]),
            ).fetchone()
            if not due:
                rejected["previously_used_not_due"] = (
                    rejected.get("previously_used_not_due", 0) + 1
                )
                continue
        counts[(sub, kind)] += 1
    buckets = [
        {
            "subject": sub,
            "question_type": kind,
            "required": required,
            "eligible_upper_bound": counts[(sub, kind)],
            "shortage_at_least": max(0, required - counts[(sub, kind)]),
        }
        for sub in SUBJECTS for kind, required in COUNTS.items()
    ]
    return {
        "specification": "xiaohan-60q-150pt-v2",
        "available_to_prepare": all(b["shortage_at_least"] == 0 for b in buckets),
        "exact_selection_verified": False,
        "interpretation": (
            "These are strict current-evidence upper bounds, not a guaranteed "
            "60-question selection: cross-answer cues, topic spread and stem "
            "similarity may still cause 409 insufficient_reviewed_bank."
        ),
        "rejection_counts": rejected,
        "buckets": buckets,
    }
