"""Owner-authorized QA automation, distinct from immutable historical trials.

Every bounded job is HMAC bound to its exact payload and model parameters.
The SDK receives no database credentials. A grant is execution permission,
never scientific approval. Existing two-round and publication gates still apply.
"""
import hashlib
import hmac
import os

from psycopg.types.json import Jsonb
from app.ai_jobs import content_sha

SYSTEM_SHA = 'f781d95fcd429c48a6c5de98413621814f613fff9a5bc05e474beba6f5715b26'
POLICY = {
    'version': 'qa-autonomy/1', 'model': 'deepseek-flash', 'sdk': '0.2.1rc2',
    'system_sha256': SYSTEM_SHA, 'effort': 'high', 'token_limit': 32768,
    'retry_limit': 2, 'max_concurrency': 2,
    # Old broad-scope QA model authorizations must never survive this change.
    # The owner permits DeepSeek ONLY for new DAILY question generation.
    'tasks': ['generation'],
    'data': 'minimum_locked_six_source_excerpts_and_owner_answers',
    'tools': False, 'money_limit': 'requires_separate_owner_authorized_daily_budget',
    'target_project': '0ff7ff18-e746-42db-8eb2-b515306fa473',
    'target_schema': 'source_staging_ocr_20261030',
}
SCOPE = content_sha(POLICY)


def authorized(env=None):
    env = os.environ if env is None else env
    if (env.get('QA_AUTOMATION_ENABLED') != '1'
            or env.get('DEEPSEEK_AUTOMATION_SCOPE_ID') != SCOPE
            or len(env.get('QA_AUTOMATION_SIGNING_KEY', '')) < 40):
        raise PermissionError('qa_automation_grant_required')


def envelope(job):
    s = job['settings_snapshot']
    return dict(scope_id=SCOPE, job_id=job['id'], user_id=job['user_id'],
                kind=job['kind'], payload_sha256=content_sha(job['payload']),
                parameters={k: s[k] for k in
                            ('model_id','effort','token_limit','retry_limit','max_concurrency')})


def signature(job):
    authorized()
    return hmac.new(os.environ['QA_AUTOMATION_SIGNING_KEY'].encode(),
                    content_sha(envelope(job)).encode(), hashlib.sha256).hexdigest()


def check_job(job):
    authorized()
    s = job['settings_snapshot']
    expected = ('deepseek-flash', 'high', 32768, 2, 2)
    actual = tuple(s.get(k) for k in
                   ('model_id','effort','token_limit','retry_limit','max_concurrency'))
    guard = s.get('qa_automation') or {}
    if (actual != expected or job['kind'] not in POLICY['tasks']
            or set(guard) != {'scope_id','signature'} or guard['scope_id'] != SCOPE
            or not hmac.compare_digest(str(guard['signature']), signature(job))
            or 'limited_trial' in s or 'review_campaign' in s):
        raise ValueError('qa_automation_job_binding_invalid')
    return guard


def bind_pending(conn, uid):
    authorized()
    grant = conn.execute('SELECT policy FROM qa_automation_grants WHERE scope_id=%s AND user_id=%s',
                         (SCOPE, uid)).fetchone()
    if not grant or grant['policy'] != POLICY:
        raise PermissionError('durable_qa_automation_grant_required')
    rows = conn.execute("SELECT * FROM ai_jobs WHERE user_id=%s AND state IN ('pending','retrying') "
                        "AND kind='generation' AND NOT(settings_snapshot ? 'qa_automation') "
                        "AND NOT(settings_snapshot ? 'limited_trial') "
                        "AND NOT(settings_snapshot ? 'review_campaign') "
                        "ORDER BY id LIMIT 100 FOR UPDATE SKIP LOCKED", (uid,)).fetchall()
    for row in rows:
        snap = {**row['settings_snapshot'], 'qa_automation':
                dict(scope_id=SCOPE, signature=signature(row))}
        proposed = {**row, 'settings_snapshot': snap}
        check_job(proposed)
        conn.execute('UPDATE ai_jobs SET settings_snapshot=%s WHERE id=%s', (Jsonb(snap), row['id']))
    return len(rows)


def claim_reason(conn, job):
    try:
        check_job(job)
    except (ValueError, PermissionError, KeyError):
        return 'invalid_qa_automation_grant'
    row = conn.execute('SELECT policy FROM qa_automation_grants WHERE scope_id=%s AND user_id=%s',
                       (SCOPE, job['user_id'])).fetchone()
    if not row or row['policy'] != POLICY:
        return 'durable_qa_automation_grant_missing'
    return None


def execution_guard(job):
    check_job(job)
    if job.get('state') != 'running' or not 1 <= job.get('attempts', 0) <= 3:
        raise ValueError('leased_qa_automation_attempt_required')
    from app.limited_trial import wire_guard
    return wire_guard(job['payload'], SYSTEM_SHA, 'high', 32768)
