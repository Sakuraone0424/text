"""Short-lived leased automation tick, activated only with explicit API consent."""

import httpx
import psycopg
import json
import os
import sys
import signal
import threading
import time
from app.db import get_db
from app import cost_control, cny_billing
from app import ai_jobs, auto_quality, model_router, model_compatibility, auto_grading, auto_execution
from app import auto_knowledge, auto_generation, auto_exams, auto_insights, past_exam_import, legacy_reuse
from app import cny_prices, cost_reconcile
from app.harness_gateway import execute
from app.worker import tick, integrations_tick


from app.dispatch_codes import safe_dispatch_failure, _SAFE_VALIDATOR_ERRORS, _SAFE_QUALITY_CODES, _SAFE_PACKET_ERRORS  # noqa: F401  (re-exported)


WHERE = {"at": ""}      # the last thing the worker did (read by the run's error line: where a database error happened)
DB_ERRORS = []          # database errors that were contained in this run (one entry each, for the log)


def describe_db_error(exc):
    """A database error as one log token: kind, table, constraint and the conflicting key (names and values of OUR rows only; the
    server's own detail text, e.g. `Key (new_key)=(auto-repair-75-ab12) already exists.`)."""
    diag = getattr(exc, "diag", None)
    parts = [type(exc).__name__]
    for label, value in (("table", getattr(diag, "table_name", None)), ("constraint", getattr(diag, "constraint_name", None)),
                         ("detail", getattr(diag, "message_detail", None))):
        if value:
            parts.append(f"{label}={str(value)[:140]}")
    return ":".join(parts).replace(" ", "_")[:360]


def dispatch_completed(conn, scope_id=None, scope_kind="limited_trial", only_job_id=None):
    if scope_kind not in ("limited_trial", "review_campaign", "qa_automation"):
        raise ValueError("unknown_execution_scope")
    scope_filter = (
        " AND settings_snapshot->'" + scope_kind + "'->>'scope_id'=%s" if scope_id else ""
    )
    # only_job_id: an external executor's submit runs the checks of THAT one result right away (the same code as the worker's)
    rows = conn.execute(
        "SELECT * FROM ai_jobs WHERE state='succeeded' AND dispatched_at IS NULL"
        + scope_filter
        + (" AND id=%s" if only_job_id else "")
        + " ORDER BY id LIMIT 50 FOR UPDATE SKIP LOCKED",
        ((scope_id,) if scope_id else ()) + ((only_job_id,) if only_job_id else ()),
    ).fetchall()
    for job in rows:
        WHERE["at"] = f"dispatch:{job['kind']}#{job['id']}"
        try:
            with conn.transaction():
                if job["kind"] == "quality_review":
                    if auto_quality.accept_round(conn, job):
                        auto_quality.promote(conn, job["payload"]["question_id"])
                elif job["kind"] in ("science_review", "answer_review"):
                    # Retired two-round flow, kept only so old stored jobs can be read.
                    auto_quality.accept_round(conn, job)
                    phases = conn.execute(
                        "SELECT count(DISTINCT phase) AS n FROM auto_quality_events WHERE question_id=%s AND content_sha256=%s AND passed AND phase IN ('science','answer')",
                        (
                            job["payload"]["question_id"],
                            job["payload"]["content_sha256"],
                        ),
                    ).fetchone()["n"]
                    if phases == 2:
                        auto_quality.promote(conn, job["payload"]["question_id"])
                elif job["kind"] == "compatibility":
                    model_compatibility.accept(conn, job)
                elif job["kind"] == "repair" and "repair_of_job" in job["payload"]:
                    # the one repair of a draft that never became a question: same checks, same review
                    qid = auto_generation.accept_repair(conn, job)
                    auto_quality.schedule_checks(conn, job["user_id"], qid)
                    conn.execute("UPDATE auto_question_states SET revision_count=1 WHERE question_id=%s", (qid,))
                elif job["kind"] == "repair":
                    qid = auto_quality.repair(
                        conn,
                        job["payload"]["question_id"],
                        job["result"]["response"]["question"],
                        job["result"]["response"]["reason"],
                    )
                    auto_quality.schedule_checks(conn, job["user_id"], qid)
                elif job["kind"] in ("textbook_analysis", "knowledge_extraction"):
                    auto_knowledge.accept(conn, job)
                elif job["kind"] == "generation":
                    if job["payload"].get("origin") == past_exam_import.ORIGIN_PAST_EXAM:
                        new = past_exam_import.accept(conn, job)
                    else:
                        try:
                            new = auto_generation.accept(conn, job)
                        except auto_generation.RepairNeeded as fixable:
                            # one repair before the draft is thrown away; the receipt keeps what was wrong
                            if auto_generation.request_repair(conn, job["user_id"], job, fixable) is None:
                                raise
                            conn.execute("UPDATE ai_jobs SET result=jsonb_set(COALESCE(result,'{}'::jsonb),"
                                         "'{dispatch_failure}',%s) WHERE id=%s",
                                         (ai_jobs.Jsonb({"code": "repair_requested", "errors": [
                                             dict(field=e.get("field"), reason=e.get("code")) for e in fixable.errors]}), job["id"]))
                            conn.execute("UPDATE ai_jobs SET dispatched_at=clock_timestamp() WHERE id=%s", (job["id"],))
                            continue
                    auto_quality.schedule_checks(conn, job["user_id"], new)
                elif job["kind"] == "exam_analysis":
                    auto_exams.accept(conn, job)
                elif job["kind"] == "grading":
                    auto_grading.apply(conn, job)
                elif job["kind"] in ("explanation", "study_plan"):
                    auto_insights.accept(conn, job)
                else:
                    raise ValueError("unsupported_dispatch_kind")
        except psycopg.Error as exc:
            # one result that collides with a row that is already there (a unique key ...) is that task's problem alone: it is put aside with
            # the reason on record and the run goes on with the others (its own savepoint is already rolled back)
            detail = describe_db_error(exc)
            DB_ERRORS.append(f"dispatch:{job['kind']}#{job['id']}:{detail}")
            conn.execute("UPDATE ai_jobs SET state='blocked',error_code='database_conflict',result=jsonb_set(COALESCE(result,'{}'::jsonb),"
                         "'{dispatch_failure}',%s),dispatched_at=clock_timestamp() WHERE id=%s",
                         (ai_jobs.Jsonb({"code": "database_conflict", "detail": detail}), job["id"]))
            continue
        except (ValueError, KeyError, LookupError) as exc:
            if isinstance(exc, auto_quality.ReviewFormatError) and job["kind"] == "quality_review":
                # a malformed review answer says nothing about the question: ask for the review once more
                outcome = auto_quality.review_format_failure(conn, job)
                code = "review_reformat_requested" if outcome == "retry" else "review_format_failed"
                conn.execute("UPDATE ai_jobs SET result=jsonb_set(COALESCE(result,'{}'::jsonb),"
                             "'{dispatch_failure}',%s) WHERE id=%s", (ai_jobs.Jsonb({"code": code}), job["id"]))
                if outcome == "failed":
                    conn.execute("UPDATE ai_jobs SET state='blocked',error_code='invalid_or_uncertain_result' WHERE id=%s", (job["id"],))
                conn.execute("UPDATE ai_jobs SET dispatched_at=clock_timestamp() WHERE id=%s", (job["id"],))
                continue
            # The reason code (never the model's prose) is kept for every job: the progress page shows it.
            detail = safe_dispatch_failure(exc)
            conn.execute("UPDATE ai_jobs SET result=jsonb_set(COALESCE(result,'{}'::jsonb),"
                         "'{dispatch_failure}',%s) WHERE id=%s", (ai_jobs.Jsonb(detail), job["id"]))
            conn.execute(
                "UPDATE ai_jobs SET state='blocked',error_code='invalid_or_uncertain_result' WHERE id=%s",
                (job["id"],),
            )
            if job["kind"] in ("quality_review", "science_review", "answer_review"):
                conn.execute(
                    "UPDATE auto_question_states SET updated_at=clock_timestamp(),status='blocked',reason='invalid_or_uncertain_result' WHERE question_id=%s",
                    (job["payload"]["question_id"],),
                )
            elif job["kind"] == "generation" and job["payload"].get("origin") == past_exam_import.ORIGIN_PAST_EXAM:
                past_exam_import.mark_blocked(conn, job, safe_dispatch_failure(exc)["code"])
            elif job["kind"] == "repair" and "question_id" in job["payload"]:
                conn.execute(
                    "UPDATE auto_question_states SET updated_at=clock_timestamp(),status='blocked',reason='invalid_repair_result' WHERE question_id=%s",
                    (job["payload"]["question_id"],),
                )
            elif job["kind"] == "grading" and ":reassessment" not in job["job_key"]:
                payload = {
                    **job["payload"],
                    "instructions": auto_grading.REASSESS_INSTRUCTIONS + auto_grading.GRADING_INSTRUCTIONS,
                }
                ai_jobs.enqueue_rekeyed(
                    conn,
                    job["user_id"],
                    job["job_key"] + ":reassessment",
                    "grading",
                    payload,
                )
        conn.execute(
            "UPDATE ai_jobs SET dispatched_at=clock_timestamp() WHERE id=%s",
            (job["id"],),
        )


def plan(conn, uid, target=None, report=None):
    lap = time.monotonic()

    def mark(name):
        nonlocal lap
        if report is not None:
            lap = _lap(report, name, lap, "plan_steps")
        else:
            lap = time.monotonic()

    auto_quality.requeue_past_exam_duplicates(conn)   # before anything can retire them
    mark("dups")
    auto_quality.retire_stale(conn)
    mark("stale")
    auto_quality.retire_old_scoring(conn)      # (v4 -> v5 points were already converted, in place, by rescale_scoring below)
    mark("oldscale")
    auto_quality.retire_unusable_sources(conn)   # literature-list sources, missing figures, answers written in the stem
    mark("unusable")
    from app import memory

    done = memory.reclassify_existing(conn)        # once: review cards sorted by the new mistake rule (nothing deleted)
    if done and report is not None:
        report["reclassified"] = ";".join(f"{k}:{'/'.join(f'{s}{n}' for s, n in sorted(v.items())) or '-'}" for k, v in done.items())
    if report is not None:
        try:
            from app import chapters

            chapters.clear_cache()
            m = chapters.mapping_report(conn)
            report["chapter_map"] = (f"number_style={m['number_style_labels']}labels/{m['number_style_questions']}q;"
                                     f"unmapped={m['unmapped_labels']}labels/{m['unmapped_questions']}q;left={'|'.join(m['left'][:12])}")
        except Exception:  # noqa: BLE001  (a log nicety must never stop a run)
            pass
    auto_quality.discard_unfixable(conn)
    mark("retire")
    for row in conn.execute(
        "SELECT p.question_id FROM candidate_review_packets p LEFT JOIN auto_question_states s ON s.question_id=p.question_id WHERE NOT EXISTS(SELECT 1 FROM candidate_revision_links l WHERE l.old_key=p.candidate_key) AND (s.question_id IS NULL OR s.status='candidate') ORDER BY p.question_id LIMIT 10"
    ).fetchall():
        auto_quality.schedule_checks(conn, uid, row["question_id"])
    for row in conn.execute(
        "SELECT question_id FROM auto_question_states WHERE status='blocked' AND revision_count<%s AND reason<>ALL(%s) ORDER BY question_id LIMIT 3",
        (auto_quality.MAX_REPAIRS, list(auto_quality.UNREPAIRABLE) + list(auto_quality.NOT_QUESTION_FAULT)),
    ).fetchall():
        try:
            q, refs, style = auto_quality.evidence(conn, row["question_id"])
        except ValueError:
            continue
        if conn.execute(
            "SELECT 1 FROM ai_jobs WHERE kind='repair' AND payload->>'question_id'=%s AND state IN ('pending','running','retrying') LIMIT 1",
            (str(row["question_id"]),),
        ).fetchone():
            continue                   # a repair of this question is already on its way: not a second one
        payload = dict(
            question_id=row["question_id"],
            question=q,
            evidence=auto_execution.fit_evidence(refs),
            style=style,
            instructions="Repair the question using only the supplied actual excerpts. Return JSON {question: complete Question object, reason: detailed correction rationale}. No invented citation, no human approval. A new version will undergo the independent review again.",
            schema=auto_quality.Question.model_json_schema(),
        )
        repair_job = ai_jobs.enqueue_rekeyed(
            conn, uid, "repair:" + str(row["question_id"]), "repair", payload
        )
        conn.execute(
            "UPDATE auto_question_states SET updated_at=clock_timestamp(),status='repairing' WHERE question_id=%s AND EXISTS(SELECT 1 FROM ai_jobs j WHERE j.id=%s AND j.state IN ('pending','running','retrying'))",
            (row["question_id"], repair_job["id"]),
        )
    # Newest papers first: old papers whose leftover answers can never be graded (photos, too long) must not fill the slots.
    for row in conn.execute(
        "SELECT DISTINCT p.id FROM papers_v2 p JOIN attempts_v2 t ON t.paper_id=p.id JOIN answers_v2 a ON a.attempt_id=t.id "
        "WHERE p.user_id=%s AND a.points IS NULL AND t.submitted_at IS NOT NULL AND a.answer NOT LIKE '%%image_ids%%' "
        "AND NOT EXISTS (SELECT 1 FROM question_reports r WHERE r.paper_id=p.id AND r.question_id=a.question_id) "
        "ORDER BY p.id DESC LIMIT 30",
        (uid,),
    ).fetchall():
        auto_grading.schedule(conn, uid, row["id"])
    # Grading tasks blocked for a reason that has passed (budget, price, busy provider ...) go back; once, every stuck answer.
    try:
        from app import grading_recovery
        grading_recovery.recover(conn, uid, report)
    except Exception:  # noqa: BLE001  (housekeeping must never stop a run)
        pass
    # Past-exam questions need no corpus-wide sweep: each request carries only
    # the few textbook paragraphs relevant to that one question.
    mark("checks")
    legacy_reuse.plan(conn, uid)
    mark("legacy")
    # New questions are planned FIRST: past-exam term requests may take at most 20% of the tasks in flight, and that
    # share is counted against what was just queued
    if target:
        auto_generation.fill_queue(conn, uid, target)
    else:
        auto_generation.plan_gap(conn, uid)
    mark("fill")
    past_exam_import.plan(conn, uid, batch=max(past_exam_import.MAX_CONCURRENT_JOBS, target or 0))
    mark("past")


# Sizing: about 160 calls a day (60 questions at ~75% review pass rate = ~80 written
# + ~80 reviewed), only in off-peak hours, one worker run every 5 minutes.
#   weekday off-peak = 24h - (3h + 4h peak) = 17h -> 17 * 12 = 204 runs
#   160 calls * 1.25 (retries / failures) = 200 calls -> 200 / 204 ~ 1 call per run
# Default 2: a write is followed by its review in the same run, and the backlog left
# by a 4-hour peak window (up to ~48 runs) clears at twice that rate. Weekends and
# holidays (24h off-peak) have far more headroom. Raise it with AI_CALLS_PER_RUN.
DEFAULT_CALLS_PER_RUN = 2
MAX_CALLS_PER_RUN = 10


def calls_per_run(env=None):
    """Model calls one worker run may make: AI_CALLS_PER_RUN, default 3, 1..10.

    Budget, concurrency and retry limits still apply to every call; this only
    bounds how many a single cron run attempts.
    """
    raw = (os.environ if env is None else env).get("AI_CALLS_PER_RUN", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CALLS_PER_RUN
    return min(max(value, 1), MAX_CALLS_PER_RUN)


# A cron run every 5 minutes must never overlap the next one, so a whole run is held to 4 minutes.
# A model call takes about 45 s (150 s is the hard HTTP timeout), so a new call is only started while
# there is still room for one more typical call before the deadline.
SHUTDOWN = threading.Event()   # set by SIGTERM (a deploy restarts the worker): claim nothing more
SHUTDOWN_GRACE_SECONDS = 30    # calls whose request is already out get this long to bring their result back
DEFAULT_RUN_SECONDS = 270      # a run's hard end: 4 min 30 s after it started (the next cron start is at 5 min)
EXPECTED_CALL_SECONDS = 60
SAFETY_SECONDS = 15
DEFAULT_START_CUTOFF_SECONDS = 210   # no new call is started after 3 min 30 s
MAX_BATCHES = 8                      # a safety stop: a run never loops more often than this
WORKER_RUN_LOCK = 770000001          # advisory lock: one worker run at a time


def start_cutoff_seconds(env=None):
    """AI_START_CUTOFF_SECONDS: after this long since the run began no new model call is started (default 210 = 3 min 30 s,
    between 30 and 240). Calls already out may bring their result back until run_seconds()."""
    raw = (os.environ if env is None else env).get("AI_START_CUTOFF_SECONDS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_START_CUTOFF_SECONDS
    return min(max(value, 30), 240)


def run_seconds(env=None):
    """AI_RUN_SECONDS: the run's hard end, default 270 (4 min 30 s), between 60 and 270. Calls still out then are given up on:
    their cost stays an estimate and their jobs are queued again, so a run is always over before the next cron start."""
    raw = (os.environ if env is None else env).get("AI_RUN_SECONDS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RUN_SECONDS
    return min(max(value, 60), 270)


class WorkerInterrupted(Exception):
    """A call was still out when the run had to end."""


class CallPool:
    """Up to `workers` model calls at once, at most `limit` calls and `seconds` seconds in all.

    Each worker thread claims (one claim at a time: ai_jobs.claim holds a database lock, so every claim
    sees the reservations of the claims before it and the budget stays exact), runs the call, hands the
    finished result on (so its review can be claimed by the next call), and loops. Nothing here decides
    whether a job may run: that stays in ai_jobs.claim.
    """

    def __init__(self, get_db_, executor, workers, limit, seconds, clock=time.monotonic, start_cutoff=None):
        self.get_db, self.executor = get_db_, executor
        self.workers, self.limit, self.seconds, self.clock = max(1, workers), limit, seconds, clock
        self.start_cutoff = start_cutoff   # seconds after the pool starts beyond which no NEW call begins (None: the old room rule)
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.active = 0           # workers between starting to claim and handing their result on
        self.running_jobs = {}    # job id -> job, while its model call is out
        self.handed_on = 0        # results handed on so far (each may have created new jobs)
        self.started = 0           # calls begun (a claim attempt counts until it turns out to be empty)
        self.calls = 0             # calls actually made
        self.in_flight = 0
        self.peak = 0              # most calls running at the same moment
        self.longest = 0.0         # slowest call of this run, seconds
        self.claimed = []
        self.stop = False
        self.stopped_because = None
        self.last_claim = {}
        self.errors = []
        self.deadline = None
        self.t0 = None
        self.overran = False

    def _room_for_another_call(self):
        """A call starts only if it can be expected to END before the deadline (the slowest call of this run so far, or the
        usual length, plus a safety margin) and, with a start cutoff, only before that. A call that still does not come back
        by the deadline is given up on: estimated at the median of recent calls, and queued again."""
        expected = max(EXPECTED_CALL_SECONDS, self.longest)
        fits = self.clock() + expected <= self.deadline - SAFETY_SECONDS
        if self.start_cutoff is not None:
            return fits and self.clock() - self.t0 < self.start_cutoff
        return fits

    def _worker(self):
        while True:
            with self.cond:
                if self.stop or self.started >= self.limit or SHUTDOWN.is_set():
                    return
                if not self._room_for_another_call():
                    self.stopped_because = self.stopped_because or "run_time_budget"
                    self.stop = True
                    self.cond.notify_all()
                    return
                self.started += 1
                self.active += 1
                seen = self.handed_on
            began = self.clock()
            try:
                did = ai_jobs.run_once(self.get_db, self._execute)
            except Exception as exc:  # database trouble: stop the whole run and report it
                with self.cond:
                    self.errors.append(exc)
                    self.stop = True
                    self.active -= 1
                    self.cond.notify_all()
                return
            if not did:
                with self.cond:
                    self.started -= 1
                    self.active -= 1
                    self.last_claim = dict(ai_jobs.LAST_CLAIM)
                    self.stopped_because = (ai_jobs.LAST_CLAIM.get("outcome") or "no_claimable_job") + (
                        ":" + ai_jobs.LAST_CLAIM["scope"] if ai_jobs.LAST_CLAIM.get("scope") else "")
                    self.cond.notify_all()
                    # Nothing to claim right now. While other calls are still running their results may
                    # create new jobs (a review of what was just written), so wait for them; only when
                    # nobody is working any more is the queue really empty.
                    while self.active > 0 and not self.stop and self.clock() < self.deadline:
                        self.cond.wait(timeout=1.0)
                    if self.active > 0 or self.stop:
                        return
                    retry = self.started < self.limit and self.handed_on > seen and self._room_for_another_call()
                    if not retry:
                        self.stop = True
                        self.cond.notify_all()
                        return
                continue
            with self.cond:
                self.calls += 1
                self.longest = max(self.longest, self.clock() - began)
                self.claimed.append(ai_jobs.LAST_CLAIM.get("job_id"))
            try:
                with self.get_db() as conn:
                    # Hand each finished result on at once so its review jobs can be claimed by a later call.
                    dispatch_completed(conn)
            except Exception as exc:
                with self.cond:
                    self.errors.append(exc)
                    self.stop = True
                    self.active -= 1
                    self.cond.notify_all()
                return
            with self.cond:
                self.active -= 1
                self.handed_on += 1  # a result was handed on: an idle worker may find a new job
                self.cond.notify_all()

    def _execute(self, job):
        """The model call itself; counts how many are running at the same moment."""
        if SHUTDOWN.is_set():
            raise ai_jobs.Released()  # nothing was sent yet: hand the job back at once, at no cost
        with self.lock:
            self.running_jobs[job["id"]] = job
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            return self.executor(job)
        finally:
            with self.lock:
                self.in_flight -= 1
                self.running_jobs.pop(job["id"], None)

    def run(self):
        self.t0 = self.clock()
        self.deadline = self.t0 + self.seconds
        if self.workers == 1 and self.start_cutoff is None:
            self._worker()  # one call at a time: no thread needed
            return self
        # (With a start cutoff the run has a hard end: the call is watched from outside so a stuck one can be given up on.)
        threads = [threading.Thread(target=self._worker, name=f"call-{i}", daemon=True) for i in range(self.workers)]
        for t in threads:
            t.start()
        grace_until = None
        while any(t.is_alive() for t in threads) and self.clock() < self.deadline:
            if SHUTDOWN.is_set():
                grace_until = grace_until or self.clock() + SHUTDOWN_GRACE_SECONDS
                if self.clock() >= grace_until:
                    break
            for t in threads:
                t.join(0.2)
        self.overran = any(t.is_alive() for t in threads)
        if self.overran and self.running_jobs:
            self._give_up_on_running()
        return self

    def _give_up_on_running(self):
        """A call still out at the deadline or after the shutdown grace: free its slot now. Its cost stays an estimate
        (the median of recent calls) until reconciliation; the job is queued again."""
        with self.lock:
            leftover = list(self.running_jobs.values())
        for job in leftover:
            try:
                with self.get_db() as conn:
                    ai_jobs.finish(conn, job, error=WorkerInterrupted())
            except Exception:  # noqa: BLE001  (already finished by its own thread, or the lease moved on)
                pass


def queue_snapshot(conn):
    """Read-only picture of the queue: counts, and the job a claim would look at first."""
    states = {
        r["state"]: r["n"]
        for r in conn.execute("SELECT state,count(*) AS n FROM ai_jobs GROUP BY state").fetchall()
    }
    held = cost_control.held_back_kinds()
    head = conn.execute(
        "SELECT id,kind,state,attempts,error_code,"
        "array_to_string(ARRAY(SELECT k FROM unnest(ARRAY['limited_trial','review_campaign','qa_automation']) k "
        "WHERE settings_snapshot ? k),',') AS scope "
        "FROM ai_jobs WHERE state IN ('pending','retrying') AND next_retry<=clock_timestamp() AND NOT kind=ANY(%s) "
        "ORDER BY " + ai_jobs.CLAIM_ORDER_SQL + " LIMIT 1",
        (held,),
    ).fetchone()
    return states, held, head


def _fmt(value):
    return "-" if value in (None, "") else str(value).replace(" ", "_")


def rescale_scoring(conn, report):
    """Move every question still on the previous point scale to the new one, in place (nothing is retired for it).

    It runs in its OWN savepoint, ahead of and apart from the planning pass: planning raises ValueError for reasons of its own and the
    worker swallows them, which would roll back everything done in the same transaction (so nothing was ever converted)."""
    from app import scoring_rescale

    t = time.monotonic()
    try:
        with conn.transaction():
            done = scoring_rescale.run(conn)
        if done.get("rescaled") or done.get("failed"):
            report["rescale"] = ";".join(f"{k}={v}" for k, v in sorted(done.items()) if k in ("rescaled", "invalidated")) + (
                ";failed=" + ",".join(f"{k}:{n}" for k, n in sorted(done["failed"].items())) if done.get("failed") else "")
    except Exception as exc:  # noqa: BLE001  (a log line, never a stopped run)
        report["rescale_error"] = type(exc).__name__ + ":" + str(exc)[:80].replace(" ", "_")
    _lap(report, "rescale", t, "plan_detail")


def _plan_queue(conn, report, users, private, first):
    """Preparation of a run, contained: whatever goes wrong in it is written to the log (route and database detail) and the run still goes on to
    claim and execute the tasks that are already queued. Every planning route has its own savepoint, so an error in one route costs only that route; this outer one only covers what is outside the routes."""
    try:
        with conn.transaction():
            _plan_queue_inner(conn, report, users, private, first)
    except (ValueError, psycopg.Error) as exc:
        WHERE["at"] = WHERE.get("at") or "plan"
        text = describe_db_error(exc) if isinstance(exc, psycopg.Error) else type(exc).__name__ + ":" + str(exc)[:80].replace(" ", "_")
        report["plan_error"] = (report.get("plan_error", "") + " | " if report.get("plan_error") else "") + "queue=" + text


def _plan_queue_inner(conn, report, users, private, first):
    """Fill the queue (so every call of a batch has a job) and put back what should be tried again."""
    rescale_scoring(conn, report)
    t = time.monotonic()
    for user in users:
        if first:
            try:
                with conn.transaction():
                    upgrade = model_compatibility.auto_upgrade(conn, user["user_id"])
                report["upgrade"] = upgrade["status"] + (
                    ":" + ",".join(f"{c['model']}={c['reason']}" for c in upgrade.get("candidates", []))
                    if upgrade.get("candidates") else ":" + upgrade["model"] if upgrade.get("model") else ""
                )
            except (ValueError, psycopg.Error) as exc:
                report["upgrade"] = "error:" + (describe_db_error(exc) if isinstance(exc, psycopg.Error) else type(exc).__name__)
            t = _lap(report, "upgrade", t, "plan_detail")
        if (
            not user["stopped"]
            and private
            and os.environ.get("AI_QUEUE_AUTOPLAN") == "1"
        ):
            first_new = conn.execute("SELECT COALESCE(max(id),0) AS m FROM ai_jobs").fetchone()["m"]

            def step(name, fn):
                """One planning route in its own savepoint: whatever it raises (a rule error or a database error such as a unique key) undoes
                only that route, is written to the log with its route name and the key that collided, and the other routes carry on."""
                WHERE["at"] = "plan:" + name
                try:
                    with conn.transaction():
                        fn()
                except (ValueError, psycopg.Error) as exc:
                    detail = getattr(exc, "detail", None)
                    if isinstance(exc, psycopg.Error):
                        text = describe_db_error(exc)
                    else:
                        text = type(exc).__name__ + ":" + str(exc)[:80].replace(" ", "_")
                    if detail:
                        text += ":" + json.dumps(detail, ensure_ascii=False, separators=(",", ":"))[:400]
                    report["plan_error"] = (report.get("plan_error", "") + " | " if report.get("plan_error") else "") + name + "=" + text

            # grading housekeeping first and on its own: a failure further down the planning pass must not keep stuck gradings stuck
            def grading_housekeeping():
                from app import grading_recovery

                grading_recovery.recover(conn, user["user_id"], report)

            step("grading_recovery", grading_housekeeping)
            # queue enough work that every call this batch can make has a job to claim
            step("plan", lambda: plan(conn, user["user_id"], target=2 * calls_per_run(), report=report))
            t = _lap(report, "plan", t, "plan_detail")
            if os.environ.get("AI_FULL_CORPUS_ENABLED") == "1":
                step("knowledge", lambda: auto_knowledge.plan_small_batch(conn, user["user_id"]))
                step("generation", lambda: auto_generation.plan(conn, user["user_id"]))
                step("exams", lambda: auto_exams.plan(conn, user["user_id"]))
            step("insights", lambda: auto_insights.plan(conn, user["user_id"]))
            report["planned"] = report.get("planned", 0) + conn.execute(
                "SELECT count(*) AS n FROM ai_jobs WHERE id>%s", (first_new,)).fetchone()["n"]
            if ai_jobs.RESOLVED_CONFLICTS:
                report["key_conflicts_resolved"] = len(ai_jobs.RESOLVED_CONFLICTS)
                report["key_conflicts"] = ai_jobs.RESOLVED_CONFLICTS[-3:]
                ai_jobs.RESOLVED_CONFLICTS.clear()
            # Say in the log WHY nothing was planned (no gap at all, or a gap that no request could be written for).
            try:
                t = time.monotonic()
                diagnosis = auto_generation.gap_diagnosis(conn, user["user_id"])
                t = _lap(report, "gap", t, "plan_detail")
                report["gap"], report["short_buckets"] = diagnosis["gap"], diagnosis["buckets"]
                report["avail"], report["gap_detail"] = diagnosis["avail"], diagnosis["gap_detail"]
                report["on_way"] = diagnosis["on_way"]
                if diagnosis["gap"]:
                    report["stuck"] = auto_generation.short_bucket_report(conn, user["user_id"])   # per short bucket: first blocked step + funnel
                    t = _lap(report, "stuck", t, "plan_detail")       # queued/running write+review+repair jobs per bucket
                if cost_control.is_peak() and diagnosis["gap"]:
                    # computed in peak hours too; only the calls themselves wait
                    report["plan_note"] = f"高峰暂停，低峰时将规划{diagnosis['gap']}个"
                elif diagnosis["gap"] and not report.get("planned"):
                    report["plan_note"] = "gap_but_no_request_could_be_written"
            except Exception:  # noqa: BLE001  (a log nicety must never stop a run)
                pass
    # Retry budget/stopped jobs only when user explicitly enabled new attempts.
    conn.execute(
        "UPDATE ai_jobs j SET state='retrying' FROM ai_settings s WHERE s.user_id=j.user_id AND NOT s.stopped AND j.state='blocked' "
        "AND (j.error_code IN ('budget_exceeded','automation_stopped','verified_cny_price_required') OR j.error_code LIKE ANY(%s))",
        ([c + "%" for c in ai_jobs.MODEL_GONE_CODES],),
    )
    # Jobs an earlier run blocked only because DeepSeek was busy or unreachable go back to the queue.
    report["requeued_busy"] = report.get("requeued_busy", 0) + ai_jobs.requeue_platform_blocked(conn)


def batch_exit_reason(pool, limit, busy_calls):
    """Why a run should stop after this batch (None: carry on with the next one). Codes only."""
    if SHUTDOWN.is_set():
        return "shutdown"
    if pool.overran:
        return "overran"
    if pool.calls and busy_calls * 2 > pool.calls:
        return "platform_busy"                       # DeepSeek answered 503/429 to most of this batch
    if pool.calls < limit:
        code = str(pool.stopped_because or "no_claimable_job")
        if code == "run_time_budget":
            return "time_cutoff"
        if code.startswith("no_claimable_job"):
            held = str(pool.last_claim.get("held_back") or "")
            return "daily_quota" if held == "daily_quota" else "peak_hours" if held else "queue_empty"
        if "budget" in code:
            return "budget_exhausted"
        if code.startswith("blocked:automation_stopped"):
            return "automation_stopped"
        return "stopped:" + code.split(":")[0]
    return None


def run(report):
    """One worker run. Fills `report` (ids, counts and codes only; no secrets, no textbook text)."""
    run_began = time.monotonic()
    raw = os.environ.get("AI_AUTOMATION_ENABLED")
    report["enabled"] = "yes" if raw == "1" else "NO(value=" + repr(raw) + ")"
    report["autoplan"] = "1" if os.environ.get("AI_QUEUE_AUTOPLAN") == "1" else "0"
    report["bank_target"] = cost_control.bank_target() or "-"
    report["planned"] = 0
    report["batches"] = 0
    os.environ["QUIZ_SERVICE_ROLE"] = (
        "worker"  # Short jobs never own the API backup volume.
    )
    if raw != "1":
        report["exit_reason"] = "disabled"
        return
    from app.deployment_target import verify_isolated_target

    verify_isolated_target()
    report["guard"] = "ok"
    if not os.environ.get("QUIZ_DB_SCHEMA", "").startswith("source_staging_"):
        raise PermissionError("Explicit isolated source schema required")
    model_router.authorized()
    report["authorized"] = "ok"
    # Only one run at a time, whatever starts it: if the previous run is somehow still going, this one leaves at once.
    # (A transaction-level lock on a connection that stays open for the whole run; it is dropped when the run ends or dies.)
    with get_db() as lock_conn:
        got = lock_conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS got", (WORKER_RUN_LOCK,)).fetchone()["got"]
        if not got:
            report["skipped"] = "already_running"
            report["exit_reason"] = "already_running"
            return
        _locked_run(report, run_began)


def _lap(report, name, began, key="pre_detail"):
    """Seconds since `began` into report[key] ('tick0.3,queue2.1,...'): where the time before the first call goes."""
    now = time.monotonic()
    report[key] = (report.get(key, "") + "," if report.get(key) else "") + f"{name}{now - began:.1f}"
    return now


def _locked_run(report, run_began):
    lap = time.monotonic()
    tick()
    integrations_tick()  # Prepare at the scheduled tick before any long model task.
    lap = _lap(report, "tick", lap)
    private = os.environ.get("DEEPSEEK_PRIVATE_DATA_AUTHORIZED") == "1"
    with get_db() as conn:
        report["since"] = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
        # calls a previous (restarted) worker process still holds would keep the concurrency slots busy for minutes
        report["reclaimed"] = ai_jobs.reclaim_orphans(conn)
        dispatch_completed(conn)
        users = conn.execute("SELECT * FROM ai_settings").fetchall()
        report["users"] = len(users)
        if users:
            u = users[0]
            report.update(stopped=u["stopped"], max_concurrency=u["max_concurrency"],
                          model=u["model_id"], effort=u["effort"], auto_upgrade=u["auto_upgrade"])
        # Public-page refresh is not a paid inference, never uploads private material.
        if (
            users
            and not conn.execute(
                "SELECT 1 FROM ai_cny_prices WHERE verified_at>clock_timestamp()-interval '12 hours' LIMIT 1"
            ).fetchone()
        ):
            try:
                from app.cny_prices import refresh

                with conn.transaction():
                    refresh(conn)
                report["price_refresh"] = "ok"
                cny_prices.remember_success(conn)
            except (ValueError, httpx.HTTPError) as exc:
                code = cny_prices.failure_code(exc)
                report["price_refresh"] = "failed:" + code        # (a fixed code, never page text)
                cny_prices.remember_failure(conn, code)
                # Missing/expired/conflicting prices block new calls at claim().
        if (
            any(u["auto_discover"] for u in users)
            and model_router.catalog(conn)["stale"]
        ):
            model_router.refresh(conn)
        if users:
            report["price"] = cny_billing.price_state(conn, users[0]["model_id"])
        lap = _lap(report, "prices", lap)
        _plan_queue(conn, report, users, private, True)
        lap = _lap(report, "queue", lap)
        states, held, head = queue_snapshot(conn)
        report["queue"] = ",".join(f"{k}={v}" for k, v in sorted(states.items())) or "empty"
        report["peak_held"] = ",".join(held) or "-"
        report["head"] = (
            f"{head['id']}:{head['kind']}:{head['state']}:scope={_fmt(head['scope'])}:err={_fmt(head['error_code'])}"
            if head else "-"
        )
    if users:
        # hourly, and at Beijing midnight: correct the ledger against DeepSeek's real balance (never blocks writing)
        with get_db() as conn:
            report["balance"] = cost_reconcile.reconcile_if_due(conn, users[0]["user_id"])
    limit = calls_per_run()
    configured = min(report.get("max_concurrency") or 1, limit)
    with get_db() as conn:
        workers, adaptive = adaptive_concurrency(conn, configured)
    report["adaptive"] = adaptive
    report["concurrency"] = workers
    cutoff, hard = start_cutoff_seconds(), run_seconds()
    report["pre_s"] = f"{time.monotonic() - run_began:.1f}"     # everything before the first call (planning, prices, balance)
    if DB_ERRORS:
        report["db_errors"] = " ; ".join(DB_ERRORS[-3:])
        del DB_ERRORS[:]
    calls, ran, claimed, peak, overran = 0, 0, [], 0, False
    exit_reason, pool = None, None
    try:
        # Batch after batch (AI_CALLS_PER_RUN calls each) while the run is young enough to start another and there is
        # something to do. The first batch always starts; no batch starts after the cutoff; calls still out at `hard` are
        # given up on and queued again, so the run is over before the next cron start.
        while True:
            elapsed = time.monotonic() - run_began
            if ran or pool is not None:
                if elapsed >= cutoff:
                    exit_reason = "time_cutoff"
                    break
                with get_db() as conn:      # the same preparation as before the first batch
                    dispatch_completed(conn)
                    _plan_queue(conn, report, conn.execute("SELECT * FROM ai_settings").fetchall(), private, False)
            with get_db() as conn:
                batch_since = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
            pool = CallPool(get_db, execute, workers, limit, hard - elapsed, start_cutoff=max(cutoff - elapsed, 0)).run()
            calls += pool.calls
            claimed += list(pool.claimed)
            peak = max(peak, pool.peak)
            overran = overran or pool.overran
            ran += 1 if pool.calls else 0
            if pool.errors:
                raise pool.errors[0]
            with get_db() as conn:
                busy_503, busy_429, _ = platform_trouble(conn, batch_since)
            exit_reason = batch_exit_reason(pool, limit, busy_503 + busy_429)
            if exit_reason:
                break
            if ran >= MAX_BATCHES:
                exit_reason = "max_batches"
                break
    finally:
        report["pool_end_s"] = f"{time.monotonic() - run_began:.1f}"   # when the last call batch was over, since the run began
        report["calls"] = calls
        report["batches"] = ran
        report["exit_reason"] = exit_reason or "error"
        report["parallel_peak"] = peak
        report["claimed"] = ",".join(str(c) for c in claimed) or "-"
        if overran:
            report["overran"] = "yes"  # a call was still running at the deadline; its lease expires and it is retried
    with get_db() as conn:
        report["http_503"], report["http_429"], report["timeouts"] = platform_trouble(conn, report["since"])
    # Why the last batch stopped early (set by ai_jobs.claim; ids, kinds and codes only).
    if pool is not None and pool.calls < limit:
        report["stopped_because"] = _fmt(pool.stopped_because)
        for key in ("job_id", "kind", "running", "limit", "held_back"):
            if key in pool.last_claim:
                report["why_" + key] = _fmt(pool.last_claim[key])
    with get_db() as conn:
        dispatch_completed(conn)
        spent = conn.execute(
            "SELECT COALESCE(sum(amount_cny),0) AS n FROM ai_cny_events WHERE kind='usage_estimate' AND created_at>=%s",
            (report["since"],),
        ).fetchone()["n"]
        report["cost_cny"] = f"{spent:.4f}"
        if users:
            reserved, other = cost_control.month_split(conn, users[0]["user_id"])
            cap, reserve = cost_control.caps()
            report["month_counted_cny"] = f"{reserved + other:.2f}"
            report["cap_cny"] = f"{cap}/{reserve}"
    integrations_tick()


def adaptive_concurrency(conn, configured):
    """How many calls to run at once this run: the setting, unless the platform has been struggling.

    More than half of the calls of the last run answered 503/429 -> half as many this run (never below 1). After two
    ordinary runs in a row the number climbs back one at a time to the setting. Only runs that made calls count.
    Returns (concurrency, state) with state normal | halved | holding | recovering."""
    rows = conn.execute(
        "SELECT calls,busy_503,busy_429,effective_concurrency FROM worker_heartbeats WHERE calls>0 ORDER BY id DESC LIMIT 2"
    ).fetchall()
    if not rows:
        return configured, "normal"
    busy = lambda r: (r["busy_503"] + r["busy_429"]) * 2 > r["calls"]
    last = min(rows[0]["effective_concurrency"] or configured, configured)
    if busy(rows[0]):
        return max(1, last // 2), "halved"
    if last >= configured:
        return configured, "normal"
    if len(rows) >= 2 and not busy(rows[1]):
        return min(configured, last + 1), "recovering"
    return last, "holding"


def platform_trouble(conn, since):
    """How many of this run's calls met a busy or unreachable platform: (HTTP 503, HTTP 429, timeouts/broken connections)."""
    counts = {
        r["error_code"]: r["n"]
        for r in conn.execute(
            "SELECT error_code,count(*) AS n FROM ai_call_events WHERE created_at>=%s AND error_code IS NOT NULL GROUP BY 1",
            (since,),
        ).fetchall()
    }
    timeouts = sum(n for code, n in counts.items() if code in ai_jobs.PLATFORM_CODES and not code.startswith("provider_http_"))
    return counts.get("provider_http_503", 0), counts.get("provider_http_429", 0), timeouts


def record_heartbeat(report, started, error):
    """One row per run for the progress page (numbers and codes only). Never lets a run fail."""
    from decimal import Decimal

    if error:
        outcome = "error:" + error
    elif not str(report.get("enabled", "")).startswith("yes"):
        outcome = "disabled"
    else:
        outcome = "ok"
    queue = {}
    for part in str(report.get("queue", "")).split(","):
        name, _, number = part.partition("=")
        if number.isdigit():
            queue[name] = int(number)
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM worker_heartbeats WHERE finished_at<clock_timestamp()-interval '14 days'")
            conn.execute(
                "INSERT INTO worker_heartbeats(started_at,outcome,calls,cost_cny,stopped_because,queue,concurrency,duration_seconds,"
                "busy_503,busy_429,timeouts,effective_concurrency) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (started, outcome, int(report.get("calls", 0) or 0), Decimal(str(report.get("cost_cny", "0") or "0")),
                 report.get("stopped_because"), ai_jobs.Jsonb(queue),
                 int(report.get("parallel_peak") or 1),
                 Decimal(str(report.get("duration_s", "0") or "0")),
                 int(report.get("http_503") or 0), int(report.get("http_429") or 0), int(report.get("timeouts") or 0),
                 int(report.get("concurrency") or 0) or None),
            )
    except Exception:
        pass  # a missing table or database hiccup must not turn a good run into a failed one


def main():
    from datetime import datetime, timezone

    started = datetime.now(timezone.utc)
    signal.signal(signal.SIGTERM, lambda *_: SHUTDOWN.set())
    report = {}
    code = 0
    try:
        run(report)
    except Exception as exc:
        # Application-owned messages (fixed strings / codes) are safe; others: type only.
        safe = isinstance(exc, (PermissionError, ValueError)) and getattr(exc, "errno", None) is None  # ours, not the OS's
        report["error"] = type(exc).__name__ + (":" + str(exc)[:120].replace(" ", "_") if safe else "")
        if isinstance(exc, psycopg.Error):
            # which table, which constraint, which value, and the step it happened in (the worker's own row names, never learner text)
            report["error"] = describe_db_error(exc)
            report["error_at"] = WHERE["at"]
        code = 1
    report["duration_s"] = f"{(datetime.now(timezone.utc) - started).total_seconds():.1f}"  # the whole run
    if not report.get("skipped"):                 # a run that left at once (the previous one still going) is not a run
        record_heartbeat(report, started, report.get("error", "").split(":")[0] if code else "")
    report.pop("since", None)
    print("worker-run " + " ".join(f"{k}={_fmt(v)}" for k, v in report.items()), flush=True)
    if code:
        print("AI automation failed (" + report["error"].split(":")[0] + ")", file=sys.stderr, flush=True)
        sys.exit(code)


if __name__ == "__main__":
    main()
