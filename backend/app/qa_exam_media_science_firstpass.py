"""31 original DOCX media: inspected scientific first-pass and honest block reasons.
NO independent approval, original exam official answer, OCR figure certification,
external sources, billable model calls or published questions.
Full original-media sha-256 visual audit resides in the local handoff artifact;
SQL itself can re-check only the exact pinned DOCX SHA, not media bytes (not
stored in the source_documents row).
"""
from collections import Counter
from psycopg.types.json import Jsonb
from app.source_policy import COLLECTION,pinned_source_bytes

EXAMS={("cell" if x["key"]=="cell_biology_exams" else "bio"):x["file_id"]
       for x in COLLECTION["files"] if x["kind"]=="exam_transcription"}
CASES=[["cell","image1.jpeg",2014,"blocked","蔗糖转化酶囊泡处理前后变化","不能仅凭形态确认胞器和转运路径"],["cell","image2.emf",2011,"blocked","Apaf-1/Caspase-9/Cyto c简化线性图","不可把盒子排列当凋亡体结构"],["cell","image3.png",2011,"blocked","Apaf-1与Caspase-9敲除趾间表型不同","不能唯一指定Caspase-9之外的旁路分子"],["cell","image4.emf",2010,"blocked","PDGF、DNA标记峰与细胞增殖时序","不能无完整标记方案推定G2或S精确时长"],["bio","image1.png",2019,"firstpass_supported_with_limits","运动乳酸浓度先升后降","手绘时轴不能作为高精度绝对数字"],["bio","image2.png",2018,"firstpass_supported_with_limits","AMP-PNP的βγ桥连改变","区分γ磷酸转移和α磷酸核酸链延伸"],["bio","image3.png",2015,"firstpass_supported_with_limits","DNA-RNA杂交突出环","突出环对应未与RNA杂交的DNA片段但序列长度未知"],["bio","image4.png",2015,"firstpass_supported_with_limits","胸腺嘧啶甲基及缺少糖2′羟基","支持DNA判断但微小化学键仍需原图复核"],["bio","image5.png",2015,"firstpass_supported_with_limits","反应物1产物3过渡峰4","ΔG=+2正向活化3逆向1;实际速率不可凭ΔG决定"],["bio","image6.jpeg",2015,"firstpass_supported_with_limits","基因E条带1强2次之3/4很弱、GAPDH稳定","无灰度定量不能唯一指定四道精确倍数"],["bio","image7.jpeg",2014,"firstpass_supported_with_limits","β-D-半乳糖六元糖环示例","乳糖需保留β(1→4)键;单糖图不是乳糖答案"],["bio","image8.jpeg",2014,"firstpass_supported_with_limits","ATP核糖2′/3′羟基与α磷酸","入链只有α磷酸;3′羟基影响延伸"],["bio","image9.png",2014,"blocked","Y基因启动子外显子内含子终止区","无完整突变与阅读框不能推测蛋白序列"],["bio","image10.jpeg",2014,"firstpass_supported_with_limits","外显子100+50+25及polyA200","成熟RNA长度375nt条件成立;原题bp单位不规范"],["bio","image11.jpeg",2014,"firstpass_supported_with_limits","Hb Kempsey相对Hb A氧曲线左移","不能猜出水印遮挡的两端精确释放百分比"],["bio","image12.jpeg",2014,"blocked","含COOH、NH2、O/N杂环天然产物","未提供pKa无法断言pH6唯一净电荷"],["bio","image13.png",2014,"blocked","基因X突变序列含promoter及位点编号","低清图不可安全逐碱基转录及翻译"],["bio","image14.png",2014,"reference_only","按照U/C/A/G三联体组织的遗传密码查表图","密码表本身没有给出突变后的序列或读框，不得作为另一道独立题"],["bio","image15.jpeg",2010,"firstpass_supported_with_limits","A/C/G/T泳道底向上CCATCGTTG","报告为合成链5′→3′非DNA模板"],["bio","image16.jpeg",2010,"blocked","内含子A-F突变局部序列","题干说明非完整内含子;需基序定位复核"],["bio","image17.jpeg",2010,"blocked","野生型与Mut A/B RNase切割多泳道","不能从缩略图唯一反演完整RNA二级结构"],["bio","image18.jpeg",2009,"firstpass_supported_with_limits","胆固醇低温增流动高温减流动","相变缓冲作用不能写单向提高"],["bio","image19.jpeg",2009,"firstpass_supported_with_limits","PLP存在时酶活保留较高","限体外稳定性证据不排除催化辅因子作用"],["bio","image20.jpeg",2006,"blocked","D甘油醛Fischer与Newman投影","未核清Newman视线不能唯一填补取代基位置"],["bio","image21.jpeg",2006,"firstpass_supported_with_limits","D葡萄糖Fischer右左右右","α-D环型C1-OH朝下;椅式需特定构型"],["bio","image22.png",2006,"firstpass_supported_with_limits","V-E曲线1、2过原点及3横轴右移","仅理想模型解释无抑制/可逆/不可逆"],["bio","image23.jpeg",2004,"firstpass_supported_with_limits","A减斜率B右移截距","理想模型A可逆B不可逆;不等于真实化学证明"],["bio","image24.jpeg",2004,"blocked","质粒酶切图多泳道和分子量阶梯","未酶切质粒拓扑异构体不允许猜泳道标准答案"],["bio","hdphoto1.wdp",2015,"reference_only","DNA-RNA杂交环的嵌入副本","与image3.png重复候选不得重复计算"],["bio","hdphoto2.wdp",2014,"reference_only","Y基因区段的嵌入副本","与image9.png重复候选不得重复计算"],["bio","hdphoto3.wdp",2014,"reference_only","遗传密码表嵌入副本","与image14.png重复候选不得重复计算"]]
EXPECTED={"firstpass_supported_with_limits":16,"blocked":11,"reference_only":4}
DDL="""
CREATE TABLE IF NOT EXISTS qa_exam_media_science_firstpass (
  source_file_id text NOT NULL,
  source_sha256 text NOT NULL,
  media_filename text NOT NULL,
  exam_year integer NOT NULL,
  firstpass_status text NOT NULL CHECK(firstpass_status IN
    ('firstpass_supported_with_limits','blocked','reference_only')),
  visual_observation text NOT NULL,
  answer_limitation text NOT NULL,
  independent_review_pending boolean NOT NULL DEFAULT true CHECK(independent_review_pending),
  science_approved boolean NOT NULL DEFAULT false CHECK(NOT science_approved),
  official_answer_approved boolean NOT NULL DEFAULT false CHECK(NOT official_answer_approved),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(source_file_id,source_sha256,media_filename)
)"""

def seed(conn):
    if set(EXAMS)!={"cell","bio"} or len(CASES)!=31:
        raise ValueError("31_media_list_incomplete")
    keys={(x[0],x[1]) for x in CASES}
    if len(keys)!=31 or dict(Counter(x[3] for x in CASES))!=EXPECTED:
        raise ValueError("31_media_duplicate_or_status_drift")
    checked={}
    for k,fid in EXAMS.items():
        row=conn.execute(
            "SELECT sha256 FROM source_documents WHERE file_id=%s ORDER BY id DESC LIMIT 1",
            (fid,),
        ).fetchone()
        if not row or not pinned_source_bytes(fid,row["sha256"]):
            raise PermissionError("31_media_owner_docx_lock_required")
        checked[k]=row["sha256"]
    values=[(EXAMS[k],checked[k],name,year,status,observed,limit)
            for k,name,year,status,observed,limit in CASES]
    conn.execute(DDL)
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO qa_exam_media_science_firstpass("
            "source_file_id,source_sha256,media_filename,exam_year,"
            "firstpass_status,visual_observation,answer_limitation)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            values)
    stored=conn.execute(
        "SELECT source_file_id,source_sha256,media_filename,exam_year,"
        "firstpass_status,visual_observation,answer_limitation,"
        "independent_review_pending,science_approved,official_answer_approved "
        "FROM qa_exam_media_science_firstpass ORDER BY source_file_id,media_filename"
    ).fetchall()
    columns=("source_file_id","source_sha256","media_filename","exam_year",
             "firstpass_status","visual_observation","answer_limitation")
    if len(stored)!=31 or {tuple(row[c] for c in columns) for row in stored}!=set(values):
        raise ValueError("31_media_sql_source_locked_readback_failed")
    if any(not r["independent_review_pending"] or r["science_approved"]
           or r["official_answer_approved"] for r in stored):
        raise ValueError("31_media_unapproved_findings_misrepresented")
    return dict(original_docx_files=2,media_source_records=len(stored),
                content_firstpass_status=dict(Counter(x["firstpass_status"] for x in stored)),
                science_approved=0,official_answers_approved=0,
                model_calls=0,unreviewed_remaining=0,
                caveat="Media scientific first-pass only. Not human dual review; not textbook OCR image repair.")
