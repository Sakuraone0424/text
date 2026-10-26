# 资料版本与有效性

首批题稿采用不可变candidate_review_packets、追加candidate_human_decisions和candidate_revision_links（014—015）。AI分析与人工决定分表；新版本不继承批准，旧版本不可发布。38个有效题稿、45份历史版本均不改变教材SHA/索引或既有213342工作项。详见[首批候选](first-candidate-batch.md)。

审核准备覆盖层：evidence-work/1.6、transcript-review/1.4，不改变ocr-index/1.1原导入索引或原文SHA。013新增不可变pending队列；001—012保持原字节。每次分析快照可追溯，当前进度按版本过滤；辅助分析不是source_review_events人工签署。补充映射版本refined-exam-mapping/1.0单独保存。详见[当前进度](review-progress-20261026.md)。

当前清单：ocr-corrected-2026-10-26-v1；目录及六个身份见 [ocr_collection.json](../backend/app/ocr_collection.json)。文件ID是定位信息，SHA只能在合法取得实际字节后计算；不把用户报告已定位等同Codex已读。

- 四本校订TXT分别654、587、289、385连续OCR页，合计1915；无192＋193重置，无PDF/印刷页核验声明。
- 两份DOCX声明范围更新到2026。声明范围不等于逐年真题完整；原卷/转录、答案来源、教材映射与考频资格独立记录。
- 旧文件ID、旧交接清单、旧OCR及PDF退出当前有效资料，不回退搜索、不用于补齐缺题。历史存储和成绩不删除；新发布与新组卷只接受当前清单当前版本。
- source_documents以文件ID+实际SHA去重；source_versions保存原字节、格式、清单版本、父版本和材料key。正文变化创建新文档/单元，不继承reviews、knowledge_links或科学批准。相同页码不证明身份一致。
- 段落身份由文件SHA、所属文档版本、单位ID/ordinal、OCR坐标和原文共同确定；审核事件绑定unit_sha256。正文/坐标/原字节不可变；复核以追加approved/rejected事件表达。
- 书目更正采用source_bibliography_reviews追加签署，不静默修改旧版本头。新书目若使已发布题目的引用不再匹配，新组卷重新检查并排除；历史试卷仍保留原快照、答案及成绩。
- 不实现自动复核复用：即使新文件部分文字相同也需重新复核与映射。被新版本替代的旧SHA不能通过幂等导入悄悄激活旧批准；需明确人工处理，不能当作新版本导入成功。
- 已开始/已交试卷不因资料更新改分或重置60分钟；历史来源失效影响新发布和新选题。FSRS事件、真实作答成绩仍保留，旧材料不会借复习进入不合格候选池。
- 001—011迁移保持与4b070e2字节一致；012只新增不可变、仅pending的source_mapping_proposals，不修改原成绩/试卷或正式knowledge_links。旧单文件/分卷工具继续可读，无破坏性down或生产迁移操作。

当前真实六文件已通过用户ZIP交接，全部SHA匹配manifest，已在明确授权的独立本地非生产库导入、独立读回；全部仍待人工/科学审核，正式映射及发布为0。详见 [真实验收](real-source-acceptance.md)。索引版本升级为ocr-index/1.1、transcript-index/1.1，恢复数字章节及Word自动编号；原始文件SHA未改变。旧索引版本不得静默覆盖；相同文件已存在不同索引元数据时导入器拒绝，需显式迁移评审。此次真实文件为首次导入，未继承此前模拟或旧版本审核。
