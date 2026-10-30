"""Continuous independent QA planner/executor; science gates are unchanged."""
import json
import os
import time
import threading
import traceback
import signal
import shutil
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from psycopg.types.json import Jsonb
from app.qa_harness_tick import qa_db, pinned_worker, SDK_VERSION
from app.qa_automation import POLICY, SCOPE, authorized, bind_pending


def bootstrap():
    from migrate import apply_migrations
    from app.common import ensure_user
    from app import model_router, cny_prices
    from app.source_readiness import audit
    from app.model_compatibility import enqueue_probe
    authorized()
    if not pinned_worker(os.environ) or version('deepseek-harness-sdk') != SDK_VERSION:
        raise PermissionError('qa_runtime_or_sdk_identity_mismatch')
    if os.environ.get('AI_AUTOMATION_ENABLED') != '1':
        raise PermissionError('qa_automation_not_enabled')
    model_router.authorized(private=True)
    with qa_db() as conn:
        apply_migrations(conn)
        if not audit(conn)['all_six_byte_integrity_checks_passed']:
            raise PermissionError('six_source_integrity_failed')
        uid = ensure_user(conn, 'xiaohan')
        model_router.settings(conn, uid)
        conn.execute('INSERT INTO qa_automation_grants(scope_id,user_id,policy) '
                     'VALUES(%s,%s,%s) ON CONFLICT DO NOTHING', (SCOPE,uid,Jsonb(POLICY)))
        # A grant by itself must not clear an operator pause.
        conn.execute("UPDATE ai_settings SET model_id='deepseek-flash',effort='high',"
                     "token_limit=32768,retry_limit=2,max_concurrency=2,"
                     "task_limit_enabled=false,daily_limit_enabled=false,"
                     "monthly_limit_enabled=false,trial_active=false,auto_upgrade=false "
                     "WHERE user_id=%s", (uid,))
        print("QA_AUTOMATION_BOOTSTRAP=catalog",flush=True)
        model_router.refresh(conn)
        print("QA_AUTOMATION_BOOTSTRAP=official_cny_prices",flush=True)
        cny_prices.refresh(conn)
        model = conn.execute("SELECT compatibility FROM ai_model_catalog WHERE "
                             "model_id='deepseek-flash' AND available").fetchone()
        if not model:
            raise ValueError('approved_model_not_in_official_catalog')
        if not model['compatibility'].get('real_api_verified'):
            outstanding=conn.execute("SELECT 1 FROM ai_jobs WHERE kind='compatibility' "
                "AND state IN ('pending','running','retrying') "
                "AND settings_snapshot->'qa_automation'->>'scope_id'=%s LIMIT 1",(SCOPE,)).fetchone()
            if not outstanding:enqueue_probe(conn,uid,'deepseek-flash','high')
        bind_pending(conn,uid)
        # A completed attempt's SDK workspace is reproducible. Keep its raw
        # provider receipt; never prune a currently leased/running job.
        from app.harness_gateway import prune_runtime_cache
        private_root=Path(os.environ.get('AI_PRIVATE_HOME','/tmp/ustc-harness-private'))
        if private_root.is_symlink():
            raise PermissionError('qa_private_evidence_symlink_rejected')
        private_root.mkdir(parents=True,exist_ok=True)
        for row in conn.execute("SELECT id,attempts FROM ai_jobs WHERE state<>'running'").fetchall():
            for attempt in range(1,row['attempts']+1):
                prune_runtime_cache(private_root/f"job-{row['id']}-attempt-{attempt}")
        # Keep the human pause latched until the precise owner continuation is
        # consumed once, *after* integrity and disk cleanup. A future pause is
        # never overridden by a restarted deployment with the same env flag.
        if shutil.disk_usage(private_root).free < 160 * 1024 * 1024:
            raise OSError('qa_private_evidence_insufficient_free_space')
        approval = os.environ.get('QA_AUTOMATION_RESUME_APPROVAL_ID')
        if approval == '2026-10-30-explicit-owner-continue-v1':
            row = conn.execute(
                "INSERT INTO qa_automation_resume_consumptions(approval_id,scope_id,user_id) "
                "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING approval_id",
                (approval,SCOPE,uid),
            ).fetchone()
            if row:
                conn.execute("UPDATE ai_settings SET stopped=false,updated_at=clock_timestamp() "
                             "WHERE user_id=%s", (uid,))
                print('QA_OWNER_RESUME_CONSUMED_ONCE',flush=True)
    return uid


def plan_tick(uid):
    from app.auto_worker import dispatch_completed, plan
    from app import auto_knowledge, auto_generation, auto_exams
    with qa_db() as conn:
        if not conn.execute('SELECT pg_try_advisory_xact_lock(280061) AS ok').fetchone()['ok']:
            return
        dispatch_completed(conn, scope_id=SCOPE, scope_kind='qa_automation')
        if conn.execute("SELECT stopped FROM ai_settings WHERE user_id=%s", (uid,)).fetchone()["stopped"]:
            return
        # Only retry proven ENOSPC without confirmed request, surviving
        # provider receipt, or stale private-source evidence.
        from app.qa_infra_recovery import replay_enospc, requeue_owner_paused
        requeue_owner_paused(conn,uid,SCOPE)
        replay_enospc(conn,uid,Path(os.environ.get('AI_PRIVATE_HOME','/tmp/ustc-harness-private')))
        m = conn.execute("SELECT compatibility FROM ai_model_catalog WHERE model_id='deepseek-flash' AND available").fetchone()
        if m and m['compatibility'].get('real_api_verified'):
            for fn in (plan, auto_knowledge.plan_small_batch, auto_generation.plan, auto_exams.plan):
                try:
                    with conn.transaction():fn(conn,uid)
                except ValueError:pass
        bind_pending(conn,uid)


def execute_tick():
    from app.ai_jobs import run_once
    from app.harness_gateway import execute
    return run_once(qa_db,execute,scope_id=SCOPE,scope_kind='qa_automation')


def status():
    with qa_db() as conn:
        jobs = conn.execute('SELECT kind,state,count(*) AS n FROM ai_jobs '
                            'GROUP BY kind,state ORDER BY kind,state').fetchall()
        knowledge = conn.execute('SELECT status,count(*) AS n FROM auto_knowledge_events '
                                 'GROUP BY status ORDER BY status').fetchall()
        bank = conn.execute('SELECT status,count(*) AS n FROM reviewed_questions '
                            'GROUP BY status ORDER BY status').fetchall()
        failures = conn.execute("SELECT id,kind,state,error_code,result->'diagnostic' AS diagnostic,result->'dispatch_failure' AS dispatch_failure,result->'execution_failure' AS execution_failure "
                                "FROM ai_jobs WHERE error_code IS NOT NULL ORDER BY id DESC LIMIT 8").fetchall()
        calls = conn.execute("SELECT outcome,count(*) AS n FROM ai_call_events GROUP BY outcome").fetchall()
        # All of the following are QA-only counters: no source excerpts,
        # answers, credentials, file paths, or private content leave the DB.
        source_risks = conn.execute(
            "SELECT review_state,count(*) AS n FROM qa_source_risk_progress "
            "GROUP BY review_state ORDER BY review_state"
        ).fetchall()
        chapters = conn.execute(
            "SELECT review_state,count(*) AS n FROM qa_chapter_mapping_progress "
            "GROUP BY review_state ORDER BY review_state"
        ).fetchall()
        graph = conn.execute(
            "SELECT scientific_review_state,count(*) AS n "
            "FROM qa_textbook_evidence_nodes GROUP BY scientific_review_state "
            "ORDER BY scientific_review_state"
        ).fetchall()
        subjects = conn.execute(
            "SELECT content->>'subject' AS subject,"
            "content->>'question_type' AS question_type,"
            "status,count(*) AS n FROM reviewed_questions "
            "GROUP BY 1,2,3 ORDER BY 1,2,3"
        ).fetchall()
        papers = conn.execute("SELECT count(*) AS n FROM papers_v2").fetchone()["n"]
    disk=shutil.disk_usage("/private-evidence" if Path("/private-evidence").is_dir() else "/tmp")
    return dict(
        jobs=jobs,knowledge=knowledge,bank=bank,failures=failures,calls=calls,
        qa_source_risk_states=source_risks,qa_chapter_mapping_states=chapters,
        qa_graph_science_states=graph,qa_question_buckets=subjects,
        qa_papers_v2=papers,disk_free_bytes=disk.free,
        complete_scientific_acceptance_claimed=False
    )


def serve_health():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            body=b'{"status":"worker_alive","scientific_acceptance":false}'
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server=ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()


def main():
    serve_health()
    stop=threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    uid = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = []
        next_plan = next_status = next_bootstrap = next_refresh = 0
        while not stop.is_set():
            try:
                now = time.monotonic()
                if uid is None:
                    if now < next_bootstrap:
                        time.sleep(2)
                        continue
                    next_bootstrap = now + 60
                    uid = bootstrap()
                    next_refresh = now+3600
                    print('QA_AUTOMATION_BOOTSTRAPPED',flush=True)
                if now >= next_refresh:
                    bootstrap()
                    next_refresh = now+3600
                # Propagate execution infrastructure failures, never silently discard them.
                done = [f for f in futures if f.done()]
                futures = [f for f in futures if not f.done()]
                for f in done:f.result()
                if now >= next_plan:
                    plan_tick(uid)
                    next_plan = now+10
                while len(futures)<2:
                    futures.append(pool.submit(execute_tick))
                if now >= next_status:
                    print('QA_AUTOMATION_STATUS='+json.dumps(status(),default=str,separators=(',',':')),flush=True)
                    next_status = now+60
            except Exception as exc:
                frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(exc.__traceback__)]
                print('QA_AUTOMATION_CYCLE_FAILED='+json.dumps(dict(error=type(exc).__name__,frames=frames)),flush=True)
                time.sleep(5)
            time.sleep(2)


if __name__=='__main__':main()
