"""Content-level review of 23 biological mechanisms; no phony KG approval."""
import pytest
from collections import Counter
from app import qa_graph_23_science_firstpass as mech

def test_23_original_biology_and_biochemistry_mechanism_reviews_have_scientific_limits():
    assert len(mech.CASES)==23
    assert len({r[0] for r in mech.CASES})==23
    assert Counter(x[1] for x in mech.CASES)=={
        "cell_biology":14,"biochemistry_upper":4,"biochemistry_lower":5}
    assert any("微管" in r[4] and "微丝" in r[4] for r in mech.CASES)
    assert any("Ru5P" in r[4] and "R5P" in r[4] for r in mech.CASES)
    assert any("ATP" in r[4] and "活化" in r[3] for r in mech.CASES)
    assert "CHECK(NOT science_approved)" in mech.DDL


class Rows:
    def __init__(self,one=None,many=None):
        self.one=one;self.many=[] if many is None else many
    def fetchone(self):return self.one
    def fetchall(self):return self.many


class DB:
    def __init__(self,missing_anchor=False):
        self.missing_anchor=missing_anchor
        self.entries=[];self.created=False
    def execute(self,sql,p=None):
        if sql.startswith("SELECT id,sha256 FROM source_documents"):
            assert p[0] in mech.BOOKS.values()
            return Rows(one={"id":next(i for i,(key,fid)
                  in enumerate(mech.BOOKS.items(),1) if fid==p[0]),
                  "sha256":"a"*64})
        if sql.startswith("SELECT * FROM source_units"):
            if self.missing_anchor:
                return Rows(many=[])
            assert p[2] and p[1]>0
            return Rows(many=[{"id":100+next(i for i,case in enumerate(mech.CASES,1)
                   if case[2]==p[1] and case[3]==p[2]),
                   "text":p[2],"source_kind":"textbook","document_id":p[0]}])
        if sql.lstrip().startswith("CREATE TABLE"):
            self.created=True;return Rows()
        if sql.startswith("SELECT finding_id,"):
            cols=("finding_id","source_file_id","source_sha256","source_unit_id",
             "source_unit_sha256","ocr_page","exact_ocr_anchor",
             "source_anchor_match_count","scientific_content_firstpass")
            return Rows(many=[{**dict(zip(cols,v)),
               "independent_science_review_pending":True,"science_approved":False}
               for v in self.entries])
        raise AssertionError(sql)
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def executemany(self,sql,params):
        assert self.created and "ON CONFLICT DO NOTHING" in sql
        self.entries.extend(params)


def test_23_mechanism_sql_is_source_bounded_and_readback_without_approval(monkeypatch):
    monkeypatch.setattr(mech,"pinned_source_bytes",lambda fid,sha:sha=="a"*64)
    monkeypatch.setattr(mech,"unit_sha",lambda u:"b"*64)
    db=DB()
    r=mech.seed(db)
    assert len(db.entries)==23
    assert r["mechanism_content_firstpass_source_verified"]==23
    assert r["formal_science_approvals"]==0
    assert r["model_calls"]==0
    assert r["source_anchor_ambiguity_units"]==0


def test_different_ocr_bytes_or_missing_exact_mechanism_anchor_fail_closed(monkeypatch):
    monkeypatch.setattr(mech,"pinned_source_bytes",lambda fid,sha:False)
    with pytest.raises(PermissionError,match="exact_locked_textbook"):
        mech.seed(DB())
    monkeypatch.setattr(mech,"pinned_source_bytes",lambda *args:True)
    with pytest.raises(ValueError,match="exact_quote_not_found"):
        mech.seed(DB(missing_anchor=True))
