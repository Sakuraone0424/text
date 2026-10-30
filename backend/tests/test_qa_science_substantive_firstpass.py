"""Substantive biology/chemistry first-pass review, not OCR-regex bookkeeping.

No source file may be modified, no independent-human signature inferred, and
an active source SHA/page/text-unit fingerprint is required before persistence.
"""
import pytest
from app import qa_science_substantive_firstpass as science


def fake_unit(index):
    return {
        "id": 100 + index, "document_id": 8 + index, "ordinal": index,
        "chapter": "待审章节", "section": "科学论证",
        "text": "教材原句："+science.CASES[index-1][3]+"。其他上下文段落",
        "ocr_global_page": science.CASES[index-1][2],
        "quality_flags": ["scientific_notation_requires_source_review"],
    }


class ScienceConn:
    def __init__(self, modified=False):
        self.modified = modified
        self.packets = []
        self.stored = []
    def execute(self, sql, params=None):
        if sql.startswith("SELECT id,sha256 FROM source_documents"):
            return Rows(one={"id": 8 + next(
                i for i,c in enumerate(science.CASES,1)
                if science.BOOKS[c[1]] == params[0]
            ),"sha256":"a"*64})
        if sql.startswith("SELECT * FROM source_units"):
            page=params[1]
            phrase=params[2]
            match=next(i for i,c in enumerate(science.CASES,1)
                       if c[2]==page and c[3]==phrase)
            if self.modified and match==1:
                return Rows(many=[])
            return Rows(many=[fake_unit(match)])
        if "CREATE TABLE IF NOT EXISTS" in sql:
            return Rows()
        if sql.startswith("SELECT finding_id"):
            return Rows(many=[{**dict(zip((
                "finding_id","source_file_id","source_sha256","ocr_page",
                "source_unit_id","source_unit_sha256","exact_ocr_phrase",
                "proposed_scientific_correction"), p)),
                "independent_science_review_pending":True,
                "formally_approved":False,
            } for p in sorted(self.packets)])
        raise AssertionError(sql)
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def executemany(self,sql,parameters):
        assert "ON CONFLICT DO NOTHING" in sql
        assert "independent_science_review_pending" not in sql
        self.packets.extend(parameters)


class Rows:
    def __init__(self,one=None,many=None):
        self.one=one
        self.many=many if many is not None else []
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.many


def test_ten_real_scientific_cases_have_page_quote_and_five_or_more_distinct_science_topics():
    assert len(science.CASES)==10
    assert len(set(c[0] for c in science.CASES))==10
    assert all(c[1] in science.BOOKS and c[2]>0 and len(c[3])>5
               and len(c[4])>=25 for c in science.CASES)
    assert len(set(c[1] for c in science.CASES))==4
    assert "CHECK (NOT formally_approved)" in science.DDL


def test_six_source_science_findings_are_inserted_pending_and_readback(monkeypatch):
    monkeypatch.setattr(science,"pinned_source_bytes",lambda fid,sha:sha=="a"*64)
    result=science.seed(ScienceConn())
    assert result["substantive_science_corrections_source_attested"]==10
    assert result["scientific_double_approvals"]==0
    assert result["published_questions"]==0
    assert result["model_calls"]==0


def test_science_findings_fail_closed_before_sql_when_source_hash_or_quote_changed(monkeypatch):
    monkeypatch.setattr(science,"pinned_source_bytes",lambda *a:False)
    with pytest.raises(PermissionError,match="locked_six_sources"):
        science.seed(ScienceConn())
    monkeypatch.setattr(science,"pinned_source_bytes",lambda *a:True)
    with pytest.raises(ValueError,match="quote_not_one_exact"):
        science.seed(ScienceConn(modified=True))
