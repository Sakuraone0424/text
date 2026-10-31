"""QA-only six-source upload boundary; synthetic fixtures, never user originals."""
from io import BytesIO
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import qa_import_upload as upload
from app.source_policy import COLLECTION
from test_collection_seed import synthetic_collection


def synthetic_zip(root, destination, *, extras=True):
    with zipfile.ZipFile(destination,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for item in COLLECTION["files"]:
            archive.write(root/item["relative_path"],item["relative_path"])
        if extras:
            archive.writestr("PRIVATE_QA_SOURCE_LOCK.json","metadata only")
            archive.writestr("README_PRIVATE_QA.txt","QA synthetic fixture")
    return destination


def test_six_files_can_be_unpacked_without_claiming_approval(tmp_path):
    root, lock=synthetic_collection(tmp_path)
    archive=synthetic_zip(root,tmp_path/"six.zip")
    items=upload.unpack_verified_bundle(archive,tmp_path/"extract",lock_path=lock)
    assert len(items)==6
    assert sum(item["ocr_page_markers"] for item in items)==1915
    assert all(item["sha256"] and item["ocr_units"] for item in items)


def test_archive_rejects_unexpected_members_and_missing_extras(tmp_path):
    root,lock=synthetic_collection(tmp_path)
    archive=synthetic_zip(root,tmp_path/"bad.zip",extras=False)
    with pytest.raises(ValueError,match="Unexpected"):
        upload.unpack_verified_bundle(archive,tmp_path/"missing",lock_path=lock)
    with zipfile.ZipFile(archive,"a") as z:
        z.writestr("../escaped.txt","never extract")
        z.writestr("PRIVATE_QA_SOURCE_LOCK.json","metadata")
        z.writestr("README_PRIVATE_QA.txt","readme")
    with pytest.raises(ValueError,match="Unexpected"):
        upload.unpack_verified_bundle(archive,tmp_path/"extra",lock_path=lock)
    assert not (tmp_path/"escaped.txt").exists()


def test_archive_changed_original_fails_exact_hash_lock(tmp_path):
    root,lock=synthetic_collection(tmp_path)
    archive=synthetic_zip(root,tmp_path/"six.zip")
    changed=tmp_path/"changed.zip"
    source=COLLECTION["files"][0]["relative_path"]
    with zipfile.ZipFile(archive) as old,zipfile.ZipFile(changed,"w") as fresh:
        for filename in old.namelist():
            data=old.read(filename)
            if filename==source:
                data+=b"tampered"
            fresh.writestr(filename,data)
    with pytest.raises(ValueError,match="SHA-256"):
        upload.unpack_verified_bundle(changed,tmp_path/"badextract",lock_path=lock)


def test_qa_only_api_requires_private_secret_and_qa_env(monkeypatch,tmp_path):
    root,lock=synthetic_collection(tmp_path)
    archive=synthetic_zip(root,tmp_path/"six.zip")
    body=archive.read_bytes()
    client=TestClient(upload.app)
    token="x"*64
    monkeypatch.setenv("QA_UPLOAD_TOKEN",token)
    monkeypatch.setenv("QA_IMPORT_ENABLED","1")
    monkeypatch.setenv("SOURCE_STAGING_DATABASE_URL","postgresql://test:secret@qa.internal/test")
    monkeypatch.setenv("QA_IMPORT_SCHEMA","source_staging_synthetic_test")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME","qa")
    assert client.get("/health").json()["source_import_state"]=="not_checked"
    assert client.post("/import",content=body,headers={"content-type":"application/zip"}).status_code==401
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME","production")
    result=client.post("/import",content=body,headers={
        "content-type":"application/zip","authorization":"Bearer "+token})
    assert result.status_code==503
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME","qa")
    assert client.post("/import",content=body,headers={
        "content-type":"text/plain","authorization":"Bearer "+token}).status_code==415


def test_qa_upload_validates_synthetic_source_then_invokes_isolated_import_once(
    monkeypatch,tmp_path
):
    root,lock=synthetic_collection(tmp_path)
    bundle=synthetic_zip(root,tmp_path/"six.zip").read_bytes()
    token="z"*64
    for key,value in {
        "QA_UPLOAD_TOKEN":token,"QA_IMPORT_ENABLED":"1",
        "SOURCE_STAGING_DATABASE_URL":"postgresql://test:secret@qa.internal/test",
        "QA_IMPORT_SCHEMA":"source_staging_synthetic_test",
        "RAILWAY_ENVIRONMENT_NAME":"qa",
    }.items():
        monkeypatch.setenv(key,value)
    original_unpack=upload.unpack_verified_bundle
    def synthetic_lock_archive(path,root):
        return original_unpack(path,root,lock_path=lock)
    monkeypatch.setattr(upload,"unpack_verified_bundle",synthetic_lock_archive)
    invoked=[]
    def fake_import(items,schema,target,report,*,initialize_schema=False):
        invoked.append((len(items),schema,target,initialize_schema))
        return {
            "imported":[{"key":x["key"]} for x in items],
            "ocr_page_markers":1915,
            "database_integrity":{"independent_post_commit_readback_verified":True}
        }
    monkeypatch.setattr(upload,"import_isolated",fake_import)
    response=TestClient(upload.app).post("/import",content=bundle,headers={
        "authorization":"Bearer "+token,"content-type":"application/zip"
    })
    assert response.status_code==200
    assert response.json()["imported_files"]==6
    assert response.json()["science_review_completed"] is False
    assert invoked==[(6,"source_staging_synthetic_test","staging",True)]
    assert "test:secret" not in response.text


def test_invalid_payload_never_calls_qa_database(monkeypatch):
    monkeypatch.setenv("QA_UPLOAD_TOKEN","a"*64)
    monkeypatch.setenv("QA_IMPORT_ENABLED","1")
    monkeypatch.setenv("QA_IMPORT_SCHEMA","source_staging_synthetic_test")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME","qa")
    monkeypatch.setenv("SOURCE_STAGING_DATABASE_URL","postgresql://qa.internal/test")
    def abort(*args,**kwargs):
        raise AssertionError("Database must not be touched")
    monkeypatch.setattr(upload,"import_isolated",abort)
    client=TestClient(upload.app)
    response=client.post("/import",content=b"not a zip",headers={
        "authorization":"Bearer "+"a"*64,"content-type":"application/zip"
    })
    assert response.status_code==422
    assert "SHA-256" in response.text
