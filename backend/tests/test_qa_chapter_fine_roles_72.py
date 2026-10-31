"""Real 72-page OCR role granularity with source hash, exact existing QA issues,
and no claim about missing original PDF pages or independent approval.
"""
from collections import Counter
from hashlib import sha256
import pytest
from app import qa_chapter_fine_roles_72 as page

class Rows:
    def __init__(self,one=None,many=None):
        self.one=one;self.many=[] if many is None else many
    def fetchone(self):return self.one
    def fetchall(self):return self.many

def fake_original_proposals():
    records=[]
    for (source,index),role in sorted(page.expected_map().items()):
        records.append({
            "issue_key":f"{source}/{index}",
            "source_key":source,
            "ocr_page":index,
            "source_sha256":sha256(source.encode("utf-8")).hexdigest(),
            "ocr_text_sha256":f"{index:064x}",
            "navigation_category":page.BROAD.get(role,"frontmatter_or_contents"),
        })
    return records

class DB:
    def __init__(self,rows=None):
        self.original=rows if rows is not None else fake_original_proposals()
        self.stored=[]
    def execute(self,sql,values=None):
        if sql.startswith("SELECT a.issue_key,m.source_key"):
            return Rows(many=self.original)
        if sql.lstrip().startswith("CREATE TABLE"):
            return Rows()
        if sql.startswith("SELECT issue_key,source_sha256,ocr_page"):
            cols=("issue_key","source_sha256","ocr_page","source_page_sha256",
                  "precise_role","proposed_science_heading")
            return Rows(many=[{**dict(zip(cols,r)),
                 "original_pdf_page_checked":False,
                 "independent_science_approved":False,"pending_source_review":True}
                 for r in self.stored])
        raise AssertionError(sql)
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def executemany(self,sql,items):
        assert "ON CONFLICT DO NOTHING" in sql
        self.stored.extend(items)


def test_all_72_precise_roles_accounted_for_and_intro_not_labeled_preface():
    roles=page.expected_map()
    assert len(roles)==72
    assert Counter(roles.values())==page.EXPECTED
    assert all(roles["biochemistry_upper",i]=="unnumbered_scientific_intro"
               for i in range(18,34))
    assert all(roles["biochemistry_upper",i]=="part_science_introduction"
               for i in (34,35))
    assert roles["biochemistry_lower",18]=="part_science_introduction"
    assert roles["biochemistry_lower",19]=="unnumbered_scientific_intro"
    assert "CHECK (NOT independent_science_approved)" in page.DDL
    assert "CHECK (NOT original_pdf_page_checked)" in page.DDL


def test_72_ocr_roles_sql_real_readback_no_science_signatures(monkeypatch):
    monkeypatch.setattr(page,"pinned_source_bytes",lambda fid,sha:any(
        page.BOOKS[key]==fid and sha256(key.encode("utf-8")).hexdigest()==sha
        for key in page.BOOKS))
    fake=DB()
    result=page.seed(fake)
    assert len(fake.stored)==72
    assert result["pages_with_precise_ocr_roles"]==72
    assert result["nonnumbered_science_pages"]==20
    assert result["frontmatter_pages"]==52
    assert result["source_ocr_sha_readback"]
    assert result["independent_science_approvals"]==0
    assert result["physical_pdf_page_approvals"]==0
    assert result["official_chapter_issue_closures"]==0


def test_72_page_missing_or_modified_source_or_wrong_existing_kind_fails_closed(monkeypatch):
    monkeypatch.setattr(page,"pinned_source_bytes",lambda *a:False)
    with pytest.raises(ValueError,match="source_mismatch"):
        page.seed(DB())
    monkeypatch.setattr(page,"pinned_source_bytes",lambda *a:True)
    missing=fake_original_proposals()[:-1]
    with pytest.raises(ValueError,match="coverage_not_complete"):
        page.seed(DB(missing))
    wrong=fake_original_proposals()
    wrong[0]={**wrong[0],"navigation_category":"unnumbered_scientific_intro"}
    with pytest.raises(ValueError,match="conflicts_existing"):
        page.seed(DB(wrong))
