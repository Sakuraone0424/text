"""A successful dump is never substituted for a separate-cluster restore."""
import json
import pytest
from app import qa_backup_worker as b


def test_wrong_platform_refused_before_database(monkeypatch):
    monkeypatch.setenv('RAILWAY_PROJECT_ID','not-the-qa-project')
    def forbidden(*a,**k):pytest.fail('No database may be contacted')
    monkeypatch.setattr(b.psycopg,'connect',forbidden)
    with pytest.raises(PermissionError):b.validate_runtime()


def test_old_restore_receipt_cannot_certify_a_recreated_cluster(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'ROOT',tmp_path)
    (tmp_path/'independent-restore-receipt.json').write_text(json.dumps({'target_system_id':'old'}))
    with pytest.raises(PermissionError):b.restore_once(tmp_path,{}, {},'new')


def test_existing_database_stops_restore_before_subprocess(tmp_path,monkeypatch):
    monkeypatch.setattr(b,'ROOT',tmp_path)
    monkeypatch.setenv('QA_RESTORE_DATABASE_URL','unused-synthetic')
    class C:
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(b.psycopg,'connect',lambda *a,**k:C())
    def not_empty(c):raise ValueError('target_not_empty_no_restore_permitted')
    monkeypatch.setattr(b,'require_empty',not_empty)
    monkeypatch.setattr(b.subprocess,'run',lambda *a,**k:pytest.fail('Must never restore into existing data'))
    with pytest.raises(ValueError,match='not_empty'):b.restore_once(tmp_path,{}, {},'different')
