"""Database evidence snapshot plus reproducible weighted sampling within fixed quotas."""

from datetime import datetime
import json
import math
import random

from app.dynamic_model import Policy, build_context, instant, topics
from app.source_policy import current_source_sql


def policy_for(conn, user_id, as_of):
    row = conn.execute(
        "SELECT p.id,p.config FROM selection_policies p JOIN selection_policy_assignments a ON a.policy_id=p.id "
        "WHERE a.user_id=%s AND a.assigned_at<=%s AND p.created_at<=%s ORDER BY a.assigned_at DESC,a.id DESC LIMIT 1",
        (user_id, as_of, as_of),
    ).fetchone()
    return (
        (Policy.model_validate(row["config"]), row["id"]) if row else (Policy(), None)
    )


def snapshot(conn, user_id, questions, as_of=None):
    if as_of is None:
        as_of = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    as_of = instant(as_of)
    policy, policy_id = policy_for(conn, user_id, as_of)
    from app.exams import trusted_records

    records = trusted_records(conn, as_of)
    samples = conn.execute(
        "SELECT s.*,c.subject,u.verified AS valid,s.verified_at AS available_at FROM exam_samples s "
        "JOIN exam_scopes c ON c.scope_key=s.scope_key JOIN source_units u ON u.id=s.source_unit_id "
        "WHERE s.verified_at<=%s",
        (as_of,),
    ).fetchall()
    rows = conn.execute(
        "SELECT a.*,t.submitted_at,i.snapshot,p.user_id,COALESCE(g.graded_at,t.submitted_at) AS available_at "
        "FROM answers_v2 a JOIN attempts_v2 t ON t.id=a.attempt_id JOIN papers_v2 p ON p.id=t.paper_id "
        "JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id "
        "LEFT JOIN LATERAL (SELECT graded_at FROM grading_events WHERE attempt_id=a.attempt_id AND question_id=a.question_id ORDER BY id DESC LIMIT 1) g ON true "
        "WHERE p.user_id=%s AND a.points IS NOT NULL AND COALESCE(g.graded_at,t.submitted_at)<=%s",
        (user_id, as_of),
    ).fetchall()
    reflection_rows = conn.execute(
        "SELECT r.attempt_id,r.question_id,r.category FROM answer_reflections r JOIN attempts_v2 t ON t.id=r.attempt_id "
        "JOIN papers_v2 p ON p.id=t.paper_id WHERE p.user_id=%s AND r.created_at<=%s",
        (user_id, as_of),
    ).fetchall()
    errors = {}
    for r in reflection_rows:
        errors.setdefault((r["attempt_id"], r["question_id"]), set()).add(r["category"])
    observations = []
    for row in rows:
        q = row["snapshot"]
        try:
            response = json.loads(row["answer"])
        except ValueError:
            response = {"text": row["answer"]}
        if not isinstance(response, dict):
            response = {"text": str(response)}
        answered = bool(
            response.get("text", "").strip()
            or any(v.strip() for v in response.get("blanks", []))
            or response.get("image_ids")
        )
        feedback = row.get("feedback") or {}
        awarded = feedback.get("criterion_points", [])
        criteria = []
        for i, c in enumerate(q.get("rubric", [])):
            if i < len(awarded):
                criteria.append(
                    {
                        "points": awarded[i],
                        "max_points": c["points"],
                        "knowledge_tags": c.get("knowledge_tags", []),
                    }
                )
        observations.append(
            {
                "attempt_id": row["attempt_id"],
                "question_id": row["question_id"],
                "subject": q["subject"],
                "tags": topics(q),
                "points": row["points"],
                "max_points": q["max_points"],
                "question_type": q["question_type"],
                "criteria": criteria,
                "answered": answered,
                "available_at": row["available_at"],
                "error_categories": sorted(
                    set(feedback.get("error_categories", []))
                    | errors.get((row["attempt_id"], row["question_id"]), set())
                ),
                "cognitive_level": q.get("difficulty"),
            }
        )
    cards = []
    for row in conn.execute(
        "SELECT f.*,q.content FROM fsrs_cards f JOIN reviewed_questions q ON q.id=f.question_id WHERE f.user_id=%s",
        (user_id,),
    ).fetchall():
        current = row["card"]
        last = current.get("last_review")
        if last and instant(last) <= as_of:
            card = {**current, "due": row["due"].isoformat()}
        else:
            previous = conn.execute(
                "SELECT after_card FROM fsrs_events WHERE user_id=%s AND question_id=%s AND reviewed_at<=%s ORDER BY reviewed_at DESC,id DESC LIMIT 1",
                (user_id, row["question_id"], as_of),
            ).fetchone()
            if not previous:
                continue
            card = previous["after_card"]
        cards.append(
            {
                **card,
                "question_id": row["question_id"],
                "subject": row["content"]["subject"],
                "tags": topics(row["content"]),
            }
        )
    exposures = []
    for row in conn.execute(
        "SELECT i.snapshot,s.started_at FROM paper_items_v2 i JOIN training_sessions s ON s.paper_id=i.paper_id "
        "JOIN papers_v2 p ON p.id=i.paper_id WHERE p.user_id=%s AND s.started_at<=%s",
        (user_id, as_of),
    ).fetchall():
        exposures.append(
            {
                "subject": row["snapshot"]["subject"],
                "tags": topics(row["snapshot"]),
                "available_at": row["started_at"],
            }
        )
    dependencies = conn.execute(
        "WITH cutoff AS (SELECT %s::timestamptz AS at) SELECT k.* FROM knowledge_dependencies k JOIN source_units u ON u.id=k.source_unit_id JOIN source_documents d ON d.id=u.document_id WHERE u.verified AND "
        + current_source_sql(at_sql="(SELECT at FROM cutoff)")
        + " AND k.registered_at<=(SELECT at FROM cutoff)",
        (as_of,),
    ).fetchall()
    context = build_context(
        questions,
        records,
        samples,
        observations,
        cards,
        exposures,
        dependencies,
        as_of,
        policy,
    )
    context["policy_id"] = policy_id
    context["legacy_unmapped_answers"] = conn.execute(
        "SELECT COUNT(*) AS n FROM answers a JOIN attempts t ON t.id=a.attempt_id WHERE t.user_id=%s AND t.submitted_at<=%s",
        (user_id, as_of),
    ).fetchone()["n"]
    context["legacy_data_use"] = (
        "read_only_unmapped_history_not_used_as_verified_knowledge_observations"
    )
    due = {
        c["question_id"]
        for c in cards
        if c.get("last_review") and instant(c["due"]) <= as_of
    }
    return context, policy, due


def candidate_score(question, context, policy):
    tags = topics(question)
    signals = [context["topics"][question["subject"]][tag] for tag in tags]
    score = sum(s["priority"] for s in signals) / len(signals)
    counts = (
        context["exam_subjects"]
        .get(question["subject"], {})
        .get("cognitive_counts", {})
    )
    # No cognitive preference unless real annotated observations exist.
    cognitive_factor = 1.0
    kind = question["question_type"]
    typed = (
        context["exam_subjects"]
        .get(question["subject"], {})
        .get("question_type_counts", {})
    )
    if typed.get(kind, 0) >= 5:
        strongest = max(
            (
                s["exam"]["type_contributions"].get(kind, 0)
                for s in context["topics"][question["subject"]].values()
            ),
            default=0,
        )
        mass = sum(s["exam"]["type_contributions"].get(kind, 0) for s in signals) / len(
            signals
        )
        score *= (
            1
            - policy.type_adjustment
            + 2 * policy.type_adjustment * (mass + 1) / (strongest + 1)
        )
    if sum(counts.values()) >= 5:
        level = question.get("difficulty", "core")
        cognitive_factor = (
            1
            - policy.cognitive_adjustment
            + 2
            * policy.cognitive_adjustment
            * (counts.get(level, 0) + 1)
            / (max(counts.values()) + 1)
        )
        measured = [
            r["loss"]
            for s in signals
            for r in s["mastery"]["recent"]
            if r.get("cognitive_level") == level
        ]
        if len(measured) >= 3:
            cognitive_factor *= 1 + 0.15 * (sum(measured) / len(measured) - 0.5)
    return score * cognitive_factor


def pick(pool, count, chapter_counts, context, policy, seed, subject, kind, decisions):
    rng = random.Random(f"{seed}:{subject}:{kind}")
    pool = sorted(pool, key=lambda r: r["id"])
    chosen = []
    available_chapters = len({r["content"]["evidence"][0]["chapter"] for r in pool})
    for slot in range(count):
        if not pool:
            break
        weights = []
        details = []
        for row in pool:
            q = row["content"]
            chapter = q["evidence"][0]["chapter"]
            n = chapter_counts[(subject, chapter)]
            factor = (
                1 / (1 + policy.coverage_strength * n)
                if available_chapters > 1
                else 1.0
            )
            if available_chapters > 1 and n >= math.ceil(14 * policy.chapter_share):
                factor *= 0.25
            base = candidate_score(q, context, policy)
            weight = max(policy.exploration * 0.01, base * factor)
            weights.append(weight)
            details.append(
                {
                    "question_id": row["id"],
                    "base_priority": base,
                    "coverage_factor": factor,
                    "draw_weight": weight,
                }
            )
        target = rng.random() * sum(weights)
        cumulative = 0.0
        index = len(pool) - 1
        for i, weight in enumerate(weights):
            cumulative += weight
            if target < cumulative:
                index = i
                break
        row = pool.pop(index)
        chosen.append(row)
        chapter_counts[(subject, row["content"]["evidence"][0]["chapter"])] += 1
        decisions.append(
            {
                "subject": subject,
                "question_type": kind,
                "slot": slot + 1,
                "selected_id": row["id"],
                "candidates": details,
            }
        )
    return chosen


def summary(run):
    context = run["context"]
    selected = []
    for choice in run["decisions"]:
        audit = next(
            r for r in run["candidates"] if r["question_id"] == choice["selected_id"]
        )
        signals = {
            tag: context["topics"][audit["subject"]][tag]
            for tag in audit["knowledge_tags"]
        }
        selected.append(
            {
                "question_id": choice["selected_id"],
                "subject": audit["subject"],
                "question_type": audit["question_type"],
                "knowledge_tags": audit["knowledge_tags"],
                "chapter": audit["chapter"],
                "is_retest": audit["is_retest"],
                "review_knowledge_tags": audit["review_knowledge_tags"],
                "priority": sum(v["priority"] for v in signals.values()) / len(signals),
                "basis": {
                    tag: {
                        "exam_years": s["exam"]["years"],
                        "exam_record_ids": s["exam"]["record_ids"],
                        "exam_distinct_questions": s["exam"]["question_count"],
                        "complete_year_sample": s["exam"]["complete_year_sample"],
                        "exam_confidence": s["exam"]["confidence"],
                        "effective_samples": s["mastery"]["effective_samples"],
                        "recent_answer_samples": len(s["mastery"]["recent"]),
                        "recent_lost_samples": sum(
                            r["loss"] > 0 for r in s["mastery"]["recent"]
                        ),
                        "review_due": any(c["due"] for c in s["review"]["cards"]),
                        "review_near_due": any(
                            c["near_due"] for c in s["review"]["cards"]
                        ),
                        "review_card_ids": [
                            c["question_id"] for c in s["review"]["cards"]
                        ],
                        "effective_weights": s["effective_weights"],
                        "degraded": s["degraded"],
                    }
                    for tag, s in signals.items()
                },
            }
        )
    return {
        "algorithm_version": run["algorithm_version"],
        "seed": run["seed"],
        "as_of": run["as_of"].isoformat()
        if isinstance(run["as_of"], datetime)
        else run["as_of"],
        "stage": context["stage"],
        "subject_stages": context["subject_stages"],
        "subject_reference_weights": context["subject_reference_weights"],
        "policy_id": run["policy_id"],
        "reference_weights": context["reference_weights"],
        "meaning": "training_priority_not_exam_probability",
        "legacy_unmapped_answers": context.get("legacy_unmapped_answers", 0),
        "exam_samples": context["exam_subjects"],
        "selected": selected,
    }
