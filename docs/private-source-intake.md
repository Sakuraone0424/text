# 私有教材与原卷接入：准备、授权和隔离验收

日期：2026-10-25（Asia/Shanghai）。延续PR #1，不改变已验收的动态命题算法。此文及仓库不含教材正文、原PDF、凭据或真实资料核验结论。

## 当前状态与授权交接

四本教材OCR与两份真题汇编仅有用户提供的Drive定位ID；ID不是读取授权。本轮没有Drive连接器/运行身份，没有访问或公开分享文件。真实材料导入数为0，不能据模拟试验宣称教材已核验或正式题库已具备。

六份定位信息已放入checkout之外、0600权限的私有交接清单 `/workspace/private-intake/handoff.json`，不提交Git。该清单不是可直接导入的元数据：原件分卷、作者/版本、实际页数和授权范围仍待填写。

实际接入需要：

1. 用户通过受支持的私有连接或受控本地文件交接，明确授权读取这六份指定OCR，以及对应原始PDF的**全部分卷**；不需开放整个Drive，更不应改为公开链接。若没有连接器，可由用户将文件下载到checkout之外的私有目录后交接。现有OCR直接使用，不重新OCR。
2. 每份OCR提供本地路径、源文件ID和独立确认的页数；每份PDF分卷提供本地路径、分卷文件ID、顺序、实际页数、全书物理页偏移，建议同时提供SHA-256用于交接比对。当前尚无真实分卷PDF的ID/清单。
3. 对照实际封面/版权页核对书名、作者、版次、ISBN（无则留空），记录OCR局部分卷、全书OCR页、全书物理PDF页和印刷页的对应关系。不能由文件名推定版本或作者；不能假定PDF分卷恰为OCR的192/193页。
4. 明确授权的development或staging数据库，使用专用最小权限账号和安全注入的 `SOURCE_STAGING_DATABASE_URL`，不使用生产连接。数据库应为独立实例；schema隔离提供第二层保护，但字符串 `--target staging` 无法证明某个DSN确实不是生产，交接人必须核对实际目标。
5. 指定可签署教材/原卷、科学符号、知识点及真题范围的审核人。数据导入、完整性检查、内容核验、题目审核发布是独立步骤；本工具不自动核验或发布。

## 分页与证据坐标

支持独立分隔行 `===== page-001 =====`、`===== page-01 =====`，也支持用户文本中可能出现的行首反斜杠形式 `\===== page-001 =====`、大小写PAGE和旧 `[PAGE 1]` 格式。不是段落内出现这些字符就划页；疑似但不合法的分隔行拒绝。正文出现在首个页标之前、重复页、非法重置、声明范围缺页、越界和多OCR页映射同物理页均拒绝，不把整书悄悄合为无页码正文。

细胞第五版的**OCR**配置为：

```json
{
  "file_id": "交接的OCR源ID",
  "title": "以版权页核查为准",
  "authors": "以版权页核查为准",
  "edition": "以版权页核查为准",
  "subject": "cell_biology",
  "expected_pages": 385,
  "volume_offsets": [0, 192],
  "volume_pages": [192, 193],
  "physical_mapping": "identity_confirmed",
  "printed_pages": {},
  "material_status": "private_pending"
}
```

`identity_confirmed`只能在实际确认全书OCR第N页对应全书物理PDF第N页之后填写，不是默认事实；未声明对应策略的私有预检拒绝。若不相同，设置 `physical_mapping:"explicit"`，并用 `pdf_pages` 对全部全书OCR页显式映射。例如2页OCR对应PDF第2、3页：`{"1":2,"2":3}`。不能把页数相同当成正文对应证明。

坐标含义：

| 字段 | 含义 |
|---|---|
| volume、ocr_page | OCR分卷与该卷局部页；第二卷001仍为局部1 |
| ocr_global_page | OCR全书序号；第二卷001为193 |
| pdf_page | 按原PDF分卷顺序合成的全书物理页，用于 `PDF:N` 证据定位 |
| original_part、original_page | 原PDF分卷编号和该PDF内实际页，不受OCR重置规则影响 |
| printed_page | 人工提供的书本印刷页标签，可为罗马数字；缺失为null，不自动猜测 |

`printed_pages`以**全书物理页**字符串为键。印刷页未知不会伪装为物理页，也不声称已核查；报告列出所有缺口。空白OCR页保留在source_pages覆盖记录中，即使没有正文单元。分卷最后的空页仍须有页标；缺正文/空页须人工确认其确为空页。

## 有界多分卷原件

保留单原件100 MiB限制。分卷模式每卷最多100 MiB、最多16卷、合计最多512 MiB；单PDF1—2000页，全书最多4000页。OCR文件最多50 MiB；DOCX展开大小原有限制继续保留。先检查文件大小并有界读取，再解析；拒绝空文件、非PDF、加密或不可解析PDF、实际页数与交接不符、哈希不符、重复ID、偏移缺口/重叠/乱序、混用单原件与分卷。大卷需在用户控制下重新合理分卷，不能删除限制凑合导入。

分卷清单示例仅演示结构，数值为**合成样例**；真实值须实际核对：

```json
[
  {"path":"part-1.pdf", "file_id":"原件分卷1ID", "page_offset":0, "expected_pages":192},
  {"path":"part-2.pdf", "file_id":"原件分卷2ID", "page_offset":192, "expected_pages":193}
]
```

路径相对清单文件所在目录解析，可另填 `sha256` 与交接哈希比较。偏移从0起连续增加，必须按实际PDF页数计算；OCR分卷与原PDF分卷不要求一一对应。报告列出未被OCR映射的原件页，前言/封面等缺口由审核人解释，不能据此自动称全书正文完整。

010增量迁移新增 `source_original_parts`，逐卷保存二进制、哈希、真实页数、字节数和偏移。文档头的original_sha256在分卷模式是有序规范化清单的SHA-256，**不是不存在的合并PDF哈希**；`original_content`为空。各卷不可覆盖/删除；纠错使用新资料修订。完整清单身份、实际卷哈希、单位的全书/分卷页关系在核验和题目证据审查中检查。真题来源存在性检查同样支持分卷，不会因没有单文件bytea而误判缺原件，也不会只凭正文存在就绕过原件。

## 先预检，再隔离导入与独立读回

所有资料、清单和报告置于私有目录，不能放入checkout或上传GitHub；下例使用 `/private/intake` 占位路径。报告不含正文或PDF字节，但仍有书目信息、文件标识和审计信息，按私有资料处理。报告以0600权限独占创建，拒绝覆盖已有文件/符号链接。每次运行选新文件名。

```sh
cd backend
python -m app.source_intake check /private/intake/cell-ocr.txt \
  --metadata /private/intake/cell.json \
  --original-parts /private/intake/cell-parts.json \
  --report /private/intake/reports/cell-preflight-001.json
```

预检不连接数据库，不做网络请求、不下载Drive、不运行OCR。单原件用互斥的 `--original /private/intake/book.pdf`。成功仍为 `pending_human_review / verified:false`；页标/映射等失败返回2并写私有rejected报告。报告包含OCR哈希、原件身份/各卷哈希和页数、逐页对应、空页、缺失印刷页、未映射原件页、正文字符/段落数、质量标记。年份文本仅叫 `year_mentions_not_verified_exam_coverage`，教材中提到2024不代表有2024真题。

在明确选择的非生产数据库安全注入 `SOURCE_STAGING_DATABASE_URL` 后：

```sh
python -m app.source_intake init --target staging --schema source_staging_intake_001
python -m app.source_intake import /private/intake/cell-ocr.txt \
  --metadata /private/intake/cell.json --original-parts /private/intake/cell-parts.json \
  --target staging --schema source_staging_intake_001 \
  --report /private/intake/reports/cell-import-001.json
python -m app.source_intake audit --document-id 1 \
  --target staging --schema source_staging_intake_001 \
  --report /private/intake/reports/cell-readback-001.json
python -m app.source_intake exam-coverage \
  --target staging --schema source_staging_intake_001 \
  --report /private/intake/reports/exam-years-001.json
```

init只创建新 `source_staging_*` schema并运行版本化迁移，不复用或重建已有schema。import/audit要求schema已存在，禁止public或production目标，不回退使用 `DATABASE_URL`。旧 `python -m app.sources` 可执行名委托到同一严格import入口，也必须提供target/schema/report。

导入在事务内写原件、页面及单元，重复文件/哈希幂等；待审资料补原件保留单元ID。已核验资料不可重索引，已保存分卷不可替换，须新修订。预检后文件变化或数据库读回失败会拒绝并回滚；报告写入失败同样回滚。数据库提交失败时退出非0，不能根据留下的准备报告认定提交成功；**只有命令退出0并通过独立audit才完成持久化验收**。

审计逐卷读回哈希/页数、索引摘要、页面覆盖、单元与页面对应、知识链接及书目缺项。索引摘要涵盖正文/章节/坐标/标记，能发现存储后变更，但不证明OCR语义或科学正确。报告 `integrity_checked_not_content_verified` 不等于教材核验完成。正文搜索和知识标签经审核人确认后补齐；本工具不猜测全部知识标签。

`material_status:synthetic_test`用于模拟导入，核验入口明确拒绝将其变为真实教材；这种数据不得复制到正式题库。普通私有导入同样默认全部单元未核验、题目0发布。迁移不会把原有历史来源的新增坐标补成推测值，也不重写旧答卷或成绩；旧单文件原件仍可读取、校验。旧已核验资料若需补充新索引字段，使用新修订而非覆盖已核验内容。

## 年度真题覆盖验收

两个汇编按用户声明分别覆盖生化2004—2022、细胞1998—2022；必须逐年核实，不将文件名或年份提及当作覆盖。报告仍列生化2004—2026、细胞1998—2026的52个年度槽位；2023—2026材料未知，不默认为已取得。

先对原卷OCR做相同预检/原件映射，再经审核员将原题编号、科目、年份、类型和真实来源单元写入现有 `/v2/exams`，审核后追加scope与classification；完整年度用 `/v2/exams/samples` 核对原件和去重数量。官方答案仍需独立核验来源；个人/资料答案不能冒充官方。导入器不会自动把正则匹配到的年份建立为已核实原题。

覆盖报告区分原件获取、正文记录、来源核验、缺答案/教材证据、完整卷声明及可靠映射数量；考频使用现有已核验范围模型，未知年份不作零。只有真实原件、教材和审核全通过，才进一步审核正式题库；题库不足继续拒绝组卷，不用模拟材料凑数。

## 本轮开发验收与边界

同真实OCR分隔格式的合成385页、192+193页原PDF分卷完成CLI预检、隔离开发schema导入、全文搜索、一个明确模拟的知识链接、独立只读审计与52年覆盖报告。该schema中385个来源单元、385条页映射、2个原件分卷，**核验单元0、试卷0**；模拟资料核验尝试被拒绝。它验证工具链，不是真实教材接入。

自动测试涵盖实际分隔符/重置、空页/缺页、印刷页与物理页区分、前言偏移、格式/加密/大小/总量/页数/哈希异常、分卷证据门禁、事务失败/文件变化回滚、只读年度报告、旧单文件数据迁移兼容、既有训练全流程。最终数量及CI见PR与 [验收报告](acceptance.md)。

没有合并main、生产部署或生产数据库修改。实际教材/真题核验必须待上述最小授权与文件交接后执行；不因本轮完成了可独立开发部分而把外部资料状态改成完成。
