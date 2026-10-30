"""Source-locked OCR formula risk triage, never chemical correctness certification.

Detect visually sensitive equations/redox notation and obvious replacement
glyphs from the six real textbooks. All findings stay PENDING until original
page/figure and scientific meaning are independently compared. No model APIs.
"""
import re
from collections import Counter
from hashlib import sha256
from app.source_policy import pinned_source_bytes, current_source_sql

VERSION = "qa-formula-visual/1"
ARROWS = re.compile(r"[→←↔⇌⇄⟶⟷⇒]")
COFACTORS = re.compile(r"(?<![A-Za-z])(?:NADPH|NADP\+|NADH|NAD\+|FADH2?|ATP|ADP|GTP|GDP)(?![A-Za-z])", re.I)
BAD_GLYPHS = re.compile(r"[�□]{1,}")
FORMULA_MARKS = re.compile(r"(?:[+=]|[⁺⁻²³₊₋₂₃])")

DDL = """
CREATE TABLE IF NOT EXISTS qa_formula_visual_review_candidates(
  unit_id bigint NOT NULL REFERENCES source_units(id),
  check_code text NOT NULL,
  algorithm text NOT NULL CHECK(algorithm='qa-formula-visual/1'),
  source_sha256 text NOT NULL,
  text_sha256 text NOT NULL,
  ocr_page integer NOT NULL CHECK(ocr_page>0),
  review_state text NOT NULL DEFAULT 'pending' CHECK(review_state='pending'),
  scientifically_verified boolean NOT NULL DEFAULT false CHECK(scientifically_verified=false),
  PRIMARY KEY(unit_id,check_code,algorithm)
);
CREATE INDEX IF NOT EXISTS qa_formula_review_by_page
  ON qa_formula_visual_review_candidates(ocr_page,check_code);
"""


def classify(text):
    """Flags are inspection requests, not proof the transcription is wrong."""
    text = text or ""
    codes = set()
    if BAD_GLYPHS.search(text):
        codes.add("replacement_or_missing_glyph_check")
    if ARROWS.search(text):
        codes.add("reaction_direction_original_visual_check")
        if COFACTORS.search(text):
            codes.add("reaction_cofactor_charge_original_visual_check")
    if COFACTORS.search(text) and FORMULA_MARKS.search(text):
        codes.add("cofactor_stoichiometry_notation_check")
    return tuple(sorted(codes))


def build(rows):
    out = []
    by_class = Counter()
    for unit in rows:
        if not pinned_source_bytes(unit["file_id"], unit["source_sha256"]):
            raise ValueError("formula_audit_unpinned_textbook")
        if type(unit["ocr_page"]) is not int or unit["ocr_page"] <= 0:
            raise ValueError("formula_audit_missing_source_page")
        for code in classify(unit["text"]):
            out.append((unit["unit_id"], code, VERSION, unit["source_sha256"],
                        sha256(unit["text"].encode("utf-8")).hexdigest(),
                        unit["ocr_page"]))
            by_class[code] += 1
    if len(set((x[0],x[1]) for x in out)) != len(out):
        raise ValueError("formula_audit_duplicate_unit_risk")
    return out, dict(sorted(by_class.items()))


def seed(conn):
    rows = conn.execute(
        "SELECT u.id AS unit_id,d.file_id,d.sha256 AS source_sha256,"
        "u.ocr_global_page AS ocr_page,u.text FROM source_units u "
        "JOIN source_documents d ON d.id=u.document_id WHERE " +
        current_source_sql("d", kind="textbook") + " ORDER BY u.id"
    ).fetchall()
    candidates, by_kind = build(rows)
    conn.execute(DDL)
    if candidates:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO qa_formula_visual_review_candidates("
                "unit_id,check_code,algorithm,source_sha256,text_sha256,ocr_page) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                candidates,
            )
    found = conn.execute(
        "SELECT unit_id,check_code,algorithm,source_sha256,text_sha256,ocr_page "
        "FROM qa_formula_visual_review_candidates WHERE algorithm=%s", (VERSION,),
    ).fetchall()
    readback = {tuple(row[k] for k in ("unit_id","check_code","algorithm",
             "source_sha256","text_sha256","ocr_page")) for row in found}
    if len(found) != len(readback) or readback != set(candidates):
        raise ValueError("formula_audit_sql_provenance_mismatch")
    return {
        "pending_visual_candidates": len(candidates),
        "by_kind": by_kind,
        "actual_formula_correctness_certified": False,
    }
