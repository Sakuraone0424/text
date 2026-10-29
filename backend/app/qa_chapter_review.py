"""QA-only tracking for every chapter-unmapped OCR page.

The 72 unmapped pages are NOT necessarily missing textbook content: covers,
tables of contents and frontmatter are legitimate. This records each page with
source SHA, page number, and its relation to the first recognized body chapter;
scientific/source visual review remains pending.
"""
import json
import os
from hashlib import sha256
from app.source_intake import SCHEMA, isolated_db
from app.source_readiness import audit as source_audit
from app.source_policy import COLLECTION, pinned_source_bytes

DDL = """
CREATE TABLE IF NOT EXISTS qa_chapter_mapping_items(
  issue_key text PRIMARY KEY,
  source_key text NOT NULL,
  source_sha256 text NOT NULL,
  ocr_page integer NOT NULL CHECK(ocr_page>0),
  provisional_class text NOT NULL CHECK(
    provisional_class IN ('before_first_body','after_or_no_body')),
  baseline_state text NOT NULL DEFAULT 'pending' CHECK(baseline_state='pending'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(source_key,source_sha256,ocr_page)
);
CREATE TABLE IF NOT EXISTS qa_chapter_mapping_events(
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  issue_key text NOT NULL REFERENCES qa_chapter_mapping_items(issue_key),
  reviewer text NOT NULL CHECK(length(trim(reviewer)) >= 3),
  verified_category text NOT NULL CHECK(
    verified_category IN ('confirmed_frontmatter','chapter_mapping_repaired',
                         'scan_missing_or_ambiguous','needs_original_visual')),
  original_checked boolean NOT NULL DEFAULT false,
  reason text NOT NULL CHECK(length(trim(reason))>=30),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE OR REPLACE VIEW qa_chapter_mapping_progress AS
 SELECT i.issue_key,i.source_key,i.ocr_page,i.provisional_class,
 CASE WHEN EXISTS(
   SELECT 1 FROM qa_chapter_mapping_events e
   WHERE e.issue_key=i.issue_key AND e.original_checked
     AND e.verified_category IN ('confirmed_frontmatter','chapter_mapping_repaired')
 ) THEN 'reviewed_context_not_automatically_science_approved'
 ELSE 'pending' END AS review_state
 FROM qa_chapter_mapping_items i;
"""

def rows_from_report(report):
    expected={x["key"]:x for x in COLLECTION["files"] if x["kind"]=="textbook"}
    rows=[]
    for d in report["files"]:
        if d["key"] not in expected:
            continue
        if not pinned_source_bytes(expected[d["key"]]["file_id"],d["source_sha256"]):
            raise ValueError("Unpinned textbook in chapter mapping")
        before=set(d["chapter_unmapped_before_body"])
        after=set(d["chapter_unmapped_at_or_after_body"])
        unclassified=set(d["chapter_unmapped_ocr_pages"])
        if before & after or before|after != unclassified:
            raise ValueError("Inconsistent chapter-mapping counts")
        for page in sorted(unclassified):
            status="before_first_body" if page in before else "after_or_no_body"
            fingerprint=sha256(
                f"{d['source_sha256']}|chapter_missing|{page}".encode("utf-8")
            ).hexdigest()
            rows.append((fingerprint,d["key"],d["source_sha256"],page,status))
    if len(rows)!=len({r[0] for r in rows}):
        raise ValueError("Duplicate QA chapter issue")
    return rows

def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME","").strip().lower()!="qa":
        raise SystemExit("qa_chapter_review_refuses_non_qa")
    if os.environ.get("QA_IMPORT_ENABLED")!="0":
        raise SystemExit("qa_chapter_review_requires_upload_disabled")
    schema=os.environ.get("QA_IMPORT_SCHEMA","")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_chapter_review_requires_isolated_schema")
    with isolated_db(schema,"staging") as conn:
        data=source_audit(conn)
        if data["imported_files"]!=6 or data["integrity_passed_files"]!=6:
            raise ValueError("Six-source QA integrity gate failed")
        rows=rows_from_report(data)
        if len(rows)!=72:
            raise ValueError("Expected 72 known chapter-unmapped OCR pages; investigate drift")
        conn.execute(DDL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO qa_chapter_mapping_items("
                "issue_key,source_key,source_sha256,ocr_page,provisional_class)"
                " VALUES(%s,%s,%s,%s,%s) ON CONFLICT(issue_key) DO NOTHING",
                rows,
            )
        count=conn.execute(
            "SELECT count(*) AS n FROM qa_chapter_mapping_items"
        ).fetchone()["n"]
        if count!=len(rows):
            raise ValueError("QA chapter issue seed incomplete")
    with isolated_db(schema,"staging",readonly=True) as conn:
        counts=conn.execute(
            "SELECT count(*) AS n,"
            "count(*) FILTER(WHERE review_state='pending') AS pending "
            "FROM qa_chapter_mapping_progress"
        ).fetchone()
        if counts["n"]!=len(rows):
            raise ValueError("QA chapter issue postcommit readback mismatch")
    print("QA_CHAPTER_REVIEW_SQL_READBACK="+json.dumps({
        "status":"all_chapter_unmapped_pages_tracked_in_qa",
        "tracked_unmapped_pages":counts["n"],
        "pending":counts["pending"],
        "source_content_exported":False,
        "scientific_review_completed":False,
    },sort_keys=True,separators=(",",":")),flush=True)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
