# 当前六文件OCR-only接入与核验

2026-10-26（Asia/Shanghai）。本说明替代此前旧OCR、PDF分卷和192＋193重置交接要求。只使用用户指定OCR文件夹下六份校订资料，不下载或请求其他教材、真题或PDF。旧PDF工具仅作独立兼容工具，见 [optional-pdf-intake.md](optional-pdf-intake.md)。

## 唯一资料清单

文件夹ID：`1RDVTRbLGnfc6GwPzGCCjGADf1ocLFMt6`。代码唯一清单为 [ocr_collection.json](../backend/app/ocr_collection.json)，版本 `ocr-corrected-2026-10-26-v1`。各文件ID、路径、科目、格式及声明范围均集中在该文件；这些配置不是Drive访问凭据。

| 目录 | 文件 | 预期OCR页标/声明范围 |
|---|---|---|
| 生物化学/书本 | 生物化学_上册_校订版.txt | 1—654，`===== 上册 page-001 =====` |
| 生物化学/书本 | 生物化学_下册_校订版.txt | 1—587，`===== 下册 page-001 =====` |
| 生物化学/书本 | 生化高分笔记_校订版.txt | 1—289，`===== page-001 =====` |
| 细胞生物学/书本 | 细胞生物学_第五版_校订版.txt | 连续1—385，`===== page-001 =====` |
| 生物化学/真题 | 2004~2026年中国科学技术大学生物化学与分子生物学真题.docx | 声明2004—2026，逐年待核 |
| 细胞生物学/真题 | 1998-2026年中国科学技术大学细胞生物学真题.docx | 声明1998—2026，逐年待核 |

四本合计预期1915个页标。该数不表示1915页科学校对完成，不是PDF物理页数，也不是书本印刷页核对结果。细胞文件不再使用192＋193 OCR重置映射；当前流程拒绝这种旧配置。支持可选行首反斜杠及两位/三位零填充；拒绝缺号、重复、倒退、非法册名/页标、未定位前置正文及声明范围不符。

## 当前真实接入状态

用户已在其ChatGPT连接中定位并抽查六份文件；Codex独立检查没有可用Drive读取工具、凭据或身份，不能继承另一个执行环境的权限，也未尝试匿名下载。当前Codex真实文件读取0、预检通过0、入库0、正文人工审核0、题目发布0。

私有交接清单 `/workspace/private-intake/handoff.json` 已更新为当前六文件，旧清单已归档为失效历史。真实缺文件报告在 `/workspace/private-intake/corrected-six-preflight-blocked.json`。它们在checkout之外、0600权限，不提交Git。旧合成PDF或旧OCR不会被搜索为替代来源。

若当前Codex不能使用既有私有连接，只需用户通过已有合法方式提供**上述六个文件**，可在checkout之外按目录树交接。无需PDF、分卷、额外OCR或其他材料，也不要把Drive文件改为公开。文件字节取得后才能计算真实SHA和判断实际内容；不能先给文件猜哈希。

## 状态必须分开

| 状态 | 证据/操作 | 不代表 |
|---|---|---|
| 用户报告已定位 | 用户提供文件ID/路径 | Codex已连接Drive或已读文件 |
| 本地文件已定位 | 在交接目录找到指定路径 | 来源身份已经远端核实 |
| 文件已读取 | 有界读取TXT/DOCX实际字节、保存SHA | 页标完整或知识正确 |
| 预检通过 | 全部声明页标/格式/索引检查通过 | 数据库已提交或人工审核 |
| 正文已入库 | 隔离事务提交且独立读回 | 内容已人工核验 |
| 文本人工复核 | 逐单元text审核，绑定单位摘要 | 科学内容或答案已审核 |
| 科学审核 | science审核、必要的uncertainty处置 | 题目已发布或考试原卷已核验 |
| 题目已审核发布 | 现有五维审核和分值/证据/风格门禁 | 所有教材/所有年度均已核验 |

检索/旧接口中的source_units.verified在OCR模式仅承接text审核；新接口返回独立review_kind，不能用一个verified字段覆盖全部状态。报告按章列出索引单元、OCR页、文本与科学审核比例、风险待处理及知识点链接；未知章节明确unclassified，不能当作已完成章节校对。

## OCR-only存储与证据

TXT是教材原始交接文件，DOCX是真题原始交接文件；无需附加原PDF。保存其完整实际字节、SHA-256、格式、清单身份、上个版本和索引器版本。source_pages完整保存每个页标原文及册名；source_units保存章节、小节、正文、全书OCR页和段落ID。DOCX保留段落/表格路径，页数未知，按段落定位。

OCR-only的pdf_page、printed_page、original_part均为null，不允许元数据注入“已核对物理/印刷页”。引用示例：

```json
{
  "source_unit_id": 123,
  "file_id": "清单中的文件ID",
  "source_sha256": "实际文件的64位SHA-256",
  "ocr_page": 42,
  "paragraph_id": 123,
  "locator": "OCR:42:UNIT:123",
  "excerpt": "该段落中可逐字核对的短摘录"
}
```

这是Evidence中的来源字段示例；还须按API填写书目/章节等现有字段。DOCX引用使用 `DOCX:UNIT:123`，ocr_page为null。文件ID、实际SHA、OCR页、段落ID、定位器和原文短引文必须全部匹配，不能拿PDF页码替代OCR页。正式题目必须包含当前四本教材之一的证据，DOCX转录不能单独替代教材科学依据。

原始字节/版本、正文和坐标、审核事件不可静默覆盖；文件SHA变化创建新版本和新单元、父版本可追踪，审核与知识链接均不复制。即使页码或文本相同也不自动继承复核。旧版本仍保留用于历史试卷；新组卷只允许当前六文件的当前版本。旧哈希不能通过重新导入悄悄恢复旧审核。详见 [data-versions.md](data-versions.md)。

## 私有预检、隔离导入与读回

文件、元数据和报告都放在checkout之外的私有目录，不把私有正文上传GitHub。报告不含教材正文，但有标识、SHA及审计信息，仍按私有资料处理，独占创建为0600，不能覆盖已有报告。

```sh
cd backend
python -m app.source_intake collection-manifest \
  --directory /private/OCR --report /private/current-handoff-001.json
python -m app.source_intake collection-check \
  --directory /private/OCR --report /private/six-preflight-001.json
```

批量check只尝试清单中的六个路径，检查符号链接不能跳出交接根目录；不联网、不OCR、不用旧文件兜底。报告分别累计读取、预检、入库及审核状态。年份文本只作候选，不是考频样本。

manifest中的metadata可保存为每文件的私有JSON供单文件命令使用。教材采用清单的654/587/289/385、volume_offsets:[0]；DOCX无需expected_pages。source_mode是ocr_only，无需original、original-parts或physical_mapping。书目未知时保留待核查/未载明，不发明作者、版本或ISBN；从这六文件自身核对后可在导入前填写，或用下述追加书目复核，不改写原始身份。

```sh
python -m app.source_intake check /private/OCR/生物化学/书本/生物化学_上册_校订版.txt \
  --metadata /private/config/biochemistry_upper.json \
  --report /private/reports/upper-check-001.json
# SOURCE_STAGING_DATABASE_URL经安全渠道注入，明确选择开发或staging，不使用生产。
python -m app.source_intake init --target staging --schema source_staging_corrected_001
python -m app.source_intake import /private/OCR/生物化学/书本/生物化学_上册_校订版.txt \
  --metadata /private/config/biochemistry_upper.json \
  --target staging --schema source_staging_corrected_001 \
  --report /private/reports/upper-import-001.json
python -m app.source_intake audit --document-id 1 \
  --target staging --schema source_staging_corrected_001 \
  --report /private/reports/upper-readback-001.json
python -m app.source_intake collection-status \
  --target staging --schema source_staging_corrected_001 \
  --report /private/reports/collection-status-001.json
python -m app.source_intake exam-coverage \
  --target staging --schema source_staging_corrected_001 \
  --report /private/reports/years-001.json
```

不提供数据库连接时可以完成离线预检。写库只接受SOURCE_STAGING_DATABASE_URL及显式development/staging、已初始化source_staging_* schema，不使用DATABASE_URL，不允许public或production目标。target文字本身不能证明DSN非生产，运行者必须核对授权目标。init只创建新schema，不重建已有schema；不提供删除历史数据命令。报告失败、输入文件在预检与导入间变化或读回不一致会回滚。命令退出0后仍须独立audit，不把准备报告当成数据库提交证明。

OCR文件最多50MiB；DOCX展开总量100MiB、表格嵌套上限8仍保留。乱码、图片依赖、结构式、表格、科学符号、不确定OCR和已报告缺题均单独标记；DOCX嵌图无转录的单元不能被猜字核验。库里material_status:synthetic_test资料不能被批准成真实教材。

## 人工任务与发布门禁

审核员使用既有独立x-review-api-key，不调用收费AI：

- `GET /v2/sources/search?q=...`：仅检索当前六文件当前版本，返回OCR身份及独立审核状态。
- `GET /v2/sources/collection-status`、`GET /v2/sources/{document_id}/coverage`：六文件阶段计数及各章节索引/审核任务。
- `POST /v2/sources/{unit_id}/reviews`：review_kind为text/science/uncertainty/exam_style，status为approved/rejected，带reviewer及实质性note。追加记录，不覆盖旧结论。
- text先核对OCR原文；science先有text；存在风险标记时，先做uncertainty处置。须逐项列resolved_flags、resolution:text_sufficient及充分explanation；内容无法从文字确证时应拒绝，不能猜图或公式。乱码/无转录图须校订原文件产生新版本。
- `POST /v2/sources/{document_id}/bibliography`：追加带版本SHA的书目复核（title/authors/edition/isbn/reviewer/note），原始文档元数据不改写。无ISBN可为空；未载明信息不得伪装成已识别。
- `POST /v2/sources/{unit_id}/knowledge`：为当前版本人工建立知识点关系；没有新版本链接时正式题目不能拿旧OCR链接兜底。
- 正式题目还须参考答案、评分细则、固定分值、五维专家审核、唯一性/重复检测和经过人工核对的风格参考。未知章节、未审科学内容、身份不符或未解决风险均拒绝。审核通过不表示自动AI证明了科学真理。

## 2026版真题汇编

仅依据这两份DOCX的实际正文建立候选。段落/表格逐项索引，显式年度标题和题号用于生成待审候选，题型、年份缺失、边界不明、表格/图像及转录缺口单列。文件名范围、年份提及、题号序列均不自动证明完整年度。对未能可靠分段的内容保留原段落任务，不猜补题干或答案；真实格式尚未读取，后续可能需按实际排版完善规则。

年度报告覆盖生化2004—2026、细胞1998—2026，特别标记2023—2026。分别列候选题数/类型、未知年份、边界、明确缺口、参考答案标记、官方答案证据、教材映射及可信考频资格。OCR文件获取与原卷获取分开报告；当前DOCX转录不能被设置为原卷已核验、完整原卷或官方答案已证明，classification必须保留transcription。

经人工复核的转录可提供命题风格参考，但不能冒充已核实原卷的考频样本。动态引擎保留已有真题、薄弱点、FSRS、置信度、章节软约束、去重及seed审计；可信真题资料不足时如实降级。候选统计不替代其可信考频输入，旧版本映射/考频也不自动沿用。

## 验收边界与交接后顺序

代码和测试可在无真实文件时完成；真实资料处理必须等合法连接或六文件交接。获得文件后依次做SHA身份、全文页标、章节索引、知识点关系、乱码/符号任务、正文和科学审核；再核对逐年真题及题目审核。没有真实题库就保持缺题报错，不用模拟资料生成正式训练。

独立合成六文件CLI验收解析1915个页标、1947单元、32条真题候选，隔离入库后审核0、试卷0、答卷0；合成资料核验尝试被拒绝，随后仅清理该次合成schema。它不改变真实六文件的0读取/0导入状态。最终测试及CI见 [acceptance.md](acceptance.md) 和PR #1。

未合并main、未部署production、未修改生产Postgres、未删除历史成绩或公开任何真实教材/真题。
