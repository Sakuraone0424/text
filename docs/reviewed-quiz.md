# 肖涵自定义强化训练 V2

本 PR 延续 `feat/reviewed-dual-subject-28`，退役旧60题业务入口，保留旧迁移和历史成绩只读访问。最新用户要求覆盖此前保持旧业务可用的要求。代码已在开发环境运行；生产发布、私有资料导入和现有 ChatGPT 应用接入状态分别列在 [验收报告](acceptance.md)。

## 固定规格

这是用户自定义训练结构，不是声称中科大官方考试固定采用的结构。

| 每个学科 | 数量 | 每题分值 | 小计 |
|---|---:|---:|---:|
| 单选 | 3 | 2 | 6 |
| 填空 | 3 | 3 | 9 |
| 名词解释 | 4 | 4 | 16 |
| 简答 | 3 | 8 | 24 |
| 论述 | 1 | 20 | 20 |
| 合计 | 14 | | 75 |

生物化学与分子生物学、细胞生物学结构相同：全卷 28 题、150 分，实际开始后限时 **60 分钟**。[唯一现行产品规格](product-specification.md)明确不提供每日60题或双模式。`training_spec.py` 是唯一代码规格定义，`GET /v2/training-specification`供网页读取，定时备卷和ChatGPT适配器共用默认蓝图；Blueprint 校验科目、固定数量、分值与 3600 秒时长，题目校验 max_points 和 rubric 之和，组卷前再次校验每科 75 分。旧示例 4/4/2/2/2 不再接受。历史 V2 试卷的快照及分值不被改写。

```json
{
  "specification": "xiaohan-150-60-v1",
  "quotas": {
    "biochemistry": {"single_choice":3,"fill_blank":3,"term":4,"short_answer":3,"essay":1},
    "cell_biology": {"single_choice":3,"fill_blank":3,"term":4,"short_answer":3,"essay":1}
  },
  "points": {"single_choice":2,"fill_blank":3,"term":4,"short_answer":8,"essay":20},
  "duration_seconds":3600
}
```

最后两个字段可以省略并使用上述固定默认值，不能改为其他数值。规范标识不同的已有当日卷不会被覆盖。

## 60 分钟作答协议

学习者沿用原 Cookie、`x-api-key` 或 Bearer 认证。未开始时只返回试卷元数据，不提前下发题目。

1. `POST /v2/papers` 准备完整试卷；缺题 HTTP 409，分学科分题型报告 required/available，不创建半张卷，不回填未审核种子题。
2. 用户点击开始后 `POST /v2/papers/{id}/start`。服务端 `clock_timestamp()` 写入 started_at 和 deadline；重复调用保持原截止时间。
3. `GET /v2/papers/{id}` 恢复题目、服务端时间、截止时间、剩余秒数、草稿及版本。用户换设备或刷新不会重置时间。
4. `PUT /v2/papers/{id}/drafts/{question_id}`，传 `expected_version` 与 `value`。草稿可含 text、blanks、image_ids、flagged。纸面级版本和数据库行锁防止并发静默覆盖；409 要求先读取最新版本再明确合并。
5. `POST /v2/papers/{id}/submit`，传 expected_version 和可选 answers 映射。`answers:{}` 使用服务端已存草稿；不要求填满。空白按 0 分，已作答主观题待批，尚未批完时总分 null。
6. 过期请求只按截止前保存的内容交卷，不能接受迟到文字或图片。提交过程中跨过截止时间会回滚新合并的答案，保留先前草稿。重复交卷返回 HTTP 200 和同一 attempt 的原结果，忽略迟到的新答案；服务重启或首次响应丢失后可安全重发，不创建第二条成绩或重复FSRS事件。

API lifespan 默认运行独立的 deadline worker（需 DATABASE_URL，QUIZ_WORKER_ENABLED 默认 1）。即使浏览器离线，worker 每秒处理到期卷。访问过期卷也会补做幂等交卷。若 API 进程停机，恢复后处理积压记录，逻辑交卷时间仍为 deadline；写入检查始终拒绝超时补答。不是实时操作系统，不承诺数据库宕机期间仍能立即物理写入。

Notion 或定时准备在另一条 worker 循环执行，不能因外部同步缓慢阻塞倒计时。单独运行 `python -m app.worker` 时要按部署方案避免不必要的重复 worker；数据库锁仍保护重复执行。

## 手机兼容界面、照片与离线恢复

同一 FastAPI 服务的 `/` 与 `/training` 均只提供正式28题界面，历史只读档案通过独立按钮访问。它是现有后端的兼容界面，不是声称替换或改造了现有 ChatGPT 内应用。

支持纵向滚动、点击选项、文本及多空输入、标记题、进度、统一交卷确认、拍照 capture 入口及相册入口。文本草稿在本机保留后自动送至服务端；Service Worker 只缓存训练页面和静态资源，**不缓存认证或私有 API 响应**。照片在 IndexedDB 暂存，断网/刷新后重连重试。上传成功前不能把照片当成已交付；本机配额耗尽或用户清除浏览器存储时无法保证本机数据保留，会显示存储/同步错误。

断线的本机内容须在 deadline 前同步，超过截止时间仍留在本机也不会补算。离线倒计时只是最后服务端时间的估算，最终判定始终由服务端完成。多设备真正冲突暂停同步并保留本机版本；相同答案丢失确认的情况可安全确认副本，不静默覆盖不同答案。

`POST /v2/papers/{id}/images/{question_id}?expected_version=N` 接收 JPEG/PNG/WebP 原始字节，单张最多 8 MiB、20M 像素、每题最多 5 张。上传、解码和接收耗时都计入计时。原图留在私有 PostgreSQL，`GET /v2/images/{id}` 必须验证答卷归属，响应 no-store；相同照片重试不会重复添加。

## 教材导入、分页和证据门禁

当前唯一来源是用户OCR文件夹下六份校订TXT/DOCX，采用OCR-only模式，不再要求PDF。清单、身份及声明范围见 [ocr_collection.json](../backend/app/ocr_collection.json)，完整流程见 [private-source-intake.md](private-source-intake.md)，版本规则见 [data-versions.md](data-versions.md)。

上册654、下册587、高分笔记289、细胞385连续页标，合计1915个；上/下册支持册名，细胞不再192+193重置。保存原文件字节/SHA、所有页标、章节、小节、段落ID与知识链接。引用匹配文件ID+SHA+OCR页/段落+短摘录，不声明PDF或印刷页已核对。DOCX保留段落/表格路径，年份与题号只产生待审候选。

文件存在、读取、预检、入库、文本复核、科学审核与发布状态分离；未解决的图像/结构式/表格/OCR问题不能猜测发布。新资料版本不继承旧审核或映射，旧成绩/快照不改写。当前Codex没有Drive读取工具/身份，真实六文件仍未读取；只需经合法方式交接这六份文件，不需要其他资料。

## 真题、考频与审核

`POST /v2/exams` 记录原题编号、科目、年份、类型、原题/选项、源单元、知识点、参考答案类型、评分点、教材证据和原卷状态。题干须与来源正文匹配。官方答案须另有已验证的答案源单元；系统个人参考答案不能称官方。缺答案/评分/教材证据的真题仍可保留作分析，不会绕过正式题库的答案门禁。

`GET /v2/exams/coverage` 输出生化 2004—2026、细胞 1998—2026 的逐年核查状态；未获取显示 original_not_acquired。已有记录分别报告原卷核验、完整卷、缺答案及缺教材证据。没有读取两份私有汇编，不能把文件名范围算成已核实逐年覆盖。

`GET /v2/exams/frequencies` 按概念、结构功能、代谢、机制、比较、实验、综合类别和知识点统计。相同规范化题干在同一年、不同转录来源只计一次；不同年份重复考查仍分别计数。字符相似不能自动判定语义等价，变式与风格判断须人工审查。

题目必须有参考答案、评分rubric、当前指定教材证据及同科同型的风格参考ID。OCR-only真题转录经text/exam_style人工审核及风险处置后可作风格参考，但不能因此升级为原卷可信考频；OCR-only题目科学依据仍须text/science复核。五维专家审核包含科学正确性、真题风格、唯一性、题型适配、重复率；每项给出理由，并记录教材核验说明。score 为 0—100，发布阈值 90，但任何强制项失败都不可用总分绕过。

自动去重：题干 NFKC、去空白标点、折叠大小写，SequenceMatcher 相似度 >=0.85 阻止发布；报告比较量、相似量、比例及最大相似度。发布时重查，事务锁防止审核后重复条件变化的竞态。此检测不保证语义去重，专家须检查同义改写、答案唯一性和干扰项合理性。

操作员使用独立 `x-review-api-key`；`QUIZ_REVIEW_API_KEY` 未设置时相应接口 503。普通答题凭据不能发布或批改。题目内容、试卷快照、审核及批改事件受不可变/追加约束保护。

## 组卷、自适应、评分与 FSRS

候选只来自发布合格题。动态引擎融合已核验同范围真题、实际已批作答和真实FSRS状态；初始参考权重0.55/0.30/0.15，按每科证据置信度及学习阶段校准。没有固定重点章节或70/20/10。可复现加权抽样、探索下限、章节软约束及完整选择审计详见 [dynamic-engine.md](dynamic-engine.md)。

新题原则上不复用本人历史旧/新试卷题干。真实到期 FSRS 题允许复测，明确 `is_retest=true` 并计入 28 题，不冒充新题。没有合格题就报错，不补题或改分。

单选自动判分。填空支持显式同义表达、中英文与缩写列表，保留科学大小写，不能自动把 mM 和 mm 等同；合理大小写变体应明确列入。多空使用 `blanks:[{accepted_answers:[...],points:...}]`，占位符 `____` 数量须一致，逐空分值合计 3 分，返回逐空得分。单空旧字段 accepted_answers 继续可用。

主观题默认 pending_review，不按长度判分。`POST /v2/papers/{id}/answers/{qid}/grade` 按快照 rubric 逐项赋分，返回命中/缺失评分点、可选概念错误、表述问题、建议、教材依据。没有填写的分析字段不代表“无错误”。批改事件留存操作员、时间、评分细则和手写识别快照，不能覆盖完成成绩。重复评分拒绝，不额外改变记忆状态。

手写图片先通过操作员 `POST /v2/images/{id}/transcription` 保存识别文本、置信度与确认状态。低置信度或未确认照片不能猜字或判零；确认前拒绝评分。当前没有后台 AI/OCR 模型调用，使用人工确认兼容流程。外部收费模型未接入，当前 ChatGPT 会话批改能力也未宣称已联通后端。

新错题使用 **fsrs==6.3.0**，保存稳定性、难度、状态、到期时间及每次前后状态。`GET /v2/memory/due` 查询到期卡，`POST /v2/memory/{qid}/review` 提交 Again/Hard/Good/Easy（1—4）、版本与唯一 event_key；重复事件幂等。空白、待批或正确题均不建立失败卡或写入记忆事件；仅实际作答且已批错误进入失败记录。旧 1/3/7 天记录原样保留，绝不称为 FSRS。兼容界面提供先回忆、再显示答案、最后反馈的流程。

提交后可通过 reflection 接口或界面记录七类错误原因：概念不清、记忆遗漏、审题、机制、表述、计算、章节联系；未知原因不自动猜测，复盘记录只能追加，不修改成绩。

`GET /v2/stats/weekly` 汇总本人近 7 天章节得分与待批题量；旧 `/stats` 保持原语义。

## Notion、适配与自动化状态

Notion 默认关闭。只有运行环境明确配置 `NOTION_SYNC_ENABLED=1`、NOTION_TOKEN、NOTION_DATABASE_ID，且用户通过 `/v2/archive/consent` 或界面明确启用归档后，才处理出站任务。数据库需有 Name（title）和 External ID（rich_text）属性，API 版本固定 2022-06-28。切勿把令牌放入 Git 或聊天。

唯一 attempt_id 出站任务保存试卷日期、逐题答案/评分/证据、各科成绩、复盘、周度章节统计、FSRS状态、动态算法/权重/入卷依据及带时间和配置的掌握估计对照。变化明确不代表本卷的因果提升，缺历史轨迹不补造。失败后退避重试；通过 External ID 找回不确定写入，避免重复建成绩页；追加有摘要校验的版本分块，不删除用户自己的块。只有读回内容摘要一致才是 verified。pending/synced/verified/failed 分别表示等待、已写未核验、已验证、失败。撤销许可停止后续处理，不删除历史记录或远端已存内容。

`chatgpt_adapter.py` 提供版本化准备、开始、恢复、保存、交卷、查分协议与模拟契约测试，目的端须显式配置 HTTPS URL。它不是已发布的 ChatGPT Action，也没有伪造 app ID、后端调用关系或认证配置。

北京时间 20:00 准备任务需要显式 `QUIZ_DAILY_PREP_ENABLED=1`，默认关闭。唯一任务键、行锁、重试时间、30 秒 SQL 超时及状态表防止重复发布；缺题显示 failed，不显示成功。生成不启动作答计时。这里只支持已部署进程内 worker，不声称提示词本身会定时运行。

## 迁移、运行、部署和回滚

保留已应用001—010文件字节不变。004 新增限时、草稿、图片、FSRS 与归档对象，005 私有来源/真题，006 批改审计，007 任务状态、008 追加式错题复盘。009新增范围、完整年度样本、追加分类、知识依赖、策略版本及选题审计；不清空、替换或降级历史业务表。旧 `/quiz/today`、`/quiz/submit`、`/review/due` 认证后返回410且不进入OpenAPI；旧静态页面、种子、导出到旧题库及1/3/7调度已移除。`/history`、`/stats` 保持只读语义；`/legacy/papers/{id}` 按归属返回原题、答案、原成绩，`/v2/history` 汇总历史，不重新批改。旧种子仅作为明确标记的测试夹具存在。

开发运行：

```sh
cd backend
python -m pip install -r requirements-dev.txt
# 仅对明确选择的开发数据库执行；生产步骤本 PR 未执行。
python migrate.py
# APP_PASSWORD、SESSION_SECRET、QUIZ_API_KEY、QUIZ_REVIEW_API_KEY 安全注入，勿输出。
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

同服务 `/training` 为兼容入口；浏览器正式使用须 HTTPS，以保证 Secure Cookie、生效的 Service Worker 与照片权限。Python 3.12、PostgreSQL 17 用于本次验收。实际 Railway 配置可能在控制面另有启动前迁移命令，仓库 Dockerfile 本身只启动 uvicorn；本 PR 未读取或修改控制面。

未来独立批准发布时：先备份并验证恢复路径；在 staging 使用生产结构的脱敏副本运行迁移和测试；确认私有资料存储、密钥、TLS、worker、容量和 Notion 权限；在维护窗口执行增量迁移，再切换应用版本。数据库迁移 SHA-256 登记、事务和锁保证重复运行与失败回滚。不要修改已应用文件；不要把删除生产库当成回滚。

发现问题先设置 `QUIZ_PREPARATION_ENABLED=0` 暂停新组卷、`QUIZ_DAILY_PREP_ENABLED=0` 停止每日准备；已开始的卷仍允许恢复、保存、到期交卷和批改。策略回退通过新建包含旧配置的新版本并重新分配完成，不覆盖审计。

应用回退只使用在staging验证的、保留旧业务退役与证据门禁的兼容V2版本；不要直接切回main或旧基线恢复60题入口及种子。数据库保留009/010和全部成绩，不执行破坏性down；必要时暂停新卷、修复兼容版本后恢复。真实部署与备份恢复尚未执行，不能宣称已经验证生产回退。010追加分卷原件、逐页覆盖及独立OCR/原件坐标；旧单文件资料不改写。011新增OCR-only版本、独立人工审核和候选转录；`/db/health` 检查全部11项迁移，配置数据库时 `/health` 同时检查就绪状态。

验证命令：

```sh
cd backend
python -m playwright install --with-deps chromium
TEST_DATABASE_URL='明确选定的开发数据库连接' python -m pytest -ra -q -p no:cacheprovider
```

大多数测试使用独立 schema+强制回滚事务；真实 HTTP、浏览器、并发测试只创建/提交/清理本次随机生成的 `quiz_live_test_*` schema，不使用 public。未设置 TEST_DATABASE_URL 时数据库测试明确 skip，不能视为验收通过。
