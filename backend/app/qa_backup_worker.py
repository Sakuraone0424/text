"""Private daily QA snapshots and a real restore into a separate empty cluster.

Never overwrites a database, drops a schema, prints credentials, or claims
an archive check is an executed restore. The source connection is read-only.
"""
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict
from app.deployment_target import QA_TARGET, QA_SOURCE_SCHEMA, verify_database_url, verify_database_connection
from app.review_backup import export, verify_package
from app.recovery_check import require_empty, verify_restored
from app.source_intake import private_json

ROOT=Path('/private-backups')


def validate_runtime():
    if (os.environ.get('RAILWAY_PROJECT_ID') != QA_TARGET['PROJECT']
        or os.environ.get('RAILWAY_ENVIRONMENT_ID') != QA_TARGET['ENVIRONMENT']
        or os.environ.get('RAILWAY_SERVICE_ID') != os.environ.get('QA_BACKUP_APPROVED_SERVICE_ID')
        or os.environ.get('QA_BACKUP_ENABLED') != '1'):
        raise PermissionError('qa_backup_identity_required')
    source=os.environ['SOURCE_STAGING_DATABASE_URL']
    verify_database_url(source)
    with psycopg.connect(source,row_factory=dict_row) as c:
        c.execute('SET TRANSACTION READ ONLY')
        verify_database_connection(c)
    target=os.environ['QA_RESTORE_DATABASE_URL']
    info=conninfo_to_dict(target)
    if (info.get('host') != os.environ.get('QA_RESTORE_EXPECTED_HOST')
        or not info.get('host','').endswith('.railway.internal')
        or info.get('dbname') != 'qa_restore'
        or any(info.get(k) for k in ('hostaddr','service','options'))):
        raise PermissionError('qa_restore_endpoint_not_approved')
    with psycopg.connect(target,row_factory=dict_row) as c:
        c.execute('SET TRANSACTION READ ONLY')
        row=c.execute('SELECT system_identifier::text AS id FROM pg_control_system()').fetchone()
        if row['id'] == os.environ['QUIZ_APPROVED_DB_SYSTEM_ID']:
            raise PermissionError('restore_must_use_separate_cluster')
    return info,row['id']


def historical_restore_status(previous, current_backup_sha, target_system_id):
    """Never treat a previous cluster restore as a new independent restore.

    In particular, a daily snapshot with a new SHA remains unverified until
    restored into a separately approved, EMPTY target; populated targets are
    never wiped or overwritten.
    """
    if previous.get('target_system_id') != target_system_id:
        raise PermissionError('restore_proof_belongs_to_different_cluster')
    prior_proven = bool(previous.get('restore_executed')
                        and previous.get('independent_readback'))
    same_snapshot = previous.get('backup_sha256') == current_backup_sha
    return {
        'status': ('prior_restore_matches_current_snapshot'
                   if prior_proven and same_snapshot
                   else 'historic_restore_only_latest_snapshot_unverified'),
        'historic_restore_proof': prior_proven,
        'current_snapshot_sha256': current_backup_sha,
        'prior_verified_backup_sha256': previous.get('backup_sha256'),
        'independently_restored_current_snapshot': prior_proven and same_snapshot,
        'fresh_restore_executed_this_tick': False,
        'restore_executed': False,
        'prior_verified_tables': previous.get('tables_verified'),
        'prior_verified_migrations': previous.get('migrations_verified'),
        'prior_verified_sources': previous.get('source_files_verified'),
        'target_system_id': target_system_id,
    }


def restore_once(directory,index,info,target_system_id):
    receipt=ROOT/'independent-restore-receipt.json'
    if receipt.exists():
        previous=json.loads(receipt.read_text())
        # Cluster identity must be rejected BEFORE touching any snapshot index:
        # a stale proof cannot be replayed against a newly provisioned target.
        if previous.get('target_system_id') != target_system_id:
            raise PermissionError('restore_proof_belongs_to_different_cluster')
        if not isinstance(index.get('sha256'), str):
            raise ValueError('current_snapshot_sha_required')
        return historical_restore_status(previous,index['sha256'],target_system_id)
    target=os.environ['QA_RESTORE_DATABASE_URL']
    with psycopg.connect(target,row_factory=dict_row) as c:
        require_empty(c)
    # Verify both ZIP and dump before any write to the dedicated empty target.
    verify_package(directory/index['filename'],index,'/usr/lib/postgresql/17/bin')
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','SSL_CERT_FILE')}
    mapping={'host':'PGHOST','port':'PGPORT','user':'PGUSER','password':'PGPASSWORD','dbname':'PGDATABASE'}
    env.update({mapping[k]:v for k,v in info.items() if k in mapping})
    subprocess.run(['/usr/lib/postgresql/17/bin/pg_restore','--single-transaction',
                    '--exit-on-error','--no-owner','--no-acl','--dbname=qa_restore',
                    str(directory/'database.dump')],env=env,capture_output=True,check=True)
    manifest=json.loads((directory/'database-manifest.json').read_text())
    with psycopg.connect(target,row_factory=dict_row) as c:
        c.execute('SET TRANSACTION READ ONLY')
        c.execute("SET LOCAL TIME ZONE 'UTC'")
        result=verify_restored(c,manifest)
    result.update(restore_executed=True,
                  independently_restored_current_snapshot=True,
                  fresh_restore_executed_this_tick=True,
                  historic_restore_proof=False,
                  backup_sha256=index['sha256'],
                  verified_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                  target_system_id=target_system_id,
                  source_system_id=os.environ['QUIZ_APPROVED_DB_SYSTEM_ID'])
    private_json(receipt,result)
    return result


def tick():
    info,target_system=validate_runtime()
    ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
    pointer=ROOT/'latest.json'
    day=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    if pointer.exists():
        saved=json.loads(pointer.read_text())
        if saved['day']==day:
            directory=ROOT/saved['directory']
            index=json.loads((directory/'download-index.json').read_text())
            return restore_once(directory,index,info,target_system)
    directory=ROOT/('snapshot-'+day+'-'+uuid4().hex[:12])
    index=export(directory,QA_SOURCE_SCHEMA,'/usr/lib/postgresql/17/bin','railway')
    proof=restore_once(directory,index,info,target_system)
    temp=ROOT/('pointer-'+uuid4().hex+'.json')
    private_json(temp,dict(day=day,directory=directory.name))
    os.replace(temp,pointer)
    return proof


def main():
    from app.qa_autonomous_worker import serve_health
    serve_health()
    while True:
        try:
            print('QA_BACKUP_RESTORE_PROOF='+json.dumps(tick(),sort_keys=True),flush=True)
            time.sleep(3600)
        except Exception as exc:
            print('QA_BACKUP_FAILED='+type(exc).__name__,flush=True)
            time.sleep(60)


if __name__=='__main__':main()
