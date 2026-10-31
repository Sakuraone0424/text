"""QA media first-pass actual content records, never 31 science approvals."""
from collections import Counter
import pytest
from app import qa_exam_media_science_firstpass as media


def test_all_31_original_exam_media_have_visual_content_and_nonapproval():
    assert len(media.CASES)==31
    assert len({(r[0],r[1]) for r in media.CASES})==31
    assert Counter(r[0] for r in media.CASES)=={"cell":4,"bio":27}
    assert dict(Counter(r[3] for r in media.CASES))==media.EXPECTED
    assert all(r[2]>=2004 and len(r[4])>=6 and len(r[5])>=9
               for r in media.CASES)
    assert "CHECK(NOT science_approved)" in media.DDL
    assert "CHECK(NOT official_answer_approved)" in media.DDL
    blocked=[r for r in media.CASES if r[3]=="blocked"]
    assert any("质粒" in r[4] for r in blocked)
    assert any("PDGF" in r[4] for r in blocked)


class Rows:
    def __init__(self,one=None,many=None):
        self.one=one
        self.many=[] if many is None else many
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.many


class DB:
    def __init__(self):
        self.inserted=[]
        self.created=False
    def execute(self,sql,args=None):
        if sql.startswith("SELECT sha256 FROM source_documents"):
            return Rows(one={"sha256":"a"*64})
        if sql.startswith("CREATE TABLE"):
            self.created=True
            return Rows()
        if sql.startswith("SELECT source_file_id,"):
            columns=("source_file_id","source_sha256","media_filename","exam_year",
                     "firstpass_status","visual_observation","answer_limitation")
            values=[{**dict(zip(columns,v)),"independent_review_pending":True,
                     "science_approved":False,"official_answer_approved":False}
                    for v in self.inserted]
            return Rows(many=values)
        raise AssertionError(sql)
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def executemany(self,sql,records):
        assert "ON CONFLICT DO NOTHING" in sql
        assert self.created
        self.inserted.extend(records)


def test_all_figures_are_inserted_and_readback_with_immutable_pending_status(monkeypatch):
    monkeypatch.setattr(media,"pinned_source_bytes",lambda fid,sha:sha=="a"*64)
    db=DB()
    receipt=media.seed(db)
    assert len(db.inserted)==31
    assert receipt["media_source_records"]==31
    assert receipt["content_firstpass_status"]==media.EXPECTED
    assert receipt["unreviewed_remaining"]==0
    assert receipt["science_approved"]==0
    assert receipt["official_answers_approved"]==0
    assert receipt["model_calls"]==0


def test_unlocked_owner_docx_cannot_receive_content_review(monkeypatch):
    monkeypatch.setattr(media,"pinned_source_bytes",lambda fid,sha:False)
    db=DB()
    with pytest.raises(PermissionError,match="docx_lock"):
        media.seed(db)
    assert db.inserted==[] and not db.created
