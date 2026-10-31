"""QA-only canonical textbook evidence graph: every OCR unit, never AI facts.

All 9,655 user-locked textbook units become linked source nodes in the
separate QA PostgreSQL. Facets are regex-based REVIEW CANDIDATES, not
approved definitions or mechanism edges. Raw textbook content remains in
the already imported source_units table and is never printed or duplicated.
"""
from collections import Counter
from hashlib import sha256
import json
import os
import re

from app.source_intake import SCHEMA, isolated_db
from app.source_readiness import audit as source_audit
from app.source_policy import COLLECTION, pinned_source_bytes

FACET_PATTERNS = {
    "definition": r"称为|是指|被定义为|定义为|所谓|指的是|又称为",
    "mechanism": r"催化|激活|结合|通过.+[实完]现|作用机制|协同|构象变化",
    "sequence_process": r"首先|接着|随后|最终|步骤|依次|整个过程|循环途径",
    "comparison": r"相比|不同于|差别在于|与.+不同|共同点|相同之处|相反的是",
    "regulation": r"调控|调节|抑制|反馈|激活|诱导|磷酸化",
    "cellular_localization": r"定位|核定位|内质网|线粒体|细胞核|胞质|细胞质|细胞膜",
    "experimental_method": r"实验|电泳|离心|显微|检测|免疫印迹|荧光标记",
    "molecular_structure": r"结构|构象|亚基|结构域|复合物|组装",
}
COMPILED = {k:re.compile(v,re.I) for k,v in FACET_PATTERNS.items()}
BOOKS = {s["file_id"]:s for s in COLLECTION["files"] if s["kind"]=="textbook"}

DDL = """
CREATE TABLE IF NOT EXISTS qa_textbook_evidence_nodes (
  unit_id bigint PRIMARY KEY REFERENCES source_units(id),
  source_key text NOT NULL,
  source_sha256 text NOT NULL,
  source_text_sha256 text NOT NULL,
  subject text NOT NULL,
  chapter text NOT NULL,
  ocr_page integer NOT NULL CHECK (ocr_page>0),
  flagged_risks integer NOT NULL DEFAULT 0 CHECK(flagged_risks>=0),
  scientific_review_state text NOT NULL DEFAULT 'pending'
    CHECK(scientific_review_state='pending')
);
CREATE INDEX IF NOT EXISTS qa_textbook_nodes_chapter_page
 ON qa_textbook_evidence_nodes(source_key,chapter,ocr_page);
CREATE TABLE IF NOT EXISTS qa_textbook_facet_candidates(
  unit_id bigint NOT NULL REFERENCES qa_textbook_evidence_nodes(unit_id),
  facet text NOT NULL,
  reason text NOT NULL DEFAULT 'literal_regex_not_science',
  scientific_verified boolean NOT NULL DEFAULT false CHECK(scientific_verified=false),
  PRIMARY KEY(unit_id,facet)
);
CREATE INDEX IF NOT EXISTS qa_textbook_facets_kind
 ON qa_textbook_facet_candidates(facet);
CREATE OR REPLACE VIEW qa_textbook_chapter_inventory AS
 SELECT source_key,subject,chapter,count(*) AS text_units,
        min(ocr_page) AS first_ocr_page,max(ocr_page) AS last_ocr_page,
        count(*) FILTER(WHERE flagged_risks>0) AS risk_flagged_units,
        false AS science_approved
 FROM qa_textbook_evidence_nodes GROUP BY source_key,subject,chapter;
"""


def prepare_rows(raw):
    nodes = []
    facets = []
    chapters=set()
    for row in raw:
        item=BOOKS.get(row["file_id"])
        if not item or not pinned_source_bytes(row["file_id"],row["source_sha256"]):
            raise ValueError("QA knowledge graph contains unpinned or nontextbook material")
        if row["ocr_page"] is None or row["ocr_page"] < 1:
            raise ValueError("QA textbook knowledge cannot lack a source OCR page")
        chapter=row["chapter"] or "unclassified"
        flags=row["quality_flags"] or []
        digest=sha256(row["text"].encode("utf-8")).hexdigest()
        nodes.append((
            row["unit_id"],item["key"],row["source_sha256"],digest,
            item["subject"],chapter,row["ocr_page"],len(flags),
        ))
        chapters.add((item["key"],chapter))
        for kind,pattern in COMPILED.items():
            if pattern.search(row["text"]):
                facets.append((row["unit_id"],kind))
    if len({n[0] for n in nodes}) != len(nodes) or len(set(facets)) != len(facets):
        raise ValueError("Duplicate source units or graph facet candidates")
    return nodes, facets, chapters


def main():
    if os.environ.get("RAILWAY_ENVIRONMENT_NAME","").strip().lower()!="qa":
        raise SystemExit("qa_graph_refused_non_qa_environment")
    if os.environ.get("QA_IMPORT_ENABLED")!="0":
        raise SystemExit("qa_graph_requires_upload_disabled")
    schema=os.environ.get("QA_IMPORT_SCHEMA","")
    if not SCHEMA.fullmatch(schema):
        raise SystemExit("qa_graph_requires_isolated_schema")
    with isolated_db(schema,"staging") as conn:
        report=source_audit(conn)
        if report["imported_files"]!=6 or report["integrity_passed_files"]!=6:
            raise ValueError("Six-source QA source integrity required")
        source_rows=conn.execute(
            "SELECT u.id AS unit_id,d.file_id,d.sha256 AS source_sha256,"
            "d.subject,u.chapter,u.text,u.quality_flags,"
            "u.ocr_global_page AS ocr_page "
            "FROM source_units u JOIN source_documents d ON d.id=u.document_id "
            "ORDER BY u.id"
        ).fetchall()
        books=[r for r in source_rows if r["file_id"] in BOOKS]
        if len(books)!=9655 or len(source_rows)!=12396:
            raise ValueError("QA textbook units or entire source collection drifted")
        nodes,facets,chapters=prepare_rows(books)
        if len(nodes)!=9655 or len(chapters)!=83:
            raise ValueError("Original OCR chapter/unit graph scope drifted")
        conn.execute(DDL)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO qa_textbook_evidence_nodes("
                "unit_id,source_key,source_sha256,source_text_sha256,"
                "subject,chapter,ocr_page,flagged_risks"
                ") VALUES(%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT(unit_id) DO NOTHING",
                nodes,
            )
            cur.executemany(
                "INSERT INTO qa_textbook_facet_candidates(unit_id,facet)"
                " VALUES(%s,%s) ON CONFLICT(unit_id,facet) DO NOTHING",
                facets,
            )
        counts=conn.execute(
            "SELECT count(*) AS nodes,"
            "count(*) FILTER(WHERE scientific_review_state='pending') AS pending "
            "FROM qa_textbook_evidence_nodes"
        ).fetchone()
        facet_count=conn.execute(
            "SELECT count(*) AS n FROM qa_textbook_facet_candidates"
        ).fetchone()["n"]
        if counts["nodes"]!=len(nodes) or facet_count!=len(facets):
            raise ValueError("QA graph nodes or facets not fully materialized")
    with isolated_db(schema,"staging",readonly=True) as conn:
        graph=conn.execute(
            "SELECT count(*) AS nodes,"
            "count(*) FILTER(WHERE scientific_review_state='pending') AS pending "
            "FROM qa_textbook_evidence_nodes"
        ).fetchone()
        facets_readback=conn.execute(
            "SELECT count(*) AS n FROM qa_textbook_facet_candidates"
        ).fetchone()["n"]
        chapter_count=conn.execute(
            "SELECT count(*) AS n FROM qa_textbook_chapter_inventory"
        ).fetchone()["n"]
        if (graph["nodes"],graph["pending"],facets_readback,chapter_count)!=(
            len(nodes),len(nodes),len(facets),len(chapters)
        ):
            raise ValueError("Postcommit graph readback does not match source evidence")
    print("QA_TEXTBOOK_GRAPH_SQL_READBACK="+json.dumps({
        "status":"all_four_original_textbooks_unit_nodes_postcommit_verified",
        "evidence_nodes":len(nodes),
        "chapter_groups":len(chapters),
        "lexical_semantic_facet_candidates":len(facets),
        "literal_facet_parity_with_independent_offline":len(facets)==12896,
        "scientifically_approved_nodes":0,
        "chapter_and_definitions_scientifically_reviewed":False,
        "original_text_logged":False,
    },sort_keys=True,separators=(",",":")),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
