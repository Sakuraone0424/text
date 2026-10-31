"""Source-locked, append-only 72-page OCR navigation proposals (NOT science review).

An unnumbered introduction can contain actual biological science, and a
preface/contents page can itself mention scientific concepts. These labels are
pure navigation. They do NOT close QA chapter issues, verify missing figures,
repair OCR source bytes, or approve biological/chemical claims.
"""
from collections import Counter,defaultdict
from hashlib import sha256
from psycopg.types.json import Jsonb

from app.qa_chapter_context_inventory import (
    EXPECTED_PENDING_BY_BOOK, SUBSTANTIVE_UNNUMBERED, PART_TRANSITIONS,
)
from app.source_policy import COLLECTION, pinned_source_bytes

BOOKS={m["key"]:m["file_id"] for m in COLLECTION["files"] if m["kind"]=="textbook"}

DDL="""
CREATE TABLE IF NOT EXISTS qa_ocr_chapter_navigation_proposals (
  issue_key text PRIMARY KEY REFERENCES qa_chapter_mapping_items(issue_key),
  source_sha256 text NOT NULL,
  ocr_page integer NOT NULL CHECK(ocr_page>0),
  OCR_unit_ids jsonb NOT NULL,
  ocr_text_sha256 text NOT NULL,
  navigation_category text NOT NULL CHECK(navigation_category IN (
    'unnumbered_scientific_intro','part_transition_science_context',
    'frontmatter_or_contents')),
  proposed_ocr_label text NOT NULL,
  pending_science_review boolean NOT NULL DEFAULT true CHECK(pending_science_review),
  original_visual_verified boolean NOT NULL DEFAULT false CHECK(NOT original_visual_verified),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
"""


def navigation_label(key,page):
    if page in SUBSTANTIVE_UNNUMBERED.get(key,()):
        if key=="biochemistry_upper":
            return "unnumbered_scientific_intro","绪论（未编号正文）"
        return "unnumbered_scientific_intro","第三篇导言（未编号科学文字）"
    if page in PART_TRANSITIONS.get(key,()):
        title=("第一篇：蛋白质与核酸的结构和功能" if key=="biochemistry_upper"
               else "第三篇：糖类和脂质的结构与功能")
        return "part_transition_science_context",title+"（篇章过渡）"
    return "frontmatter_or_contents","前置材料、序言或目录（不冒充编号章节）"


def build(issues,units):
    source_rows=defaultdict(list)
    for u in units:
        ident=(u["source_key"],u["source_sha256"],u["ocr_page"])
        source_rows[ident].append(u)
    seen=set()
    counts=Counter()
    labels=Counter()
    records=[]
    for item in issues:
        key,page,source_sha=item["source_key"],item["ocr_page"],item["source_sha256"]
        if (key not in BOOKS or type(page) is not int or page<1
                or not pinned_source_bytes(BOOKS[key],source_sha)
                or item["issue_key"] in seen):
            raise ValueError("qa_ocr_72_chapter_issue_source_lock_failed")
        seen.add(item["issue_key"])
        counts[key]+=1
        blocks=sorted(source_rows.get((key,source_sha,page),[]),key=lambda u:u["ordinal"])
        if any(u["source_key"]!=key or u["source_sha256"]!=source_sha
               or u["ocr_page"]!=page for u in blocks):
            raise ValueError("qa_ocr_72_page_unit_provenance_invalid")
        category,label=navigation_label(key,page)
        labels[category]+=1
        text="\n\n".join(u["text"] for u in blocks)
        records.append(dict(
            issue_key=item["issue_key"],source_sha256=source_sha,ocr_page=page,
            unit_ids=[u["unit_id"] for u in blocks],ocr_text_sha256=sha256(text.encode()).hexdigest(),
            navigation_category=category,proposed_ocr_label=label,
        ))
    if dict(counts)!=EXPECTED_PENDING_BY_BOOK or len(records)!=72 or (
        labels!=Counter(unnumbered_scientific_intro=17,
                        part_transition_science_context=3,frontmatter_or_contents=52)
    ):
        raise ValueError("qa_ocr_72_chapter_classification_coverage_drift")
    return records


def seed(conn):
    issues=conn.execute(
        "SELECT issue_key,source_key,source_sha256,ocr_page "
        "FROM qa_chapter_mapping_items ORDER BY source_key,ocr_page"
    ).fetchall()
    units=conn.execute(
        "SELECT k.source_key,k.source_sha256,u.ocr_global_page AS ocr_page,"
        "u.id AS unit_id,u.ordinal,u.text "
        "FROM source_units u JOIN qa_textbook_evidence_nodes k ON k.unit_id=u.id "
        "WHERE u.ocr_global_page IS NOT NULL "
        "ORDER BY k.source_key,u.ocr_global_page,u.ordinal"
    ).fetchall()
    proposals=build(issues,units)
    conn.execute(DDL)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO qa_ocr_chapter_navigation_proposals("
            "issue_key,source_sha256,ocr_page,OCR_unit_ids,ocr_text_sha256,"
            "navigation_category,proposed_ocr_label)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [(p["issue_key"],p["source_sha256"],p["ocr_page"],Jsonb(p["unit_ids"]),
              p["ocr_text_sha256"],p["navigation_category"],p["proposed_ocr_label"])
             for p in proposals],
        )
    stored=conn.execute(
        "SELECT issue_key,source_sha256,ocr_page,OCR_unit_ids AS unit_ids,"
        "ocr_text_sha256,navigation_category,proposed_ocr_label "
        "FROM qa_ocr_chapter_navigation_proposals ORDER BY issue_key"
    ).fetchall()
    def ident(x):
        return (x["issue_key"],x["source_sha256"],x["ocr_page"],
                tuple(x["unit_ids"]),x["ocr_text_sha256"],
                x["navigation_category"],x["proposed_ocr_label"])
    if len(stored)!=72 or {ident(x) for x in stored}!={ident(x) for x in proposals}:
        raise ValueError("qa_ocr_72_chapter_navigation_sql_readback_failed")
    return dict(
        exact_six_source_navigation_proposals=len(stored),
        by_category=dict(sorted(Counter(p["navigation_category"] for p in proposals).items())),
        verified_scientific_chapter_repairs=0,original_visual_approvals=0,
        original_pending_chapter_issue_count=72,
    )
