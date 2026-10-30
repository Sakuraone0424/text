"""Ten original-six-source, substantive SCIENCE errata proposals.

These are content judgments, not 5,094 regex classifications: chemistry
stoichiometry, transcription/translation mechanism, review qualifiers, and
genuine-exam answer ambiguity. They do not amend user source bytes, grant
independent approval, or publish questions. First-pass assistant review ONLY.
"""
import hashlib
from psycopg.types.json import Jsonb
from app.ocr_sources import unit_sha
from app.source_policy import COLLECTION, pinned_source_bytes

BOOKS = {i["key"]: i["file_id"]
         for i in COLLECTION["files"] if i["kind"] == "textbook"}

CASES = (
    ("SCI-001","biochemistry_lower",326,"相当于消耗了 2 分子 ATP",
     "脂肪酸活化：实际1 ATP分子->AMP+PPi；2高能键/ATP当量，不能按2 ATP分子计数"),
    ("SCI-002","biochemistry_notes",272,"总消耗能量：2n ATP（活化）",
     "n个氨基酸活化实际消耗n个ATP分子、2n个高能磷酸键当量；分子/当量不可混淆"),
    ("SCI-003","cell_biology",222,"在所有体细胞里则尚未发现端粒酶的活性",
     "绝对断言过度；同页讨论肿瘤细胞可表达端粒酶；不得写成所有体细胞均无"),
    ("SCI-004","biochemistry_lower",166,"葡萄糖-6-磷酸 + 2NADP⁺ + H₂O ⟶ 核酮糖-5-磷酸",
     "戊糖磷酸氧化段直接Ru5P；R5P由后续异构生成；分清直接与功能性产物"),
    ("SCI-005","biochemistry_lower",393,"在 HDL 被肝细胞摄取之后",
     "HDL逆向胆固醇转运不等于SR-BI使整颗HDL典型受体介导胞吞；同页明确不引发胞吞"),
    ("SCI-006","biochemistry_notes",278,"操纵子的前导区内类似于终止子结构的一段 DNA 序列",
     "DNA前导调控序列与RNA的2-3反终止/3-4终止发夹不同，不能混淆"),
    ("SCI-007","biochemistry_notes",272,"大多数真核生物 mRNA 中含 RBS",
     "真核常规翻译起始应参考上册562/564页5′帽、40S扫描/Kozak，不应误用SD"),
    ("SCI-008","cell_biology",134,"线粒体数目和体积调控的生物学意义尚不完全清楚",
     "有关分布/遗传共享意义需保留“可能”限定，QA线粒体9分题已改为追加v3待审"),
    ("SCI-009","biochemistry_upper",129,"G 蛋白含 α",
     "2017年生化‘G蛋白’题不限定异三聚体；上册还给出Ran单体小G蛋白例子"),
    ("SCI-010","biochemistry_upper",483,"RNA 剪接的化学反应本身不需要消耗能量",
     "两步转酯化学本身无直接ATP消耗；剪接体组装/重排却需ATP，避免二选一误判"),
)

DDL = """
CREATE TABLE IF NOT EXISTS qa_science_substantive_firstpass_findings (
    finding_id text PRIMARY KEY,
    source_file_id text NOT NULL,
    source_sha256 text NOT NULL,
    ocr_page integer NOT NULL CHECK (ocr_page > 0),
    source_unit_id bigint NOT NULL REFERENCES source_units(id),
    source_unit_sha256 text NOT NULL,
    exact_ocr_phrase text NOT NULL,
    proposed_scientific_correction text NOT NULL,
    independent_science_review_pending boolean NOT NULL DEFAULT true
        CHECK (independent_science_review_pending),
    formally_approved boolean NOT NULL DEFAULT false CHECK (NOT formally_approved),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
)
"""

def build(conn):
    if len(CASES) != 10 or len({c[0] for c in CASES}) != 10:
        raise ValueError("substantive_review_10_case_drift")
    records=[]
    for case_id,key,page,phrase,correction in CASES:
        if key not in BOOKS or not phrase or len(correction) < 25:
            raise ValueError("science_erratum_invalid_source_or_note")
        fid=BOOKS[key]
        doc=conn.execute(
            "SELECT id,sha256 FROM source_documents WHERE file_id=%s ORDER BY id DESC LIMIT 1",
            (fid,),
        ).fetchone()
        if not doc or not pinned_source_bytes(fid,doc["sha256"]):
            raise PermissionError("science_erratum_not_locked_six_sources")
        blocks=conn.execute(
            "SELECT * FROM source_units WHERE document_id=%s "
            "AND ocr_global_page=%s AND strpos(text,%s)>0 ORDER BY ordinal",
            (doc["id"],page,phrase),
        ).fetchall()
        if len(blocks)!=1:
            raise ValueError("science_erratum_quote_not_one_exact_source_unit:"+case_id)
        records.append(dict(
            finding_id=case_id,source_file_id=fid,source_sha256=doc["sha256"],
            ocr_page=page,source_unit_id=blocks[0]["id"],
            source_unit_sha256=unit_sha(blocks[0]),exact_ocr_phrase=phrase,
            proposed_scientific_correction=correction,
        ))
    return records

def seed(conn):
    records=build(conn)
    conn.execute(DDL)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO qa_science_substantive_firstpass_findings("
            "finding_id,source_file_id,source_sha256,ocr_page,source_unit_id,"
            "source_unit_sha256,exact_ocr_phrase,proposed_scientific_correction)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            [tuple(r.values()) for r in records],
        )
    stored=conn.execute(
        "SELECT finding_id,source_file_id,source_sha256,ocr_page,source_unit_id,"
        "source_unit_sha256,exact_ocr_phrase,proposed_scientific_correction,"
        "independent_science_review_pending,formally_approved "
        "FROM qa_science_substantive_firstpass_findings ORDER BY finding_id"
    ).fetchall()
    attrs=tuple(records[0])
    if len(stored)!=10 or any(
        tuple(row[k] for k in attrs)!=tuple(record[k] for k in attrs)
        or row["independent_science_review_pending"] is not True
        or row["formally_approved"] is not False
        for row,record in zip(stored,records)
    ):
        raise ValueError("science_erratum_firstpass_sql_readback_mismatch")
    return {
        "substantive_science_corrections_source_attested":10,
        "distinct_original_science_units":len({r["source_unit_id"] for r in stored}),
        "scientific_double_approvals":0,
        "published_questions":0,
        "model_calls":0,
        "scope":"ten_source_locked_assistant_firstpass_only"
    }
