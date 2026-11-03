"""Short-lived leased automation tick, activated only with explicit API consent."""

import httpx
import os
import sys
import signal
import threading
import time
from app.db import get_db
from app import cost_control
from app import ai_jobs, auto_quality, model_router, model_compatibility, auto_grading
from app import auto_knowledge, auto_generation, auto_exams, auto_insights, past_exam_import, legacy_reuse
from app import cost_reconcile
from app.harness_gateway import execute
from app.worker import tick, integrations_tick


from app.dispatch_codes import safe_dispatch_failure, _SAFE_VALIDATOR_ERRORS, _SAFE_QUALITY_CODES, _SAFE_PACKET_ERRORS  # noqa: F401  (re-exported)


def dispatch_completed(conn, scope_id=None, scope_kind="limited_trial"):
    if scope_kind not in ("limited_trial", "review_campaign", "qa_automation"):
        raise ValueError("unknown_execution_scope")
    scope_filter = (
        " AND settings_snapshot->'" + scope_kind + "'->>'scope_id'=%s" if scope_id else ""
    )
    rows = conn.execute(
        "SELECT * FROM ai_jobs WHERE state='succeeded' AND dispatched_at IS NULL"
        + scope_filter
        + " ORDER BY id LIMIT 50 FOR UPDATE SKIP LOCKED",
        (scope_id,) if scope_id else (),
    ).fetchall()
    for job in rows:
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
                ai_jobs.enqueue(
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


def plan(conn, uid, target=None):
    auto_quality.requeue_past_exam_duplicates(conn)   # before anything can retire them
    auto_quality.retire_stale(conn)
    auto_quality.retire_old_scoring(conn)
    auto_quality.discard_unfixable(conn)
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
        payload = dict(
            question_id=row["question_id"],
            question=q,
            evidence=refs,
            style=style,
            instructions="Repair the question using only the supplied actual excerpts. Return JSON {question: complete Question object, reason: detailed correction rationale}. No invented citation, no human approval. A new version will undergo the independent review again.",
            schema=auto_quality.Question.model_json_schema(),
        )
        ai_jobs.enqueue(
            conn, uid, "repair:" + str(row["question_id"]), "repair", payload
        )
        conn.execute(
            "UPDATE auto_question_states SET updated_at=clock_timestamp(),status='repairing' WHERE question_id=%s AND EXISTS(SELECT 1 FROM ai_jobs j WHERE j.job_key=%s AND j.state IN ('pending','running','retrying'))",
            (row["question_id"], "repair:" + str(row["question_id"])),
        )
    for row in conn.execute(
        "SELECT DISTINCT p.id FROM papers_v2 p JOIN attempts_v2 t ON t.paper_id=p.id JOIN answers_v2 a ON a.attempt_id=t.id WHERE p.user_id=%s AND a.points IS NULL ORDER BY p.id LIMIT 10",
        (uid,),
    ).fetchall():
        auto_grading.schedule(conn, uid, row["id"])
    # Past-exam questions need no corpus-wide sweep: each request carries only
    # the few textbook paragraphs relevant to that one question.
    legacy_reuse.plan(conn, uid)
    # New questions are planned FIRST: past-exam term requests may take at most 20% of the tasks in flight, and that
    # share is counted against what was just queued
    if target:
        auto_generation.fill_queue(conn, uid, target)
    else:
        auto_generation.plan_gap(conn, uid)
    past_exam_import.plan(conn, uid, batch=max(past_exam_import.MAX_CONCURRENT_JOBS, target or 0))


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
DEFAULT_RUN_SECONDS = 240
EXPECTED_CALL_SECONDS = 60
SAFETY_SECONDS = 15


def run_seconds(env=None):
    """AI_RUN_SECONDS: whole-run time budget, default 240, between 60 and 270."""
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

    def __init__(self, get_db_, executor, workers, limit, seconds, clock=time.monotonic):
        self.get_db, self.executor = get_db_, executor
        self.workers, self.limit, self.seconds, self.clock = max(1, workers), limit, seconds, clock
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
        expected = max(EXPECTED_CALL_SECONDS, self.longest)
        return self.clock() + expected <= self.deadline - SAFETY_SECONDS

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
        if self.workers == 1:
            self._worker()  # one call at a time: no thread needed
            return self
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


def run(report):
    """One worker run. Fills `report` (ids, counts and codes only; no secrets, no textbook text)."""
    run_began = time.monotonic()
    raw = os.environ.get("AI_AUTOMATION_ENABLED")
    report["enabled"] = "yes" if raw == "1" else "NO(value=" + repr(raw) + ")"
    report["autoplan"] = "1" if os.environ.get("AI_QUEUE_AUTOPLAN") == "1" else "0"
    report["bank_target"] = cost_control.bank_target() or "-"
    report["planned"] = 0
    os.environ["QUIZ_SERVICE_ROLE"] = (
        "worker"  # Short jobs never own the API backup volume.
    )
    if raw != "1":
        return
    from app.deployment_target import verify_isolated_target

    verify_isolated_target()
    report["guard"] = "ok"
    if not os.environ.get("QUIZ_DB_SCHEMA", "").startswith("source_staging_"):
        raise PermissionError("Explicit isolated source schema required")
    model_router.authorized()
    report["authorized"] = "ok"
    tick()
    integrations_tick()  # Prepare at the scheduled tick before any long model task.
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
            except (ValueError, httpx.HTTPError) as exc:
                report["price_refresh"] = "failed:" + type(exc).__name__
                # Missing/expired/conflicting prices block new calls at claim().
        if (
            any(u["auto_discover"] for u in users)
            and model_router.catalog(conn)["stale"]
        ):
            model_router.refresh(conn)
        for user in users:
            upgrade = model_compatibility.auto_upgrade(conn, user["user_id"])
            report["upgrade"] = upgrade["status"] + (
                ":" + ",".join(f"{c['model']}={c['reason']}" for c in upgrade.get("candidates", []))
                if upgrade.get("candidates") else ":" + upgrade["model"] if upgrade.get("model") else ""
            )
            if (
                not user["stopped"]
                and private
                and os.environ.get("AI_QUEUE_AUTOPLAN") == "1"
            ):
                try:
                    with conn.transaction():
                        first_new = conn.execute("SELECT COALESCE(max(id),0) AS m FROM ai_jobs").fetchone()["m"]
                        # queue enough work that every call this run can make has a job to claim
                        plan(conn, user["user_id"], target=2 * calls_per_run())
                        report["planned"] = report.get("planned", 0) + conn.execute(
                            "SELECT count(*) AS n FROM ai_jobs WHERE id>%s", (first_new,)).fetchone()["n"]
                        if os.environ.get("AI_FULL_CORPUS_ENABLED") == "1":
                            auto_knowledge.plan_small_batch(conn, user["user_id"])
                            auto_generation.plan(conn, user["user_id"])
                            auto_exams.plan(conn, user["user_id"])
                        auto_insights.plan(conn, user["user_id"])
                except ValueError:
                    pass  # Not admitted is not a success; job/probe status remains visible.
        # Retry budget/stopped jobs only when user explicitly enabled new attempts.
        conn.execute(
            "UPDATE ai_jobs j SET state='retrying' FROM ai_settings s WHERE s.user_id=j.user_id AND NOT s.stopped AND j.state='blocked' "
            "AND (j.error_code IN ('budget_exceeded','automation_stopped') OR j.error_code LIKE ANY(%s))",
            ([c + "%" for c in ai_jobs.MODEL_GONE_CODES],),
        )
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
    workers = min(report.get("max_concurrency") or 1, limit)
    pool = CallPool(get_db, execute, workers, limit, run_seconds() - (time.monotonic() - run_began)).run()
    report["calls"] = pool.calls
    report["concurrency"] = workers
    report["parallel_peak"] = pool.peak
    report["claimed"] = ",".join(str(c) for c in pool.claimed) or "-"
    if pool.overran:
        report["overran"] = "yes"  # a call was still running at the deadline; its lease expires and it is retried
    if pool.errors:
        raise pool.errors[0]
    # Why the run stopped early (set by ai_jobs.claim; ids, kinds and codes only).
    if pool.calls < limit:
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
                "INSERT INTO worker_heartbeats(started_at,outcome,calls,cost_cny,stopped_because,queue,concurrency,duration_seconds) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (started, outcome, int(report.get("calls", 0) or 0), Decimal(str(report.get("cost_cny", "0") or "0")),
                 report.get("stopped_because"), ai_jobs.Jsonb(queue),
                 int(report.get("parallel_peak") or 1),
                 Decimal(str(report.get("duration_s", "0") or "0"))),
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
        code = 1
    report["duration_s"] = f"{(datetime.now(timezone.utc) - started).total_seconds():.1f}"  # the whole run
    record_heartbeat(report, started, report.get("error", "").split(":")[0] if code else "")
    report.pop("since", None)
    print("worker-run " + " ".join(f"{k}={_fmt(v)}" for k, v in report.items()), flush=True)
    if code:
        print("AI automation failed (" + report["error"].split(":")[0] + ")", file=sys.stderr, flush=True)
        sys.exit(code)


if __name__ == "__main__":
    main()
