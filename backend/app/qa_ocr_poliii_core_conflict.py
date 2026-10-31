"""A concrete unresolved scientific/OCR contradiction INSIDE the locked upper biochemistry book.

Page 311 calls DNA Pol III core alpha,epsilon,theta; the page 313
narrative calls it alpha,delta,epsilon while the figure transcript on the
same page returns alpha,epsilon,theta. Never silently 'repair' source OCR.
Cannot decide original visual printer error without unavailable original PDF.
"""
from app.ocr_sources import unit_sha
from app.source_policy import COLLECTION,pinned_source_bytes

SOURCE=next(s for s in COLLECTION["files"] if s["key"]=="biochemistry_upper")
QUOTES=(
    (311,"由 α、ε、θ 三个亚基构成"),
    (313,"除构成核心酶的 α、δ、ε 亚基"),
    (313,"DNA 聚合酶核心酶（α 5′→3′ 聚合酶活性、ε 3′→5′ 核酸外切酶活性、θ 核心酶组装）"),
)
DDL="""
CREATE TABLE IF NOT EXISTS qa_ocr_poliii_core_source_conflict (
 conflict_key text PRIMARY KEY,
 source_file_id text NOT NULL,
 source_sha256 text NOT NULL,
 scientific_topic text NOT NULL,
 issue_summary text NOT NULL,
 source_anchors jsonb NOT NULL,
 source_excerpts_sha256 text NOT NULL,
 original_visual_checked boolean NOT NULL DEFAULT false CHECK(NOT original_visual_checked),
 independent_science_approved boolean NOT NULL DEFAULT false CHECK(NOT independent_science_approved),
 pending_source_revision_review boolean NOT NULL DEFAULT true CHECK(pending_source_revision_review),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
)
"""
def seed(conn):
    import hashlib,json
    from psycopg.types.json import Jsonb
    doc=conn.execute(
        "SELECT id,sha256 FROM source_documents WHERE file_id=%s ORDER BY id DESC LIMIT 1",
        (SOURCE["file_id"],)).fetchone()
    if not doc or not pinned_source_bytes(SOURCE["file_id"],doc["sha256"]):
        raise PermissionError("poliii_conflict_not_from_exact_owner_OCR")
    originals=[]
    for p,q in QUOTES:
        found=conn.execute(
            "SELECT * FROM source_units WHERE document_id=%s AND "
            "ocr_global_page=%s AND strpos(text,%s)>0 ORDER BY ordinal,id",
            (doc["id"],p,q)).fetchall()
        if not found:
            raise ValueError("poliii_original_source_quote_missing")
        row=found[0]
        originals.append({"source_unit_id":row["id"],"source_unit_sha256":unit_sha(row),
                          "ocr_page":p,"exact_quote":q,"phrase_matches":len(found)})
    h=hashlib.sha256(json.dumps(originals,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    key="biochemistry_upper_poliii_alpha_delta_vs_theta_311_313"
    summary="OCR text conflicts: p311 core alpha epsilon theta; p313 prose alpha delta epsilon; p313 caption alpha epsilon theta. Scientific answer must not inherit p313 prose blindly."
    conn.execute(DDL)
    conn.execute(
        "INSERT INTO qa_ocr_poliii_core_source_conflict("
        "conflict_key,source_file_id,source_sha256,scientific_topic,"
        "issue_summary,source_anchors,source_excerpts_sha256)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (key,SOURCE["file_id"],doc["sha256"],"E. coli DNA Pol III core",summary,Jsonb(originals),h))
    row=conn.execute(
        "SELECT * FROM qa_ocr_poliii_core_source_conflict WHERE conflict_key=%s",
        (key,)).fetchone()
    if (not row or row["source_file_id"]!=SOURCE["file_id"]
        or row["source_sha256"]!=doc["sha256"]
        or row["source_anchors"]!=originals
        or row["source_excerpts_sha256"]!=h
        or row["independent_science_approved"]
        or row["original_visual_checked"]
        or not row["pending_source_revision_review"]):
        raise ValueError("poliii_scientific_source_conflict_sql_readback_failed")
    return dict(real_textbook_internal_conflicts=1,source_original_quotes=3,
                independent_science_approvals=0,original_pdf_images_verified=0,
                question_autoscoring_allowed=False,model_calls=0)
