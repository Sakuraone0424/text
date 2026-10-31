# 用户六文件真实字节审计（2026-10-29，隔离工作区）

**用户题源定位：** 两份历年考研真题汇编按用户明确指定视为真实真题。审计仅检查文件内容与提取完整性，不再开展真题真实性判定。\n\n**结论（范围限定）：** 六份文件已从用户连接的 Google Drive 下载到临时隔离工作区，已逐字节哈希；四份OCR文本的页标全部按版本清单连续，从1开始，总计1915个。**尚未写入Railway正式或测试PostgreSQL。**本审计不代表科学内容或未附答案的题目已完成独立答案核验。

| key | 字节数 | SHA-256 | OCR页标 / 预期 | 其他 |
| --- | ---: | --- | ---: | --- |
| biochemistry_upper | 1,749,006 | `89b226f7c9da53179943375751cacdf873b3693efdb426304a41ed1556e3b013` | 654/654 | 页标连续，无重复 |
| biochemistry_lower | 1,520,962 | `1cdd646caae4fe185fc90623e2706ed9503690fe93a41fb91f381927694c9a5b` | 587/587 | 页标连续，无重复 |
| biochemistry_notes | 915,492 | `dbae27206fed8ad0b1303dcf759c73b55194dbb4fa6308f392826592ec85bd6c` | 289/289 | 页标连续，无重复 |
| cell_biology | 1,387,287 | `de3dd4e75ba10c5a4f49096949a0e4cf44512856070440fd410c373e0745bae1` | 385/385 | 页标连续，无重复 |
| biochemistry_exams | 1,697,187 | `6add10fad4708453f7f8cff5562ee73bf0853d70e43c8c3d67d06299cf82c001` | 不适用 | DOCX，含27个嵌入媒体对象 |
| cell_biology_exams | 272,001 | `63b0eea0b1dbb5a7b6323dcd769792bba60a96ce9d93c6b8bd8c79fd4939ef79` | 不适用 | DOCX，含4个嵌入媒体对象 |

**合计：** 7,541,935字节；1,915个连续的OCR页标，0个重复、0个缺失。四份文本中出现大量“以原图为准”或图示依赖，不能仅凭分页完整判断图、公式及反应结构已被可靠转录。DOCX的31个媒体对象不能只按文本抽取处理。真题按用户所提供的原始DOCX认定；年份覆盖报告只陈述当前文件中实际收录的题目、图片与文本，不推断缺失内容，也不质疑来源真实性。

**2017年生化真题的实录数量：** 当前DOCX的2017年章节中，可辨认6道名词解释及4道简答，共10道。系统按这10道实际材料保存和分析，不擅自增删，也不据此怀疑真题真实性。图片或文字识别问题另外记录技术风险。\n\n**锁定版本：** `app/ocr_source_sha256_lock.json`；没有把六份版权原文件提交Git。源文件仅在用户私有隔离工作区中，私有导入包可以传给隔离QA主机。

## 安全导入路径

1. 非生产PostgreSQL准备**独立数据库**（不要仅把 production 上的 schema 改名当成完全隔离）。
2. 在非生产库执行已有 `python -m app.source_intake init --schema source_staging_qa_20261029 --target staging`。环境只配置 `SOURCE_STAGING_DATABASE_URL`；不得把生产 URL 指向该变量。
3. 解压用户私有六文件包到专用目录，运行：
   ```bash
   python -m app.collection_seed --directory /private/six_source_import --report /private/reports/preflight.json
   python -m app.collection_seed --directory /private/six_source_import --report /private/reports/staging-readback.json --write-isolated --schema source_staging_qa_20261029 --target staging
   ```
4. 只有全部六份文件哈希、索引、页标和数据库读回通过才报告成功；否则整批事务回滚。记录OCR质量风险，后续进入独立AI证据复核；**不因导入成功自动发布任何试题**。
5. 在QA库运行 `GET /v2/quality/six-source-readiness` 与 `/v2/quality/60q-bank-readiness`，接着根据实际合格数量确定达到120题储备目标的缺口。

**当前状态：** 本轮完成文件发现、真实字节审计、SHA锁定、隔离批量导入工具与自动化测试。尚无授权的非生产数据库连接以完成实际数据库导入与读回，不能称“六份资料已经进系统数据库”。后续不可直接部署60题上线。
