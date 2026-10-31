"""Original-page review proposals for all unmapped textbook OCR chapters.

Nearest mapped chapters are merely navigation hints. A cover, index, scan
omission or OCR delimiter cannot be repaired without inspecting original pages.
The 72 original QA chapter issues and their immutable states are untouched.
"""
from collections import defaultdict
from bisect import bisect_left
from app.source_policy import COLLECTION, pinned_source_bytes

KEYS = {i["key"]:i for i in COLLECTION["files"] if i["kind"]=="textbook"}
UNMAPPED = frozenset(("", "unclassified", "unknown", "contents"))
VERSION = "qa-chapter-neighbor/1"

DDL = """
CREATE TABLE IF NOT EXISTS qa_chapter_neighbor_proposals(
  issue_key text PRIMARY KEY REFERENCES qa_chapter_mapping_items(issue_key),
  algorithm text NOT NULL CHECK (algorithm='qa-chapter-neighbor/1'),
  source_sha256 text NOT NULL,
  ocr_page integer NOT NULL CHECK(ocr_page>0),
  previous_chapter text,
  previous_distance integer CHECK(previous_distance>=0),
  following_chapter text,
  following_distance integer CHECK(following_distance>=0),
  needs_original_visual boolean NOT NULL DEFAULT true CHECK(needs_original_visual=true),
  verified boolean NOT NULL DEFAULT false CHECK(verified=false)
);
"""


def derive(issues, nodes):
    indexed = defaultdict(lambda:defaultdict(set))
    for node in nodes:
        source = KEYS.get(node["source_key"])
        if source is None or not pinned_source_bytes(source["file_id"],node["source_sha256"]):
            raise ValueError("chapter_neighbor_unpinned_graph")
        if node["chapter"] not in UNMAPPED:
            indexed[(node["source_key"],node["source_sha256"])][node["ocr_page"]].add(node["chapter"])
    spans={}
    for key,by_page in indexed.items():
        pages=sorted(by_page)
        spans[key]=(pages,by_page)
    proposals=[]
    for issue in issues:
        source=KEYS.get(issue["source_key"])
        if source is None or not pinned_source_bytes(source["file_id"],issue["source_sha256"]):
            raise ValueError("chapter_neighbor_unpinned_issue")
        pages, by_page=spans.get((issue["source_key"],issue["source_sha256"]),([],{}))
        pos=bisect_left(pages,issue["ocr_page"])
        before=pages[pos-1] if pos>0 else None
        after=pages[pos] if pos<len(pages) else None
        # Do not guess among multiple disagreeing labels on one OCR page.
        prev_name=next(iter(by_page[before])) if before is not None and len(by_page[before])==1 else None
        next_name=next(iter(by_page[after])) if after is not None and len(by_page[after])==1 else None
        proposals.append((
            issue["issue_key"],VERSION,issue["source_sha256"],issue["ocr_page"],
            prev_name,issue["ocr_page"]-before if prev_name is not None else None,
            next_name,after-issue["ocr_page"] if next_name is not None else None,
        ))
    if len(set(p[0] for p in proposals))!=len(proposals):
        raise ValueError("chapter_neighbor_duplicate_issue")
    return proposals


def seed(conn):
    issues=conn.execute(
        "SELECT issue_key,source_key,source_sha256,ocr_page "
        "FROM qa_chapter_mapping_items ORDER BY source_key,ocr_page"
    ).fetchall()
    nodes=conn.execute(
        "SELECT source_key,source_sha256,chapter,ocr_page "
        "FROM qa_textbook_evidence_nodes ORDER BY source_key,ocr_page"
    ).fetchall()
    proposals=derive(issues,nodes)
    conn.execute(DDL)
    if proposals:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO qa_chapter_neighbor_proposals("
                "issue_key,algorithm,source_sha256,ocr_page,previous_chapter,"
                "previous_distance,following_chapter,following_distance) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                proposals,
            )
    stored=conn.execute(
        "SELECT issue_key,algorithm,source_sha256,ocr_page,previous_chapter,"
        "previous_distance,following_chapter,following_distance "
        "FROM qa_chapter_neighbor_proposals"
    ).fetchall()
    actual={tuple(row.values()) for row in stored}
    if len(actual)!=len(stored) or actual!=set(proposals):
        raise ValueError("chapter_neighbor_sql_provenance_mismatch")
    return {
        "unmapped_pages_with_navigation_records":len(proposals),
        "with_both_adjacent_labels":sum(p[4] is not None and p[6] is not None for p in proposals),
        "with_no_adjacent_labels":sum(p[4] is None and p[6] is None for p in proposals),
        "actual_original_page_approvals":0,
        "chapter_repairs_applied":0,
    }
