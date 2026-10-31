"""Source-locked precise OCR-page roles for all 72 unnumbered pages.

Derived from a full manual page-content reading of the user's four exact OCR
textbooks; navigation ONLY, never source byte edits, missing-page certification,
independent science review or textbook figure repair.
"""
from collections import Counter
from psycopg.types.json import Jsonb
from app.source_policy import COLLECTION,pinned_source_bytes

BOOKS={x["key"]:x["file_id"] for x in COLLECTION["files"] if x["kind"]=="textbook"}
ROLES={
 "biochemistry_upper":{
  "cover":[1], "half_title":[2], "blank":[3], "publication_credits":[4],
  "abstract_and_copyright":[5], "series_preface":[6,7],
  "author_preface":[8,9,10,11,12,13], "contents":[14,15,16,17],
  "unnumbered_scientific_intro":list(range(18,34)),
  "part_science_introduction":[34,35]
 },
 "biochemistry_lower":{
  "cover":[1],"half_title":[2],"blank":[3],"publication_credits":[4],
  "abstract_and_copyright":[5],"series_preface":[6,7],
  "author_preface":[8,9,10,11,12,13],"contents":[14,15,16,17],
  "part_science_introduction":[18],"unnumbered_scientific_intro":[19]
 },
 "biochemistry_notes":{"cover":[1],"contents":[2]},
 "cell_biology":{
  "cover":[1],"publication_credits":[2],"abstract_and_copyright":[3],
  "half_title":[4],"copyright_notice":[5],"editor_bio":[6],
  "digital_course_guide":[7],"author_preface":[8,9,10],
  "gene_and_protein_nomenclature":[11],"contents":[12,13,14,15,16]
 }
}
CHAPTER_HINTS={
 ("biochemistry_upper","unnumbered_scientific_intro"):"绪论（OCR正文第18—33页）",
 ("biochemistry_upper","part_science_introduction"):"第一篇：蛋白质与核酸的结构和功能（OCR导言第34—35页）",
 ("biochemistry_lower","unnumbered_scientific_intro"):"第三篇：糖类和脂质的结构与功能（OCR导言第19页）",
 ("biochemistry_lower","part_science_introduction"):"第三篇：糖类和脂质的结构与功能（OCR导言第18页）",
}
EXPECTED=Counter({
 "cover":4,"half_title":3,"blank":2,"publication_credits":3,
 "abstract_and_copyright":3,"series_preface":4,"author_preface":15,
 "contents":14,"unnumbered_scientific_intro":17,
 "part_science_introduction":3,"copyright_notice":1,"editor_bio":1,
 "digital_course_guide":1,"gene_and_protein_nomenclature":1
})
BROAD={
 "unnumbered_scientific_intro":"unnumbered_scientific_intro",
 "part_science_introduction":"part_transition_science_context",
}
DDL="""
CREATE TABLE IF NOT EXISTS qa_ocr_chapter_precise_roles_72 (
 issue_key text PRIMARY KEY REFERENCES qa_ocr_chapter_navigation_proposals(issue_key),
 source_sha256 text NOT NULL,
 ocr_page integer NOT NULL CHECK (ocr_page>0),
 source_page_sha256 text NOT NULL,
 precise_role text NOT NULL,
 proposed_science_heading text,
 original_pdf_page_checked boolean NOT NULL DEFAULT false
   CHECK (NOT original_pdf_page_checked),
 independent_science_approved boolean NOT NULL DEFAULT false
   CHECK (NOT independent_science_approved),
 pending_source_review boolean NOT NULL DEFAULT true
   CHECK (pending_source_review),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
)
"""

def expected_map():
    pairs={}
    count=Counter()
    for key,roles in ROLES.items():
        if key not in BOOKS:raise ValueError("precise_map_unpinned_book")
        for role,pages in roles.items():
            for page in pages:
                if type(page) is not int or page<1 or (key,page) in pairs:
                    raise ValueError("precise_page_duplicate")
                pairs[key,page]=role
                count[role]+=1
    if count!=EXPECTED or len(pairs)!=72:
        raise ValueError("precise_72_role_count_drift")
    return pairs

def build(rows):
    roles=expected_map()
    records=[]
    seen=set()
    for row in rows:
        key,page,sha=row["source_key"],row["ocr_page"],row["source_sha256"]
        role=roles.get((key,page))
        if (role is None or row["issue_key"] in seen or
            not pinned_source_bytes(BOOKS[key],sha)):
            raise ValueError("precise_ocr_page_source_mismatch")
        seen.add(row["issue_key"])
        if row["navigation_category"]!=BROAD.get(role,"frontmatter_or_contents"):
            raise ValueError("precise_role_conflicts_existing_category")
        page_sha=row["ocr_text_sha256"]
        if not isinstance(page_sha,str) or len(page_sha)!=64:
            raise ValueError("precise_page_original_ocr_sha_required")
        records.append((
            row["issue_key"],sha,page,page_sha,role,
            CHAPTER_HINTS.get((key,role))
        ))
    if len(records)!=72 or len(seen)!=72 or len({(r[1],r[2]) for r in records})!=72:
        raise ValueError("precise_ocr_72_coverage_not_complete")
    return records

def seed(conn):
    rows=conn.execute(
        "SELECT a.issue_key,m.source_key,a.source_sha256,a.ocr_page,"
        "a.ocr_text_sha256,a.navigation_category "
        "FROM qa_ocr_chapter_navigation_proposals a "
        "JOIN qa_chapter_mapping_items m ON m.issue_key=a.issue_key "
        "ORDER BY m.source_key,a.ocr_page"
    ).fetchall()
    records=build(rows)
    conn.execute(DDL)
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO qa_ocr_chapter_precise_roles_72("
            "issue_key,source_sha256,ocr_page,source_page_sha256,"
            "precise_role,proposed_science_heading)"
            " VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            records)
    stored=conn.execute(
        "SELECT issue_key,source_sha256,ocr_page,source_page_sha256,"
        "precise_role,proposed_science_heading,original_pdf_page_checked,"
        "independent_science_approved,pending_source_review "
        "FROM qa_ocr_chapter_precise_roles_72 ORDER BY issue_key"
    ).fetchall()
    cols=("issue_key","source_sha256","ocr_page","source_page_sha256",
          "precise_role","proposed_science_heading")
    if len(stored)!=72 or {tuple(x[k] for k in cols) for x in stored}!=set(records):
        raise ValueError("precise_72_sql_readback_failed")
    if any(row["original_pdf_page_checked"] or row["independent_science_approved"]
           or not row["pending_source_review"] for row in stored):
        raise ValueError("precise_72_falsely_marked_science_approved")
    return dict(
        pages_with_precise_ocr_roles=72,
        nonnumbered_science_pages=20,
        frontmatter_pages=52,
        roles=dict(sorted(Counter(r["precise_role"] for r in stored).items())),
        source_ocr_sha_readback=True,
        independent_science_approvals=0,physical_pdf_page_approvals=0,
        official_chapter_issue_closures=0,model_calls=0,
    )
