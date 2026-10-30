"""Build real textbook science-review work without models or approval claims.

Every work row is a source-locked pending task: textual evidence must still
undergo actual scientific examination. Original graph nodes remain pending.
"""
from collections import Counter

from app.evidence_work import VERSION, build, persist
from app.source_policy import FILES, pinned_source_bytes

KINDS = frozenset(("unit_review", "chapter_alignment", "relationship"))


def eligible(work):
    if work.get("algorithm") != VERSION or work.get("kind") not in KINDS:
        return False
    if work.get("status") != "pending":
        return False
    evidence = work.get("evidence") or {}
    if work["kind"] == "unit_review":
        if evidence.get("source_role") != "textbook":
            return False
        ref = evidence.get("reference") or {}
    elif work["kind"] == "relationship":
        ref = evidence.get("reference") or {}
    else:
        # Chapter alignment work is built only from textbook chapter groups.
        # Its original source-unit IDs are checked by persist() on insertion.
        return True
    fid, digest = ref.get("file_id"), ref.get("source_sha256")
    return (
        fid in FILES
        and FILES[fid]["kind"] == "textbook"
        and pinned_source_bytes(fid, digest)
    )


def ensure(conn, builder=build, persister=persist, limit=12000):
    """Idempotent bounded worklist, not a scientific review or publication."""
    if type(limit) is not int or not 1 <= limit <= 20000:
        raise ValueError("science_pending_work_limit_invalid")
    tasks = [w for w in builder(conn)["work"] if eligible(w)]
    keys = [w["work_key"] for w in tasks]
    if len(set(keys)) != len(keys):
        raise ValueError("science_pending_duplicate_source_review")
    # The worklist is append-only: never overwrite past reviewer decisions.
    existing = {
        row["work_key"] for row in conn.execute(
            "SELECT work_key FROM evidence_review_work WHERE algorithm=%s "
            "AND kind=ANY(%s)",
            (VERSION, sorted(KINDS)),
        ).fetchall()
    }
    pending = [w for w in tasks if w["work_key"] not in existing]
    # Prioritize actual damaged source units before clean lexical candidates.
    pending.sort(key=lambda w: (-w["priority"], w["work_key"]))
    selected = pending[:limit]
    created = persister(conn, selected) if selected else 0
    kinds = Counter(w["kind"] for w in tasks)
    return {
        "version": VERSION,
        "source_bound_science_review_tasks": len(tasks),
        "tasks_by_kind": dict(sorted(kinds.items())),
        "existing_tasks": len(existing & set(keys)),
        "new_pending_tasks": created,
        "remaining_unseeded": len(pending) - created,
        "scientifically_approved_nodes": 0,
    }
