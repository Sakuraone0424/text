"""Real internal alpha-theta vs alpha-delta Pol III OCR conflict is never auto-approved."""
import pytest
from app import qa_ocr_poliii_core_conflict as c

class Rows:
    def __init__(self,one=None,many=None):
        self.one=one;self.many=[] if many is None else many
    def fetchone(self):return self.one
    def fetchall(self):return self.many

class DB:
    def __init__(self,missing=False):
        self.saved=None
        self.missing=missing
    def execute(self,sql,values=None):
        if sql.startswith("SELECT id,sha256 FROM source_documents"):
            assert values == (c.SOURCE["file_id"],)
            return Rows(one={"id":7,"sha256":"a"*64})
        if sql.startswith("SELECT * FROM source_units"):
            if self.missing:return Rows(many=[])
            assert values[0]==7
            return Rows(many=[{"id":100+values[1],"document_id":7,
                               "text":values[2],"ordinal":values[1]}])
        if sql.lstrip().startswith("CREATE TABLE"):
            return Rows()
        if sql.startswith("INSERT INTO qa_ocr_poliii_core_source_conflict"):
            from psycopg.types.json import Jsonb
            k,f,sha,topic,summary,jsonb,hash_=values
            assert isinstance(jsonb,Jsonb)
            self.saved={"conflict_key":k,"source_file_id":f,
                        "source_sha256":sha,"source_anchors":jsonb.obj,
                        "source_excerpts_sha256":hash_,
                        "original_visual_checked":False,
                        "independent_science_approved":False,
                        "pending_source_revision_review":True}
            return Rows()
        if sql.startswith("SELECT * FROM qa_ocr_poliii_core_source_conflict"):
            return Rows(one=self.saved)
        raise AssertionError(sql)

def test_three_actual_conflicting_anchor_statements_have_distinct_roles():
    assert len(c.QUOTES)==3
    assert c.QUOTES[0][0]==311
    assert c.QUOTES[1][0]==c.QUOTES[2][0]==313
    assert "α、ε、θ" in c.QUOTES[0][1]
    assert "α、δ、ε" in c.QUOTES[1][1]
    assert "θ" in c.QUOTES[2][1]
    assert "CHECK(NOT independent_science_approved)" in c.DDL
    assert "CHECK(NOT original_visual_checked)" in c.DDL


def test_contradictory_text_is_inserted_and_readback_not_certified(monkeypatch):
    monkeypatch.setattr(c,"pinned_source_bytes",lambda fid,sha:sha=="a"*64)
    monkeypatch.setattr(c,"unit_sha",lambda row:"b"*64)
    result=c.seed(DB())
    assert result["real_textbook_internal_conflicts"]==1
    assert result["source_original_quotes"]==3
    assert result["question_autoscoring_allowed"] is False
    assert result["independent_science_approvals"]==0


def test_missing_quote_or_changed_book_blocks_silently_accepted_science(monkeypatch):
    monkeypatch.setattr(c,"pinned_source_bytes",lambda *a:False)
    with pytest.raises(PermissionError,match="exact_owner_OCR"):
        c.seed(DB())
    monkeypatch.setattr(c,"pinned_source_bytes",lambda *a:True)
    with pytest.raises(ValueError,match="original_source_quote_missing"):
        c.seed(DB(missing=True))
