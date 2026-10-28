# 真实本地恢复验收（2026-10-27）

结论：**指定备份在全新独立PG17集群内实际恢复成功，并完成重启后的独立读回**。本轮不是Mock恢复；没有恢复到Railway，也没有修改原生产或原本地资料库。

## 身份、过程和完整性

- 业务代码基线63042da7e31819237971b4289689c949af0d55b7；原Push/PR CI各410项的既有结果保留，不重复全跑。
- 原包63,472,028字节，SHA-256 `4e84fde3fdaaabded29005d9c05eeb4543b714d90673c55b3958dac84cbf081a`；本次重新核对SHA、ZIP CRC、三个成员及内层dump大小/SHA。原件未改。
- 私人备份仓库Sakuraone0424/ustc-private-backup-transfer，分支backup-transfer-20261027-4e84fde3，提交12793ae231a4bce77f9ed0bfcaaf8885660e167a；此前独立下载校验通过。用户现已接受该私人仓库作为长期位置，不再要求Google Drive或手机本地交接。未新增上传。
- 新集群目录`/workspace/private-intake/ustc-restore-rehearsal-a84gic2k/pgdata`，仅127.0.0.1:55437及私有Unix socket；DB `ustc_restore_verified_20261027`，UTF8，PG17.11，扩展仅plpgsql1.0。新集群system identifier与原本地5432集群不同；恢复前全库无用户对象。
- initdb后单独CREATE DATABASE；pg_restore仅使用`--dbname`、`--single-transaction`、`--exit-on-error`、`--no-owner`、`--no-acl`。没有--clean、--create、DROP或覆盖已有库。
- 全部68表行数与排序JSONB行SHA **100%匹配该备份Manifest**；18项迁移与仓库SHA匹配；六来源身份与原始字节SHA匹配。重启新集群后再次完成同样68表独立读回。
- 80个外键逐项检查无孤儿；68主键、33唯一约束、87 CHECK、80外键均已验证；无无效/未就绪索引；39条身份序列后续值均安全。源表内容未写入新记录。

“100%”仅指此备份存储内容匹配，不代表科学知识、官方原卷或OCR文字100%正确，也不代表包含Railway旧成绩。备份中users、历史attempts/attempts_v2、FSRS均为0，原生产历史成绩仍须原库独立保护。

## 实际数据与应用验证

| 内容 | 恢复实际值 | 质量含义 |
|---|---:|---|
| 来源 | 6 | 四TXT、两DOCX原字节均匹配 |
| 教材OCR页标 | 1,915 | 654/587/289/385连续，无空页；不是PDF/印刷页验证 |
| source_units总计 | 12,396 | 教材9,655 + 真题转录2,741，不混称全部教材正文 |
| 教材章节分组 | 83 | 79已分类、4未分类；教材未分类275单元，另有2个真题转录单元未分类 |
| 年份结构项 | 52 | 生化23年、细胞29年；不代表52份完整原卷 |
| 转录主问题候选 | 1,921 | 生化1,197、细胞724，全部待核验 |
| 旧映射建议 | 17,128 | 未自动升级为可信知识关联 |
| 当前名称候选 / 精细映射 | 11,044 / 3,139 | 版本隔离，不累加历史算法工作项 |
| 现行题稿 / 全部版本 | 38 / 45 | 7条修订链接；45全为draft |
| 科学审核ready / 正式发布题 | 0 / 0 | 38条identity通过不等于科学/答案通过 |

六份恢复原字节重新解析后，单元数、正文、章节、小节、OCR定位和页标均与存储索引一致；不是重复导入。38处风格引用的声明年份与DOCX解析归属一致；现行题稿无词面重复对（不等于语义唯一性科学审核）。38道现行候选通过Question结构/题型分值、引用短摘录、学科/文件SHA/页码/单元、风格转录来源及逐项rubric证据定位检查；45包版本摘要一致。答案语义与教材是否充分支持尚未完成独立科学审核，未发布或改状态。

真实恢复库上的后端HTTP检查：/db/health、/health、候选读取、ATP正文检索、六来源状态均200；未认证候选读取403。独立只读应用账号能读、写入被拒绝。单独recovery_probe schema通过实际app.db.get_db提交一个无教材/成绩的探针，重启后新连接读取成功；不是伪造答卷或成绩。正式学习成绩/FSRS端到端未完成。

针对性集成测试 **83 passed、0失败/跳过，1既有Starlette弃用警告，21.53秒**，连接新恢复DB；覆盖迁移/旧60题成绩夹具保护、恢复门禁、资料/真题、限时答卷、动态组卷、FSRS及归档。测试夹具仅在rollback隔离schema，不写实际资料schema；无需重复410全量测试。

## 异常及限制

- 还原数据没有丢失或Manifest不匹配。报告脚本先后发生容量Decimal无法JSON序列化、prepare返回dict却按tuple解包两处编排错误；修正报告逻辑后继续只读审计，没有重复pg_restore。原包、18迁移、业务代码均未改。
- 77组标准化重复题干涉及181次出现（104额外出现），其中10组跨学科；只是文本候选重复，可能是真实跨年重考/共通概念，不自动删题或视为错误。同旧25条transcription_repeat工作项判据不同，不相加。
- 细胞2009/2010各9/11道题型未知；细胞2003存在1处节声明数量冲突。生化2006/2007/2010/2014及细胞2001/2014存在节内重复编号，详见年度表；重复编号可能跨子卷，不能直接断言原卷错误。
- 3,911个单元有风险标记或未分类；原OCR报告缺损3、未转录图像25、OCR疑点1等均保留，不能猜测补全。来源没有任何PDF/印刷页已核验声明。
- source_review_events、正式knowledge_links、已核验原卷exam_records均0；科学/答案AI任务未执行，ready=0。候选数充足不能替代ready配额。
- 本地恢复通过，具备进入云端独立恢复准备的技术基础；**不是云端恢复/上线批准或成功证明**。

## 容量、可恢复性与接续

刚恢复DB 425,924,275字节（约406.2MiB），实际源表/索引416,301,056字节；针对性测试和探针后DB448,214,707字节。整个本地集群目录992,203,732字节，其中WAL520,093,696字节（恢复/测试后当前值，不是峰值）。因此500MB旧卷绝不可作为恢复目标；独立2GB卷是起步建议，仍需监测WAL、临时空间及增长。没有为了省空间删教材或清历史。

本机shared_buffers=32MiB、max_connections=20，重启后7个PG进程PSS合计51,488KiB；这是本地空闲观测，不等于Railway计费内存、峰值或月均。云端整体预算仍需完整工作区Usage及现有测试API用量，见railway-cost-budget.md。

本地完整日志、含原文定位的风险清单、逐章/逐年详情、恢复连接信息仅留私有目录，不进Git。当前本地库和备份保留可续接；临时环境仍可能回收，原始可恢复数据已在授权私人Git仓库中。

## 28题与旧60题产品口径

当前唯一正式自定义方案仍为28题/150分/60分钟：两科各14题/75分，各3单选×2、3填空×3、4名词×4、3简答×8、1论述×20。此前60题需求已由后续明确规格取代；本轮不擅自恢复或混用。旧60题成绩只读接口与迁移保护仅用于历史审计，不能将旧库混入28题ready池。当前不保留60题产品、双模式切换或恢复选项；任何历史讨论均不得作为恢复旧规格的依据。唯一现行要求见[产品规格](product-specification.md)。

## 可复核证据

私有根目录`/workspace/private-intake/backup-user-handoff`下：local-rehearsal-execution.log、restored-data-audit-final.log、restored-db-integration-tests.log、restart-readback-execution.log。恢复集群目录下：data-audit-summary.json、chapter-audit-private.json、year-audit-private.json、candidate-audit-private.json、foreign-key-audit.json、sequence-and-cluster-check.json、app-compatibility.json、restart-persistence-result.json。带凭据的应用配置不上传。

本报告配套recovery-validation-20261027.json只含计数/身份哈希与校验结果，不含教材、题干、答案或凭据。data-foundation-audit-20261027.md提供脱敏逐章/逐年计数；所有原文和具体疑点位置保留私有队列。


针对性测试的实际执行参数（TEST_DATABASE_URL仅指新本地socket，不含密码）：

```sh
TEST_DATABASE_URL="dbname=ustc_restore_verified_20261027 host=/workspace/private-intake/ustc-restore-rehearsal-a84gic2k/socket port=55437 user=agent" PYTHONPATH=backend .venv/bin/python -m pytest -ra -q --tb=short -p no:cacheprovider   backend/tests/test_migrations.py backend/tests/test_recovery_deployment.py   backend/tests/test_sources_exams.py backend/tests/test_timed_training.py   backend/tests/test_dynamic_integration.py backend/tests/test_memory_archiving.py
```
