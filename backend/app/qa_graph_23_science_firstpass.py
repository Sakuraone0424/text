"""Twenty-three substantive mechanism/contrast first-pass findings, six-owner-source-only.
Original OCR bytes are immutable. These are real scientific review notes, NOT
independently signed science decisions, KG approval, or textbook figure corrections.
"""
from collections import Counter
from app.source_policy import COLLECTION,pinned_source_bytes
from app.ocr_sources import unit_sha

BOOKS={x["key"]:x["file_id"] for x in COLLECTION["files"] if x["kind"]=="textbook"}
CASES=[["MEC-001","cell_biology",75,"简单扩散和协助扩散","区分不需膜蛋白的简单扩散与需膜蛋白的协助扩散；均顺电化学梯度且无需细胞直接供能"],["MEC-002","cell_biology",75,"主动运输需要细胞提供能量","主动运输需能逆电化学梯度；可直接ATP供能也可由其他离子梯度间接供能"],["MEC-003","cell_biology",73,"Na+-K+ 泵","钠钾泵ATP水解并输出钠、输入钾；引文不足以支持每轮精确化学计量"],["MEC-004","cell_biology",93,"泛素化和蛋白酶体","泛素化蛋白酶体处理不稳定/错误折叠蛋白，不等于溶酶体机制"],["MEC-005","cell_biology",94,"泛素活化酶（E1）","E1负责ATP依赖泛素激活，勿把E3的底物特异性归于E1"],["MEC-006","cell_biology",94,"泛素结合酶（E2","E2是转移泛素的结合酶/载体，不等于执行最终底物选择的E3"],["MEC-007","cell_biology",94,"泛素连接酶（E3）","E3参与底物识别及泛素连接；E1、E2、E3各具不同功能"],["MEC-008","cell_biology",116,"信号识别颗粒（signal recognition particle, SRP）","SRP结合信号肽引导共翻译内质网定位，不等同于NLS核输入机制"],["MEC-009","cell_biology",134,"当融合与分裂的比值大致处于平衡状态时","线粒体形态、数量和体积平衡为教材直接事实，运输或遗传信息共享仍具有假说限定"],["MEC-010","cell_biology",162,"Arp2/3 复合物的参与","Arp2/3促进微丝成核，不能混淆微管γ-TuRC成核"],["MEC-011","cell_biology",164,"Arp2/3 复合物由 Arp2","Arp2/3与formin共同参与肌动蛋白组装但机制不同，不宜互换"],["MEC-012","cell_biology",174,"将所结合的 GTP 水解为 GDP","组装后β微管蛋白的GTP可水解；不能把α微管蛋白GTP作同样断言"],["MEC-013","cell_biology",199,"核定位信号（nuclear localization signal, NLS）","NLS介导核输入，不同于通常经切除的经典分泌信号肽；NES介导输出"],["MEC-014","cell_biology",356,"紧密连接的大脑毛细血管内皮细胞","血脑屏障由紧密连接的脑微血管内皮参与形成，不能说只含紧密连接"],["MEC-015","biochemistry_upper",145,"酶通过降低活化能","酶降低活化自由能，不改变总体反应ΔG及平衡常数"],["MEC-016","biochemistry_upper",163,"竞争性抑制剂（I）通过与底物","竞争性抑制形成EI，Km/Vmax判读应有明确动力学模型与条件"],["MEC-017","biochemistry_upper",164,"非竞争性抑制剂与酶结合","非竞争性抑制可涉及ESI，纯非竞争与混合型不能误合并Km规律"],["MEC-018","biochemistry_upper",290,"限制性内切酶","限制性内切酶识别特异DNA序列并切割；切口与黏端类型需原图支持"],["MEC-019","biochemistry_lower",166,"主要产生戊糖磷酸","戊糖磷酸氧化段直接形成Ru5P和NADPH，后续异构成R5P"],["MEC-020","biochemistry_lower",182,"三羧酸循环","TCA与柠檬酸循环同义，不能误称每一中间物都含三个羧基"],["MEC-021","biochemistry_lower",197,"琥珀酸依次在琥珀酸脱氢酶","琥珀酸经延胡索酸与苹果酸到草酰乙酸，包括脱氢和水合步骤"],["MEC-022","biochemistry_lower",326,"肉碱转运系统","长链脂酰基借肉碱穿过线粒体内膜，跨膜形式为脂酰肉碱而非脂酰CoA"],["MEC-023","biochemistry_lower",327,"形成的长链脂酰肉碱通过","CPT-I形成脂酰肉碱，经转位体跨内膜，再在基质侧形成脂酰CoA"]]
DDL="""
CREATE TABLE IF NOT EXISTS qa_graph_23_science_firstpass (
 finding_id text PRIMARY KEY,
 source_file_id text NOT NULL,
 source_sha256 text NOT NULL,
 source_unit_id bigint NOT NULL REFERENCES source_units(id),
 source_unit_sha256 text NOT NULL,
 ocr_page integer NOT NULL CHECK(ocr_page>0),
 exact_ocr_anchor text NOT NULL,
 source_anchor_match_count integer NOT NULL CHECK(source_anchor_match_count>0),
 scientific_content_firstpass text NOT NULL,
 independent_science_review_pending boolean NOT NULL DEFAULT true
   CHECK(independent_science_review_pending),
 science_approved boolean NOT NULL DEFAULT false CHECK(NOT science_approved),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
)"""

def evidence(conn):
    if len(CASES)!=23 or len({x[0] for x in CASES})!=23:
        raise ValueError("mechanism_case_count_or_id_drift")
    records=[]
    for ident,key,page,anchor,review in CASES:
        if key not in BOOKS or len(anchor)<5 or len(review)<20:
            raise ValueError("mechanism_invalid_scientific_note")
        fid=BOOKS[key]
        doc=conn.execute(
            "SELECT id,sha256 FROM source_documents WHERE file_id=%s ORDER BY id DESC LIMIT 1",
            (fid,)).fetchone()
        if not doc or not pinned_source_bytes(fid,doc["sha256"]):
            raise PermissionError("mechanism_not_from_exact_locked_textbook")
        found=conn.execute(
            "SELECT * FROM source_units WHERE document_id=%s "
            "AND ocr_global_page=%s AND strpos(text,%s)>0 ORDER BY ordinal,id",
            (doc["id"],page,anchor)).fetchall()
        if not found:
            raise ValueError("mechanism_exact_quote_not_found:"+ident)
        # All matches are within the same exact owner OCR page.
        # Preserve multiplicity rather than pretending an ambiguous short
        # matching phrase is a unique proposition.
        first=found[0]
        records.append((ident,fid,doc["sha256"],first["id"],
                        unit_sha(first),page,anchor,len(found),review))
    return records

def seed(conn):
    records=evidence(conn)
    conn.execute(DDL)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO qa_graph_23_science_firstpass("
            "finding_id,source_file_id,source_sha256,source_unit_id,"
            "source_unit_sha256,ocr_page,exact_ocr_anchor,"
            "source_anchor_match_count,scientific_content_firstpass)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            records)
    columns=("finding_id","source_file_id","source_sha256","source_unit_id",
             "source_unit_sha256","ocr_page","exact_ocr_anchor",
             "source_anchor_match_count","scientific_content_firstpass")
    stored=conn.execute(
        "SELECT "+",".join(columns)+",independent_science_review_pending,"
        "science_approved FROM qa_graph_23_science_firstpass ORDER BY finding_id"
    ).fetchall()
    if len(stored)!=23 or {tuple(row[k] for k in columns) for row in stored}!=set(records):
        raise ValueError("mechanism_content_sql_readback_failed")
    if any(r["science_approved"] or not r["independent_science_review_pending"]
           for r in stored):
        raise ValueError("mechanism_unapproved_science_falsely_signed")
    return {
        "mechanism_content_firstpass_source_verified":len(stored),
        "source_anchored_review_notes":len(stored),
        "source_anchor_ambiguity_units":sum(r["source_anchor_match_count"]>1 for r in stored),
        "formal_science_approvals":0,
        "published_questions":0,
        "model_calls":0
    }
