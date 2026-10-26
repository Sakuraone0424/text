# 私有审核成果的保存、恢复与接续

2026-10-26。环境服务当前connected/running、资料和原数据库仍在；运行状态不构成长期持久化承诺。不能保证环境回收后本地磁盘仍在。本轮仅在现有本机授权范围备份，未上传第三方或GitHub。

## 已执行保护

- 原数据库ustc_ocr_staging_20261026及source_staging_real_20261026保持原位，没有重建或重新导入六文件。
- 六文件实际字节/SHA、12396单元、1915页标、17128原建议及001—012校验和重新核对，均一致。013只新增独立pending工作表；原始数据、历史试卷/答卷、source_review_events和正式知识链接未改写。
- 私有根目录 `/workspace/private-intake/review-20261026` 保存before-review.dump及current-final.dump（custom格式）、完整性报告、备份哈希/表内容摘要及全部逐章/逐年报告。文件0600、目录0700。pg_restore向/dev/null解码整个备份成功；**没有在第二个数据库实际恢复**，不得将归档解码称为完整恢复演练。
- 当前有效分析为evidence-work/1.6、transcript-review/1.4，见final-v1.6。早期分析版本留存为不可变历史，当前统计只使用有效版本，不能把多次分析快照累计成知识点数。6份analyst-dossier/1.0为辅助分析，不是人工审核。

## 重新连接

工作目录必须先定位仓库，确认分支feat/reviewed-dual-subject-28和干净状态。若原PostgreSQL仍在，只连接原库，不能再次init/import或自动CREATE DATABASE。

```sh
cd /实际仓库/backend
export SOURCE_STAGING_DATABASE_URL='明确授权的非生产原库连接串'
python -m app.source_intake audit --target development \
  --schema source_staging_real_20261026 --document-id 1 \
  --report /私有新目录/source-1-readback.json
```

对六个现有document_id逐一核对，使用原私有baseline-integrity.json和backup-manifest-current.json检查文件SHA、迁移SHA与表摘要。不要把凭据写入报告或shell历史。原库恢复可读后，先读取final-v1.6/summary.json、current-progress.json及analyst-dossiers.jsonl，再继续审核，不能把旧的缺文件报告当作当前状态。

## 灾难恢复方案（未执行）

只有原库确实不可用，且用户明确选择了**空白、独立、非生产恢复目标**后，才能执行恢复。禁止将备份覆盖现有库，禁止生产目标，禁止使用--clean/--create去删除或重建原数据库。校验dump的实际SHA与backup-manifest一致，用PostgreSQL17兼容pg_restore先--list及向/dev/null完整解码，再以--single-transaction --exit-on-error --no-owner --no-acl恢复到获授权空白目标。

随后比较所有表的行数和规范化内容摘要、001—013迁移SHA、六文件原字节/SHA、全部页标、17128原建议、当前版本工作项及source_review_events。六文件可从source_versions.source_content私有导出并再次比对SHA；不得从旧OCR或其他资料补齐。全部核对成功才能切换显式SOURCE_STAGING_DATABASE_URL；不自动启动答题服务，不部署。

本机备份与数据库可能一起随环境回收消失。若需跨环境长期保留，应由用户指定私有交接/存储方式并授权后另行传输；本轮没有执行外传。

## 审核接续边界

每个工作项绑定算法版本、原始单位ID、SHA/页码/摘录及确定性work_key，重复运行不会重复创建同一工作项。source_review_events仍是逐单元签署的文本/科学/风险依据；自动程序只写pending表，不调用approve或发布API。队列中的分析版本不能转化为人工审核记录。

完整关系级确认记录仍需明确审核员决定：现有knowledge_links是注释而非独立签署，不能直接称为可信知识点。统计在无链接时确认数为0；已有注释却无关系签署时返回未知，避免伪造完成数。正式命题前必须补足实际审核、可信映射、答案与评分标准，动态引擎不会消费pending工作项。

在原库确认完整且需要重建分析输出文件时，可运行（新目录）：

```sh
cd /实际仓库/backend
python -m app.evidence_work --schema source_staging_real_20261026 \
  --target development --destination /私有新目录/review-analysis
```

这只读取已导入正文、保存pending工作项和私有报告；不会重导入六文件、批准来源或发布题目。同版本同内容的work_key保持幂等。补充映射版本refined-exam-mapping/1.0与辅助分析analyst-dossier/1.0分开计数。
