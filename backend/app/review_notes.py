"""Versioned unsigned review opinions. Saving never counts as approval."""

from psycopg.types.json import Jsonb
from app.candidate_review import CHECKS


def latest(conn, key, reviewer):
    row = conn.execute(
        "SELECT * FROM reviewer_note_versions WHERE candidate_key=%s AND reviewer=%s ORDER BY version DESC LIMIT 1",
        (key, reviewer),
    ).fetchone()
    return row or dict(
        version=0, note="", checks={k: False for k in CHECKS}, state="editing"
    )


def save(conn, key, reviewer, data):
    packet = conn.execute(
        "SELECT * FROM candidate_review_packets WHERE candidate_key=%s FOR UPDATE",
        (key,),
    ).fetchone()
    if not packet:
        raise LookupError("Candidate not found")
    if conn.execute(
        "SELECT 1 FROM candidate_revision_links WHERE old_key=%s", (key,)
    ).fetchone():
        raise ValueError("Candidate superseded; existing opinion remains in history")
    current = latest(conn, key, reviewer)
    last = conn.execute(
        "SELECT id FROM candidate_human_decisions WHERE candidate_key=%s ORDER BY id DESC LIMIT 1",
        (key,),
    ).fetchone()
    decision_id = last["id"] if last else 0
    if (
        data["revision_sha256"] != packet["revision_sha256"]
        or data["expected_version"] != current["version"]
        or data["expected_last_decision_id"] != decision_id
    ):
        raise ValueError(
            "Opinion or decision changed on another device; reload and compare, never overwrite"
        )
    if (
        len(data["note"]) > 5000
        or set(data["checks"]) != set(CHECKS)
        or any(type(v) is not bool for v in data["checks"].values())
    ):
        raise ValueError("Invalid unsigned opinion")
    if (
        current["state"] == "editing"
        and current["note"] == data["note"]
        and current["checks"] == data["checks"]
    ):
        return current
    return conn.execute(
        "INSERT INTO reviewer_note_versions(reviewer,candidate_key,revision_sha256,version,note,checks,state,base_decision_id) VALUES (%s,%s,%s,%s,%s,%s,'editing',%s) RETURNING *",
        (
            reviewer,
            key,
            packet["revision_sha256"],
            current["version"] + 1,
            data["note"],
            Jsonb(data["checks"]),
            decision_id,
        ),
    ).fetchone()


def mark_submitted(conn, key, reviewer, packet, decision_id):
    prior = latest(conn, key, reviewer)
    conn.execute(
        "INSERT INTO reviewer_note_versions(reviewer,candidate_key,revision_sha256,version,note,checks,state,base_decision_id) VALUES (%s,%s,%s,%s,%s,%s,'submitted',%s)",
        (
            reviewer,
            key,
            packet["revision_sha256"],
            prior["version"] + 1,
            prior["note"],
            Jsonb(prior["checks"]),
            decision_id,
        ),
    )


def position(conn, reviewer):
    return conn.execute(
        "SELECT * FROM reviewer_positions WHERE reviewer=%s", (reviewer,)
    ).fetchone() or dict(candidate_key=None, subject_filter="", type_filter="")


def save_position(conn, reviewer, data):
    if (
        data["candidate_key"] is not None
        and not conn.execute(
            "SELECT 1 FROM candidate_review_packets WHERE candidate_key=%s",
            (data["candidate_key"],),
        ).fetchone()
    ):
        raise LookupError("Candidate not found")
    conn.execute(
        "INSERT INTO reviewer_positions(reviewer,candidate_key,subject_filter,type_filter) VALUES (%s,%s,%s,%s) ON CONFLICT(reviewer) DO UPDATE SET candidate_key=EXCLUDED.candidate_key,subject_filter=EXCLUDED.subject_filter,type_filter=EXCLUDED.type_filter,updated_at=clock_timestamp()",
        (reviewer, data["candidate_key"], data["subject_filter"], data["type_filter"]),
    )
    return position(conn, reviewer)
