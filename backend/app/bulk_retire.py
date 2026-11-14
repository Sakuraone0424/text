"""Taking the unused part of the bank out of use, once, and putting it back on request.

A question that was never on a paper and never practised is withdrawn (soft: the row stays, with what it was before, so it can be restored).
A question that was used keeps everything: papers, reviews, mistake book and review cards are untouched. Waiting DeepSeek writing / review /
repair tasks are cancelled, past-exam items whose question was withdrawn are queued again (the stem stays, the answer and the scoring points
are written anew), and question writing is routed to Claude only.
"""

import json

import psycopg

from psycopg.types.json import Jsonb

from app import legacy_reuse
from app.training_spec_v5 import POINTS, SUBJECTS

REASON = "用户认为质量差，改由 Claude 重出"
STATE_REASON = "retired_by_user"
STATE_KEY = "retire_unused_v1"
TYPES = ("fill_blank", "term", "short_answer", "essay", "single_choice")
CANCELLED_KINDS = ("generation", "repair", "quality_review", "science_review", "answer_review")


def used_ids(conn):
    """Every question that was ever on a paper (the same stem counts: a paper holds a copy), practised, or answered into a review card."""
    rows = conn.execute(
        "SELECT i.question_id AS id FROM paper_items_v2 i "
        "UNION SELECT q.id FROM reviewed_questions q WHERE EXISTS(SELECT 1 FROM paper_items_v2 i WHERE i.snapshot->>'stem'=q.content->>'stem') "
        "UNION SELECT unnest(question_ids) FROM practice_sets "
        "UNION SELECT question_id FROM practice_answers pa JOIN practice_sets ps ON ps.id=pa.set_id "
        "UNION SELECT question_id FROM fsrs_cards").fetchall()
    return {r["id"] for r in rows}


def candidates(conn):
    """(id, subject, type, question status, state status, state reason) of every question that would be withdrawn."""
    used = list(used_ids(conn))
    return conn.execute(
        "SELECT q.id,q.content->>'subject' AS subject,q.content->>'question_type' AS kind,q.status AS qstatus,s.status AS sstatus,s.reason AS sreason "
        "FROM reviewed_questions q LEFT JOIN auto_question_states s ON s.question_id=q.id "
        "WHERE q.status IN ('published','draft') AND (s.status IS NULL OR s.status<>'retired') AND NOT (q.status='draft' AND s.question_id IS NULL) "
        "AND NOT (q.id=ANY(%s)) AND NOT EXISTS(SELECT 1 FROM question_retirements r WHERE r.question_id=q.id AND r.restored_at IS NULL) "
        "ORDER BY q.id", (used,)).fetchall()


def available(conn):
    """Questions that can be drawn today: published, verified ready, at the current scoring. {subject: {type: n}}."""
    out = {s: {k: 0 for k in TYPES} for s in SUBJECTS}
    points = "CASE q.content->>'question_type' " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in POINTS.items()) + " END"
    for r in conn.execute(
        "SELECT q.content->>'subject' AS subject,q.content->>'question_type' AS kind,count(*) AS n FROM reviewed_questions q "
        "JOIN auto_question_states s ON s.question_id=q.id WHERE q.status='published' AND s.status='ready' "
        f"AND (q.content->>'max_points')::numeric IS NOT DISTINCT FROM ({points})::numeric GROUP BY 1,2").fetchall():
        if r["subject"] in out and r["kind"] in out[r["subject"]]:
            out[r["subject"]][r["kind"]] = r["n"]
    return out


def _line(table):
    return ";".join(f"{s[:4]}:" + "/".join(f"{k}={table[s][k]}" for k in TYPES) for s in SUBJECTS)


def run_once(conn, report=None, log=print):
    """The one-time withdrawal. Returns the summary (also kept in the state, so the page and the report can show before / after), or None
    when it was done before. The number of questions about to be withdrawn is printed BEFORE anything changes."""
    if legacy_reuse._state(conn, STATE_KEY):
        return None
    before = available(conn)
    rows = candidates(conn)
    per = {}
    for r in rows:
        per.setdefault((r["subject"], r["kind"]), 0)
        per[(r["subject"], r["kind"])] += 1
    log(f"retire-unused: about to withdraw {len(rows)} unused questions " +
        ",".join(f"{s[:4]}.{k}={n}" for (s, k), n in sorted(per.items())) + f" | available before: {_line(before)}", flush=True)
    ids = [r["id"] for r in rows]
    if ids:
        conn.execute(
            "INSERT INTO question_retirements(question_id,reason,prev_question_status,prev_state_status,prev_state_reason) "
            "SELECT q.id,%s,q.status,s.status,s.reason FROM reviewed_questions q LEFT JOIN auto_question_states s ON s.question_id=q.id "
            "WHERE q.id=ANY(%s) ON CONFLICT(question_id) DO UPDATE SET restored_at=NULL,reason=EXCLUDED.reason,retired_at=clock_timestamp(),"
            "prev_question_status=EXCLUDED.prev_question_status,prev_state_status=EXCLUDED.prev_state_status,prev_state_reason=EXCLUDED.prev_state_reason",
            (REASON, ids))
        conn.execute(
            "UPDATE auto_question_states SET status='retired',reason=%s,updated_at=clock_timestamp() WHERE question_id=ANY(%s)", (STATE_REASON, ids))
        conn.execute("UPDATE reviewed_questions SET status='withdrawn' WHERE id=ANY(%s) AND status='published'", (ids,))
    cancelled = conn.execute(
        "UPDATE ai_jobs SET state='cancelled',error_code='retired_by_user',finished_at=clock_timestamp() "
        "WHERE state IN ('pending','retrying') AND kind=ANY(%s) AND lease_token IS NULL "
        "AND NOT EXISTS(SELECT 1 FROM external_leases l WHERE l.job_id=ai_jobs.id AND l.state='active' AND l.expires_at>clock_timestamp()) "
        "RETURNING id", (list(CANCELLED_KINDS),)).fetchall()
    cancelled_ids = [r["id"] for r in cancelled]
    # past-exam items: the stem stays, the answer and the scoring points of the withdrawn question are void; the item is queued again
    # (written by Claude, then reviewed). An item whose question was kept (it was used) is left as it is.
    requeued = conn.execute(
        "UPDATE past_exam_items SET status='imported',question_id=NULL,job_id=NULL,blocked_reason=NULL,blocked_detail=NULL,updated_at=clock_timestamp() "
        "WHERE (question_id=ANY(%s) AND (status='packet_created' OR (status='blocked' AND blocked_reason='review_failed'))) OR (job_id=ANY(%s) AND status='queued') RETURNING id",
        (ids, cancelled_ids)).fetchall()
    # question writing is Claude's only, with no fall back to DeepSeek (review and grading stay with DeepSeek for now)
    from app import external_executor

    external_executor.save_policy(conn, {"generation": "claude"})
    after = available(conn)
    summary = dict(withdrawn=len(ids), per={f"{s}|{k}": n for (s, k), n in per.items()}, jobs_cancelled=len(cancelled_ids),
                   past_exam_requeued=len(requeued), available_before=before, available_after=after, reason=REASON)
    legacy_reuse._set_state(conn, STATE_KEY, summary)
    if report is not None:
        report["retired_unused"] = f"{len(ids)}:jobs_cancelled={len(cancelled_ids)}:past_requeued={len(requeued)}:before[{_line(before)}]:after[{_line(after)}]"
    log("retire-unused: done " + json.dumps({k: v for k, v in summary.items() if k != "reason"}, ensure_ascii=False, separators=(",", ":")), flush=True)
    return summary


def summary(conn):
    """For the 模型与费用 page: what was withdrawn per subject and type and is still out, with the one-time before / after."""
    rows = conn.execute(
        "SELECT q.content->>'subject' AS subject,q.content->>'question_type' AS kind,count(*) AS n FROM question_retirements r "
        "JOIN reviewed_questions q ON q.id=r.question_id WHERE r.restored_at IS NULL GROUP BY 1,2 ORDER BY 1,2").fetchall()
    done = legacy_reuse._state(conn, STATE_KEY) or {}
    return dict(retired=[dict(subject=r["subject"], question_type=r["kind"], count=r["n"]) for r in rows],
                total=sum(r["n"] for r in rows), reason=REASON, done=bool(done),
                available_before=done.get("available_before"), available_after=done.get("available_after"),
                past_exam_requeued=done.get("past_exam_requeued"), jobs_cancelled=done.get("jobs_cancelled"))


def restore(conn, subject=None, question_type=None):
    """Put the withdrawn questions of one subject and type (or of all) back as they were. Returns how many came back."""
    rows = conn.execute(
        "SELECT r.question_id,r.prev_question_status,r.prev_state_status,r.prev_state_reason FROM question_retirements r "
        "JOIN reviewed_questions q ON q.id=r.question_id WHERE r.restored_at IS NULL "
        "AND (%s::text IS NULL OR q.content->>'subject'=%s) AND (%s::text IS NULL OR q.content->>'question_type'=%s) ORDER BY r.question_id",
        (subject, subject, question_type, question_type)).fetchall()
    restored = 0
    for r in rows:
        try:
            with conn.transaction():          # one question that cannot be put back (its verification no longer matches) stays out, the rest come back
                if r["prev_state_status"]:
                    # the state first (a published question needs its verification to be 'ready' again), then the question
                    conn.execute("UPDATE auto_question_states SET status=%s,reason=%s,updated_at=clock_timestamp() WHERE question_id=%s",
                                 (r["prev_state_status"], r["prev_state_reason"], r["question_id"]))
                if r["prev_question_status"] == "published":
                    conn.execute("UPDATE reviewed_questions SET status='published' WHERE id=%s AND status='withdrawn'", (r["question_id"],))
                conn.execute("UPDATE question_retirements SET restored_at=clock_timestamp() WHERE question_id=%s", (r["question_id"],))
            restored += 1
        except psycopg.Error:
            continue
    return restored
