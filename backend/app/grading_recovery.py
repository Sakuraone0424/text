"""Answers that have no score yet: why, and getting them graded again.

Nothing here invents a score. A pending answer stays pending (never a zero) until a real AI grading result is applied;
this module only finds what is stuck, puts the grading task back in the queue, and says so in plain Chinese.
"""

import json
from collections import Counter

from app import ai_jobs, grading_activity
from app.ai_jobs import content_sha, enqueue

# blocked for a reason that is not the answer's own: a later run may try again (budget came back, price verified, DeepSeek
# was busy, the worker restarted ...). The one block that stays is an answer too long to send.
FINAL_CODES = ("answer_too_long_for_automatic_grading",)
STATE_KEY = "grading_requeue_v1"
LATE_AFTER_MINUTES = 30

REASON_TEXT = {
    "waiting_to_be_queued": "等待进入判分队列",
    "queued_for_ai_grading": "排队等待 AI 判分",
    "ai_grading_in_progress": "AI 判分中",
    "ai_result_being_applied": "判分结果写入中",
    "reassessing": "结果不确定，正在自动复判一次",
    "uncertain_after_second_assessment": "复判后仍无法确定，暂不计分（不会记 0 分），可点「重新判分」",
    "ai_budget_exhausted": "本月 AI 额度已用完，额度恢复后继续判分",
    "ai_grading_stopped": "AI 判分已暂停",
    "answer_too_long_for_automatic_grading": "作答过长，无法自动判分",
    "photo_answers_not_supported": "照片作答暂不支持自动判分",
    "ai_grading_failed": "判分失败，会自动重试，也可点「重新判分」",
    "submit_auto_grading_disabled": "自动判分尚未开启",
    "ai_worker_disabled": "自动判分服务未启动",
    "owner_ai_settings_stopped": "AI 判分已暂停",
    "private_data_authorization_required": "尚未授权发送教材段落",
    "provider_authorization_required": "尚未授权调用 AI",
    "no_scoring_points": "这道题缺少评分要点，暂时无法判分",
}


def reason_text(code):
    return REASON_TEXT.get(code) or "待判分"


def _pending_rows(conn, user_id, paper_id=None, question_id=None):
    """Submitted answers without a score that nobody reported as a bad question."""
    return conn.execute(
        "SELECT p.id AS paper_id,t.id AS attempt_id,t.submitted_at,a.question_id,a.answer,i.position,i.snapshot "
        "FROM papers_v2 p JOIN attempts_v2 t ON t.paper_id=p.id JOIN answers_v2 a ON a.attempt_id=t.id "
        "JOIN paper_items_v2 i ON i.paper_id=p.id AND i.question_id=a.question_id "
        "WHERE p.user_id=%s AND a.points IS NULL AND t.submitted_at IS NOT NULL "
        "AND (%s::bigint IS NULL OR p.id=%s) AND (%s::bigint IS NULL OR a.question_id=%s) "
        "AND NOT EXISTS (SELECT 1 FROM question_reports r WHERE r.paper_id=p.id AND r.question_id=a.question_id) "
        "ORDER BY p.id DESC,i.position",
        (user_id, paper_id, paper_id, question_id, question_id),
    ).fetchall()


def _jobs_of(conn, user_id, attempt_id, question_id):
    return conn.execute(
        "SELECT id,job_key,state,error_code,attempts,created_at FROM ai_jobs WHERE user_id=%s AND kind='grading' "
        "AND job_key LIKE %s ORDER BY id DESC",
        (user_id, f"grade:{attempt_id}:{question_id}:%"),
    ).fetchall()


def has_rubric(snapshot):
    q = snapshot or {}
    if q.get("question_type") in ("single_choice",):
        return True
    return bool(q.get("rubric")) or q.get("question_type") == "fill_blank"


def report(conn, user_id, paper_ids=None):
    """Every unfinished answer of the given papers (default: all): number, type, why, the grading task's state and failure
    code. `by_reason` counts them by cause."""
    rows = []
    for r in _pending_rows(conn, user_id):
        if paper_ids and r["paper_id"] not in paper_ids:
            continue
        jobs = _jobs_of(conn, user_id, r["attempt_id"], r["question_id"])
        job = jobs[0] if jobs else None
        try:
            answer = json.loads(r["answer"])
        except (ValueError, TypeError):
            answer = {}
        photo = bool(isinstance(answer, dict) and answer.get("image_ids") and job is None)
        allowed, why = grading_activity.auto_queue_allowed(conn, user_id)
        if job is not None:
            job = dict(job, detail=None)
        reason = None
        if not has_rubric(r["snapshot"]):
            reason = "no_scoring_points"
        else:
            reason = grading_activity.pending_reason(None, job and dict(job, detail=None), photo, allowed, why)["pending_reason"]
        rows.append(dict(paper_id=r["paper_id"], position=r["position"], question_id=r["question_id"],
                         question_type=(r["snapshot"] or {}).get("question_type"), pending_reason=reason,
                         job_state=job["state"] if job else None, error_code=job["error_code"] if job else None,
                         jobs=len(jobs)))
    return dict(rows=rows, by_reason=dict(Counter(x["pending_reason"] for x in rows)),
                by_paper=dict(Counter(x["paper_id"] for x in rows)))


def regrade_key(conn, user_id, attempt_id, question_id, sha):
    n = len(_jobs_of(conn, user_id, attempt_id, question_id))
    return f"grade:{attempt_id}:{question_id}:{sha}:regrade{n}"


def requeue(conn, user_id, paper_id=None, question_id=None, final_too=False):
    """Put the grading of every unfinished answer (of one paper / one question, or all) back in the queue.
    - a blocked task goes back to 'retrying' with a fresh retry budget (its earlier tries were not the answer's fault);
    - a task that ended undecided (second assessment uncertain) is only asked again when `final_too` (the learner pressed
      the button, or the one-off recovery): a NEW task is written, the old one stays as the record;
    - no task at all: it is written now;
    - tasks already waiting or running are left alone.
    Returns counts."""
    from app import auto_grading

    done = Counter()
    for r in _pending_rows(conn, user_id, paper_id, question_id):
        q = r["snapshot"] or {}
        try:
            answer = json.loads(r["answer"])
        except (ValueError, TypeError):
            continue
        if not isinstance(answer, dict) or answer.get("image_ids") or not has_rubric(q):
            done["skipped"] += 1
            continue
        if auto_grading._length(answer) > auto_grading.MAX_ANSWER_CHARS:
            done["too_long"] += 1
            continue
        jobs = _jobs_of(conn, user_id, r["attempt_id"], r["question_id"])
        live = [j for j in jobs if j["state"] in ("pending", "retrying", "running", "succeeded")]
        if live:
            done["already_waiting"] += 1
            continue
        sha = content_sha(answer)
        blocked = [j for j in jobs if j["state"] == "blocked" and j["error_code"] not in FINAL_CODES]
        undecided = [j for j in blocked if j["error_code"] == "invalid_or_uncertain_result"]
        retryable = [j for j in blocked if j not in undecided]
        if retryable:
            ids = [j["id"] for j in retryable]
            conn.execute(
                "UPDATE ai_jobs SET state='retrying',next_retry=clock_timestamp(),transient_attempts=attempts,platform_streak=0,"
                "error_code=NULL,dispatched_at=NULL WHERE id=ANY(%s) AND lease_token IS NULL", (ids,))
            done["requeued"] += 1
        elif jobs and not final_too:
            done["left_undecided"] += 1
        else:
            key = regrade_key(conn, user_id, r["attempt_id"], r["question_id"], sha) if jobs else \
                f"grade:{r['attempt_id']}:{r['question_id']}:{sha}"
            enqueue(conn, user_id, key, "grading", auto_grading.grading_payload(
                r["paper_id"], dict(question_id=r["question_id"], attempt_id=r["attempt_id"]), q, answer))
            done["new_task"] += 1
    return dict(done)


def recover(conn, user_id, report_to=None):
    """The worker's housekeeping each run: grading tasks blocked for a reason that has passed go back to the queue; once, every
    stuck answer is asked again (including the ones that ended undecided). Grading is not held back by the peak rules."""
    from app import legacy_reuse

    counts = requeue(conn, user_id)
    if not legacy_reuse._state(conn, STATE_KEY):
        once = requeue(conn, user_id, final_too=True)
        legacy_reuse._set_state(conn, STATE_KEY, once)
        counts["recovery"] = once
    if report_to is not None and counts:
        report_to["grading_requeue"] = counts
    return counts


def late(conn, user_id, paper_id=None):
    """Unfinished answers whose paper was handed in more than half an hour ago: the learner is told."""
    rows = [r for r in _pending_rows(conn, user_id, paper_id)
            if conn.execute("SELECT clock_timestamp()-%s > make_interval(mins => %s) AS late",
                            (r["submitted_at"], LATE_AFTER_MINUTES)).fetchone()["late"]]
    return dict(count=len(rows), minutes=LATE_AFTER_MINUTES)


def summary(conn, uid):
    """For the progress page: how many answers wait for a score, how many for longer than half an hour, and why."""
    rows = _pending_rows(conn, uid)
    if not rows:
        return dict(pending=0, late=0, minutes=LATE_AFTER_MINUTES, by_reason=[], papers=[])
    detail = report(conn, uid)
    late_n = late(conn, uid)["count"]
    return dict(
        pending=len(rows), late=late_n, minutes=LATE_AFTER_MINUTES,
        by_reason=[dict(code=k, text=reason_text(k), count=v) for k, v in sorted(detail["by_reason"].items(), key=lambda kv: -kv[1])],
        papers=[dict(paper_id=k, count=v) for k, v in sorted(detail["by_paper"].items(), reverse=True)],
    )
