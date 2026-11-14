# 部署说明

## 服务

| 服务 | 构建 | 启动 |
|---|---|---|
| 网站 API（正式站、QA） | Railway 服务级设置 → `backend/Dockerfile` | `uvicorn app.main:app`；Web 进程内的 `app.worker` 由 `QUIZ_WORKER_ENABLED`（默认 `1`）控制 |
| AI 任务 worker（第 8 步新开） | Railway 服务级设置 → `deploy/railway-automation/Dockerfile`（`worker.railway.json` 仅作参考） | `python -m app.auto_worker`，cron `*/5 * * * *` |

AI 任务（出题、一轮独立审核、主观题判分）由 `auto_worker` 执行，经 `app.harness_gateway` → `app.harness_runner` 调用 `deepseek-harness-sdk`（`backend/requirements-harness.txt`）与 `backend/harness/restricted.patch.yml`。这两项不能删。

出题与真题入库的规划都在每次 worker 运行时进行，且**不依赖 `AI_FULL_CORPUS_ENABLED`**：每个请求只带一道题相关的教材段落（最多 4 段、5000 字），出新题按题型缺口、考频加权选段落。该开关只控制整库知识点提取，必须保持关闭。每次运行的模型调用次数由环境变量 `AI_CALLS_PER_RUN` 决定：默认 2，范围 1 到 10（超出按边界取值，无法解析按默认）。每次调用后立即派发结果，新产生的审核任务可在同一次运行里继续执行。预算、并发和重试上限对每一次调用照常生效。一道题约需 2 次调用（出题 + 一轮审核）。默认值的计算：每天约 80 次出题 + 80 次审核 = 160 次调用，只在低峰时段跑；工作日低峰 = 24 − 3（9–12 点）− 4（14–18 点）= 17 小时，worker 每 5 分钟一次 = 204 次运行；160 × 1.25（重试与失败余量）÷ 204 ≈ 1 次/运行；取 2，是为了出题后能在同一次运行里接着审核，并让 4 小时高峰积压的任务清得更快。周末和法定节假日全天低峰，余量更大。

## 费用控制

| 变量 | 默认 | 说明 |
|---|---|---|
| `AI_MONTHLY_CAP_CNY` | `90` | 每月硬上限（人民币，按上海时区的自然月） |
| `AI_GRADING_RESERVE_CNY` | `15` | 其中只留给判分的部分：出题、审核、修复花到 75 元（判分还没用这笔钱时）就停，判分和兼容性探测一直可用到 90 元 |
| `AI_GENERATION_THINKING` | `off` | 出题、修复的 thinking 档位（`off`/`low`/`high`/`max`） |
| `AI_REVIEW_THINKING` | `low` | 审核的 thinking 档位 |
| `AI_GRADING_THINKING` | 不设 | 不设则沿用模型设置里的档位 |
| `AI_CALLS_PER_RUN` | `2` | 见上 |

- 出题、审核、修复只在低峰时段运行（北京时间工作日 9–12 点、14–18 点为高峰；周末和日历里的法定节假日全天低峰；没有当年已核验日历时，工作日仍按高峰算）。高峰时任务排队等待，不丢弃。判分不受限制。
- 额度按**已结算的真实用量**计算；失败、超时或还在跑的调用按最坏情况的预留额计，不会被遗忘。
- 用到的每个 thinking 档位都必须先在网页的 `/v2/model/probe` 里对该模型做过兼容性探测（至少 `off` 和 `low`），否则对应任务入队时会被拒绝，不会悄悄换档。
- `GET /v2/model/spend`：当天、当月花费，输入/缓存命中/输出 token，按任务类型的明细，最近 50 次调用，剩余额度，以及「平均每入库一道题花多少钱」。额度用完时网站首页显示「本月 AI 额度已用完」。
- 提示词固定部分（规则和输出格式）排在最前面，每次变化的内容排在最后，以命中前缀缓存。
- 新题不足时，用已入库、14 天内没出现过的旧题补齐，卷面标明「含复用题」。

`backend/data/` 下两个旧资料文件含教材摘录，只由 `app/legacy_reuse.py` 就地读取：只复制进 AI worker 镜像（`deploy/railway-automation/Dockerfile`），不进 API 镜像，内容不写日志。

## AI worker 运行条件（从代码核实）

没有冻结范围（试运行 / 复核战役 / QA 自动化）的普通任务，由 `app/auto_execution.py` 的执行守卫放行。守卫只放行出题流水线的任务类型（`generation`、`quality_review`、`repair`、`grading`、`compatibility`）；整库提取类任务不会执行。守卫还核对：模型和输出上限与任务冻结的一致；每个请求的教材段落不超过 4 段、5000 字、整体 24000 字节；真实请求体与批准的提示词完全一致（无工具、无历史、无隐藏上下文）。

worker 真正开始调用模型，需要同时满足：

| 条件 | 说明 |
|---|---|
| `AI_AUTOMATION_ENABLED=1` | 否则 worker 直接退出 |
| `DEEPSEEK_API_AUTHORIZED=1`、`DEEPSEEK_PRIVATE_DATA_AUTHORIZED=1`、`DEEPSEEK_API_KEY` | 调用与发送教材段落的授权 |
| `AI_QUEUE_AUTOPLAN=1` | 否则不会自动规划出题、真题入库、复用旧资料 |
| `QUIZ_DB_SCHEMA` 以 `source_staging_` 开头 | `auto_worker.main` 的现有检查；用于单项目合并时需要放宽（第 11 步） |
| Railway 目标校验 | 在 Railway 上还需 `QUIZ_ISOLATED_TARGET_APPROVED=1` 及与部署目标一致的 `RAILWAY_*_ID` / `QUIZ_APPROVED_RAILWAY_*_ID`（`app/deployment_target.py`） |
| 数据库里 `ai_settings` 的用户未停止，三项 CNY 预算均启用且大于 0，模型已通过兼容性探测（`/model/probe`），CNY 价格已核实 | 缺任何一项，任务在领取时被挡住 |

## 部署设置的来源

**以 Railway 服务级配置为准。** 仓库里不再有根目录 `railway.json`：它会作为 config-as-code 覆盖同仓库所有服务的设置（曾让 worker 用 `backend/Dockerfile` 构建、缺少两个旧资料 JSON）。每个服务在 Railway 里单独设置：

| 服务 | Dockerfile | 启动 / 其他 |
|---|---|---|
| `training-api-qa`（网站） | `backend/Dockerfile` | 启动命令 `python migrate.py && uvicorn app.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 5`；健康检查 `/health` |
| `training-worker-qa` | `deploy/railway-automation/Dockerfile` | 启动 `python -m app.auto_worker`；cron `*/5 * * * *`；重启策略 `NEVER` |

`deploy/railway-automation/worker.railway.json` 只作为上述设置的参考，不会被任何服务读取。正式站（`xiao-ustc-quiz`）运行 `main`，不受此变更影响；第 10 步切换时需在其服务级设置里同样写明。

## 自动升级与已下架模型

- 「允许升级」只是**允许**：新模型必须先由人工在第 8.5 步质量对比通过后记录（`POST /v2/model/benchmark-approval`，需要登录并带 `x-review-api-key: <QUIZ_REVIEW_API_KEY>`，body `{"model_id": "...", "approved": true, "note": "..."}`；没有探测过的模型会被拒绝）。探测不会改动这个记录；模型元数据变化会清掉它。
- worker 每次运行评估一次，**不会静默**：`worker-run` 里的 `upgrade=` 字段是 `off` / `none` / `waiting:<模型>=<原因>` / `switched:<模型>`。切换前必须同时满足：设置档（判分）、出题档（off）、审核档（low）都已对该模型探测通过；当前人民币价格已核实；最坏单次费用在每日/每月/单任务（及试验）预算内。缺的探测会自动排队（只发合成算术，费用极小）。
- 已排队任务冻结的模型下架或目录变化时：普通任务按当前选定、已探测、有价格的模型**重新冻结后继续**（输入不变）；不能重排的标明原因 `model_delisted_no_replacement` / `replacement_effort_not_probed:<档>` / `replacement_unusable:<原因>`，出现可用替代后 worker 自动把它们放回队列。冻结试验、审核活动、QA 授权任务和兼容性探测任务不会被移到别的模型。

## 学习数据、错题筛选、短练习与添加到主屏幕

- **统计接口**（都需登录、只返回数字、不含密钥；题干只出现在错题列表里）：`GET /v2/stats/overview`（首页：倒计时、连续天数、近 7 次均分及较上周变化、客观/主观得分率、最薄弱 3 章、到期复习数、明日卷已备 N/60）、`GET /v2/stats/profile`（「我的 / 学习数据」页的全部图表数据）、`GET /v2/papers/{id}/timing`（成绩页：各题型实际用时与按分值分配的时间，超出 25% 且多于 1 分钟标为用时偏长）。不同满分的卷子一律按各自满分折算成百分比，分数模式只画与最近一套同规格的卷子；被反馈的题不计入。
- **每题用时**：前端只在该题在屏幕上、考试进行中、页面可见时计时，随草稿保存（`seconds`，只增不减），交卷时写入新表 `answer_timings`（迁移 033）。
- **错题本**：`GET /v2/mistakes?subject=&question_type=&chapter=&limit=&offset=`，返回失分的已判分题（反馈题除外）及各筛选项的数量；章节取每题第一条教材证据的章节。
- **短练习**（`POST /v2/practice`、`POST /v2/practice/{id}/answer`、`GET /v2/practice/{id}`，表 `practice_sets` / `practice_answers`）：只用已入库的客观题（单选、填空），按错过的题优先，其次没做过的；**不属于任何一张卷子，不计入每日成绩和成绩趋势**，但会更新章节正确率和连续天数。正在作答中的卷子里的题不会出现在练习里。
- **添加到主屏幕**：`/manifest.webmanifest`、`/static/icons/*`；iPhone/iPad 的 Safari 里首页会出现一次性提示（点「知道了」后不再出现，记在浏览器本地）：添加后需要重新登录一次。登录有效期 30 天。
- 字体 Noto Sans SC 来自 Google Fonts；加载失败时自动回退到系统字体，不影响使用。

## 出题进度页的统计口径

- 每格的「排队 / 出题中 / 审核中 / 被挡住」**只统计当前规格 `xiaohan-60q-300pt-v4` 的工作**：出题任务的 `max_points`、题目的 `max_points` 等于当前规格的分值（单选 4、填空 4、名词解释 6、简答 12、问答 18）才计入。旧分值（2/2/4/9）和 GPT 时期没有分值的任务不进各格，也不进合计。
- 旧工作中**被挡住的**合并成一行「历史记录（旧规格）N 条」，点开才显示按原因的明细。
- 每格显示「今日入库 N 道」（今天变为已入库的当前规格题目）；页面上方显示「今日审核通过率」（今天完成审核的当前规格题目里，结果为已入库的比例；今天没有审核时显示「今天还没有审核」，不显示 0%）。
- 目标数字旁注明「首批目标」（设置了 `AI_BANK_TARGET_PER_BUCKET`）或「两天储备」。
- 原因代码只放在可展开的「查看原因」「详情」里；行内只显示中文短句，没有对应中文的代码显示「原因未归类」。

## 任务列表怎么读（出题进度页「最近 20 个任务」）

- **任务名**写明种类和对象：`审核 · 生化填空 #482`（题目编号）、`审核 · 真题名词解释 #482`（真题入库的题）、`审核 · 旧候选复用 细胞单选 #123`（旧资料导入的候选）、`审核 · … #482（修复后）`（自动修复出的新版本）、`修复 · …`、`出题 · 生化填空（新题）`、`出题 · 真题问答答案（真题条目 #37）`（给真题写答案和评分要点）、`判分 · …`、`兼容性探测 · 模型 / 档位`。
- **为什么审核可以比出题多**：每个题目版本恰好一次独立审核（任务键 `quality:<题号>:<内容哈希>:review`，重复入队不会多出一条）。出题任务只对应「新题」和「真题答案」；下面三种题目**没有**出题任务却同样有一次审核：旧候选复用（120 条旧候选导入）、自动修复后的新版本（修复只做一次，新版本要重新审核）、以及启动前就已存在、还没审核的候选。
- **「完成」不等于通过**：审核任务完成后，行内显示题目的结果——「通过，已入库」或「未通过：<原因>」；出题任务完成但结果无法使用时显示「具体原因」（字段 + 规则，例如 `rubric：评分要点分值合计不等于本题分值`）。所有任务（不只是 QA 授权任务）都会保存这个安全的原因代码，不保存模型的原文。
- **排队任务**显示已尝试次数；重试中的显示下次重试时间；等待低峰的显示「等待低峰时段」。
- **领取顺序**（`ai_jobs.CLAIM_ORDER_SQL`）：兼容性探测 → 判分 → **审核与修复** → 出题和其他；同一类内最旧的先。即先把已出的题审完，再出新题。（原来除探测和判分外按创建顺序一视同仁，已排队的出题任务会排在新产生的审核前面。）

## 出题进度页

登录后打开 `/progress`（首页「出题进度」卡片和「我的」里都有入口），每 30 秒自动刷新，只读。数据来自 `GET /v2/model/progress`（需登录），只返回数字、代码和时间，不含题干、教材原文、提示词和密钥：

- 每个科目 × 题型：已入库 / 目标、排队中、出题中、审核中、被挡住（附原因代码统计）；目标 = `AI_BANK_TARGET_PER_BUCKET`，没设就是两天储备；
- 最近 20 个任务：种类、状态、原因、时间；
- 今日 / 本月花费、本月计入上限的金额、平均每入库一题多少钱、出题与审核剩余额度、判分预留剩余、本月每日花费；
- 当前高峰 / 低峰及下一个低峰开始时间；
- worker 上次运行时间和结果：每次运行写一行心跳到 `worker_heartbeats`（迁移 032，保留 14 天），超过 15 分钟没有心跳会标红；`worker-run` 日志照旧；
- 预计还要多久能凑够下一张完整卷子：按最近 48 小时的入库速度估算，没有足够记录时只给缺口。

## worker 状态日志与「不领任务」排查

每次运行 `python -m app.auto_worker` 结束时输出**一行** `worker-run …`（只含 ID、数量、代码，不含密钥和教材内容）：

| 字段 | 含义 |
|---|---|
| `enabled` | `yes`，或 `NO(value='…')`（`AI_AUTOMATION_ENABLED` 不是恰好 `1`，会显示实际值，如多了空格或引号） |
| `autoplan`、`bank_target` | `AI_QUEUE_AUTOPLAN` 是否为 1；`AI_BANK_TARGET_PER_BUCKET` |
| `stopped`、`max_concurrency`、`model`、`effort`、`auto_upgrade`、`users` | 数据库里 `ai_settings` 的当前值 |
| `price_refresh` | 本次是否抓取了官方价格（`ok` / `failed:错误类型`；12 小时内已核实则无此项） |
| `queue` | 各状态任务数，如 `pending=1,blocked=420` |
| `head` | 本次**会最先检查**的任务：`id:种类:状态:scope=…:err=…`。`scope` 非空表示它是旧的受限试验/审核活动/QA 授权任务 |
| `peak_held` | 因处于高峰而暂缓的任务种类 |
| `calls`、`claimed` | 本次实际调用次数、领到的任务 ID |
| `stopped_because`、`why_*` | 没有把调用次数用满时，最后一次领取失败的原因与相关 ID |
| `cost_cny` | 本次新增的人民币用量 |
| `month_counted_cny`、`cap_cny` | 本月计入上限的金额；上限/判分预留 |
| `error` | 异常类型（应用自己的固定报错信息会一并给出） |

`stopped_because` 取值：`no_claimable_job`（没有可领任务，含 `why_held_back`）、`max_concurrency_reached`、`trial_call_in_flight` / `trial_waiting_for_validation`（队首是旧的受限试验任务，**不会被标记为阻塞，也不会让位给后面的任务**）、`blocked:<原因>`（`automation_stopped`、`budget_exceeded`、`verified_cny_price_required`、`model_delisted_or_catalog_changed`、`legacy_currency_job_requires_requeue` 等）。

**会让 worker「静默退出、不领任务」的全部条件**（`auto_worker.main` → `ai_jobs.claim`），按对「任务一直 pending」的可能性排序：

1. **队首被旧任务卡住**：`claim` 每次只取排序最前的一个可领任务（先 `compatibility`，再 `grading`，其余按 ID）。若它是旧的受限试验/QA 授权任务，且同组有任务在跑或在等校验，`claim` 直接返回空而**不标记阻塞**，后面的任务永远轮不到。症状：任务一直 `pending`，日志无任何输出。`head` 的 `scope=` 非空即可确认。
2. **并发上限**：库里有 `state='running'` 且租约未过期的任务数 ≥ `max_concurrency`（默认 1），静默返回。
3. **`AI_AUTOMATION_ENABLED` 不是恰好 `1`**：整个 `main` 在第一行直接返回。
4. 低峰规则：只会暂缓出题/审核/修复，**不会**暂缓兼容性探测和判分。
5. 会把任务标记为阻塞（不是一直 pending）的：停止开关、预算/预留、价格未核实、模型目录变化、非人民币旧任务、月上限。
6. 会报错退出码 1 的：身份守卫、schema 不以 `source_staging_` 开头、密钥/授权缺失。

**旧预留是否计入月上限 90 元**：不计入。月上限（`cost_control.month_split`）和每日/每月预算（`cny_billing.totals`）都只统计**当月（北京时间）创建**的预留，10 月的 GPT 时期预留不在窗口内（有测试）。「累计预留」「未知待对账」是全期汇总，只对「首次试验累计预算」（`trial_active`）生效——该项必须保持**取消勾选**，否则全期累计 ¥37.25 会挡住任务。若当月（11 月 1 日 00:00 起）确有旧任务的预留，状态日志的 `month_counted_cny` 会显示；预留表不可修改，届时的排除办法是只统计新流水线任务，需另行批准。

## 数据库迁移

`backend/Dockerfile` 的 CMD 只启动 uvicorn，迁移由 Railway 配置执行：

- 正式站：Railway **preDeploy** 命令 `python migrate.py`。
- QA：在服务的 **startCommand** 里先跑 `python migrate.py`，再启动 uvicorn。

迁移文件在 `backend/sql/`，只追加、不修改已有文件。`/db/health` 会在迁移不完整时返回 503。

## 环境变量（新卷相关）

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 连接串 |
| `QUIZ_DAILY_SPECIFICATION` | 新卷规格，`xiaohan-60q-300pt-v4`（旧值 `xiaohan-60q-150pt-v2` 仍能启动，但 `/db/health` 会带 `specification_env_outdated: true`，且不会备新卷） |
| `DEEPSEEK_API_KEY` | DeepSeek 密钥 |
| `DEEPSEEK_API_AUTHORIZED` | `1` 才允许调用 DeepSeek |
| `DEEPSEEK_PRIVATE_DATA_AUTHORIZED` | `1` 才允许发送每题相关的教材段落 |
| `QUIZ_AUTO_GRADE_ON_SUBMIT` | `1`：交卷后主观题进入 AI 判分队列 |
| `AI_FULL_CORPUS_ENABLED` | **不设置或 `0`**。设为 `1` 会发送整套教材，超出用户授权 |

`deploy/railway-automation/Dockerfile` 内置的 `DEEPSEEK_*=0`、`AI_AUTOMATION_ENABLED=0` 是安全默认值，需要时由 Railway 服务变量覆盖。

## 首批试跑：临时题库目标量

`AI_BANK_TARGET_PER_BUCKET`（**临时**，试跑完成后删除）：设为正整数 N，则每个「科目 × 题型」补到 N 题（现有 + 流水线中 + 在途请求）；真题入库和旧资料复用也会在该格已够数时停止排队。设为 `5` 即 10 格（2 科 × 5 题型）× 5 = 50 题。不设置、为空、0、负数或非数字，一律按正常规则补到两天储备（`STARTUP_RESERVE_DAYS = 2`）。月上限 90 元、判分预留 15 元不受影响。

## 日期逻辑（项目至 2026-12-19）

2026 年节假日日历已核实（国务院通知），11 月 1 日至 12 月 19 日没有法定节假日；`tests/test_cost_control.py` 逐半小时核对这段时间内低峰判断（`cost_control.is_peak`）与计费模块（`cny_billing.period`）一致，且不会出现「未知」档。月/日统计窗口按北京时间切分，跨 12 月 1 日已测。CNY 价格每次随调用刷新 24 小时有效期，与日期无关。不需要 2027 年日历。

## 第 8 步：QA 配置清单（由用户在 Railway 后台操作）

QA：项目 `0ff7ff18-e746-42db-8eb2-b515306fa473`，环境 `qa`（`246e3b1c-811b-4e02-ba42-89cb3e19f2cb`），网站服务 `training-api-qa`（`b2cbe2fd-cc60-49ec-b52f-68f5fd35aa07`）。

**先看三个必须知道的点**

1. **新 worker 需要守卫放行其服务 ID（已改：QA 档案放行网站 `b2cbe2fd-…` 与 worker `9295cd65-7355-4804-aa06-f10a5327846c` 两个固定 ID）**：`deployment_target.verify_isolated_target()` 在 QA 档案下要求 `RAILWAY_SERVICE_ID` 等于钉死的 `b2cbe2fd-…`。新建的 worker 服务有自己的服务 ID，会报 `Railway target identity mismatch: SERVICE`。办法：允许 QA 档案的服务 ID 为 `{b2cbe2fd-…, <worker 服务 ID>}` 两个固定值（约 10 行 + 测试）。流程：先建 worker 服务、**先不要部署**，把它的服务 ID 发给我 → 我改代码、过 CI、合并 → 再部署。无需改代码的替代做法（不推荐）：在 `training-api-qa` 的启动命令里同时跑循环 `python -m app.auto_worker`，网站与 worker 互相影响且镜像里没有旧资料 JSON。
2. **网站和 worker 都要 `DEEPSEEK_API_KEY`**：排队兼容性探测、刷新官方目录在网站进程里检查密钥；真正调用模型的在 worker。
3. **网站要 `QUIZ_PUBLIC_ORIGIN`**：模型设置页的 POST 会校验 Origin，Railway 代理后网站看到的是 `http://`，不设会 403。

### a) `training-api-qa`

构建与启动（Settings）：
- Source：仓库 `Sakuraone0424/text`，分支 **`feat/60q-v2-spec`**（合并提交 `33ccbc3`；不再固定在 `d2b6a01`）。
- Builder：由 RAILPACK 改为 **Dockerfile**，路径 `backend/Dockerfile`（服务级设置，见「部署设置的来源」）。
- Start Command：保持 `python migrate.py && uvicorn app.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 5`（Dockerfile 的 CMD 只启 uvicorn，迁移靠这条）。**新增 `--timeout-graceful-shutdown 5`**：出题进度页用服务端推送（SSE）保持长连接，重启/部署时最多等 5 秒就结束这些连接；Railway 上的 Start Command 是手工设置的，需要你把这个参数加进去（事件流本身也限定了 8 秒的最长存活，浏览器会自动重连，所以不加也不会卡住部署，只是最多多等 8 秒）。
- Healthcheck Path：`/health`（服务级设置）。

变量：

| 动作 | 变量 | 值 |
|---|---|---|
| 新增 | `DEEPSEEK_API_KEY` | 密钥，用户自己填 |
| 新增 | `QUIZ_PUBLIC_ORIGIN` | `https://<training-api-qa 的公网域名>`（https，无路径、无结尾斜杠） |
| 新增 | `AI_MONTHLY_CAP_CNY` | `90` |
| 新增 | `AI_GRADING_RESERVE_CNY` | `15` |
| 改 | `QUIZ_DAILY_SPECIFICATION` | `xiaohan-60q-300pt-v4`（旧值 `xiaohan-60q-150pt-v2` 能启动但不备新卷；其他值 `/db/health` 返回 503） |
| 改 | `QUIZ_AUTO_GRADE_ON_SUBMIT` | `1` |
| 改 | `AI_AUTOMATION_ENABLED` | `1`（网站的判分排队也检查它，保持 1） |
| 改 | `DEEPSEEK_API_AUTHORIZED` / `DEEPSEEK_PRIVATE_DATA_AUTHORIZED` | `1` / `1` |
| 改 | `QUIZ_DAILY_PREP_ENABLED` | `0`（第 8–8.5 步期间不自动出卷；第 9 步前再改 `1`） |
| 核对 | `QUIZ_DEPLOYMENT_PROFILE` | `ustc-exam-ocr-qa` |
| 核对 | `QUIZ_ISOLATED_TARGET_APPROVED` | `1` |
| 核对 | `QUIZ_REQUIRE_RAILWAY_IDENTITY` | `1` |
| 核对 | `QUIZ_APPROVED_RAILWAY_PROJECT_ID` | `0ff7ff18-e746-42db-8eb2-b515306fa473` |
| 核对 | `QUIZ_APPROVED_RAILWAY_ENVIRONMENT_ID` | `246e3b1c-811b-4e02-ba42-89cb3e19f2cb` |
| 核对 | `QUIZ_APPROVED_RAILWAY_SERVICE_ID` | `b2cbe2fd-cc60-49ec-b52f-68f5fd35aa07` |
| 核对 | `QUIZ_DB_SCHEMA` | `source_staging_ocr_20261030`（该 schema 必须已存在） |
| 不动 | `APP_PASSWORD`、`SESSION_SECRET`、`QUIZ_API_KEY`、`QUIZ_USERNAME`、`DATABASE_URL`、`QUIZ_APPROVED_DB_HOST/NAME/PORT/SYSTEM_ID/USER` | |
| 删除 | `QA_IMPORT_ENABLED`、`QA_EXPECTED_PRIVATE_DB_HOST` | 读取它们的代码已在第 1.5 步删除 |
| 不要设置 | `AI_FULL_CORPUS_ENABLED`、`QUIZ_PHOTO_ANSWERS_ENABLED` | |

### b) 新 worker 服务

- 名称建议 `training-worker-qa`，同项目同环境（qa），源同上（`feat/60q-v2-spec`）。
- 设置在服务级配置里填写（不再使用配置文件；`worker.railway.json` 仅作参考）。对应内容：builder `DOCKERFILE`，`dockerfilePath` = `deploy/railway-automation/Dockerfile`，`startCommand` = `python -m app.auto_worker`，`cronSchedule` = `*/5 * * * *`，`restartPolicyType` = `NEVER`。无 `railway.json` 健康检查（cron 任务）。
- 该 Dockerfile 含 worker 需要的全部文件：`app/`、`sql/`、`harness/`、`config/`、两个旧资料 JSON、`migrate.py`。
- 变量：

| 变量 | 值 |
|---|---|
| `DATABASE_URL` | `${{training-api-qa.DATABASE_URL}}` |
| `QUIZ_DB_SCHEMA` | `${{training-api-qa.QUIZ_DB_SCHEMA}}` |
| `QUIZ_APPROVED_DB_HOST` / `_PORT` / `_NAME` / `_USER` / `_SYSTEM_ID` | `${{training-api-qa.QUIZ_APPROVED_DB_HOST}}` 等，逐个引用 |
| `QUIZ_APPROVED_RAILWAY_PROJECT_ID` / `_ENVIRONMENT_ID` | `${{training-api-qa.QUIZ_APPROVED_RAILWAY_PROJECT_ID}}`、`${{training-api-qa.QUIZ_APPROVED_RAILWAY_ENVIRONMENT_ID}}` |
| `QUIZ_APPROVED_RAILWAY_SERVICE_ID` | **填 worker 自己的服务 ID**（Settings 里复制；配合上面第 1 点的代码改动） |
| `QUIZ_DEPLOYMENT_PROFILE` | `ustc-exam-ocr-qa` |
| `QUIZ_ISOLATED_TARGET_APPROVED` | `1` |
| `QUIZ_DAILY_SPECIFICATION` | `${{training-api-qa.QUIZ_DAILY_SPECIFICATION}}` |
| `AI_AUTOMATION_ENABLED` | `1` |
| `DEEPSEEK_API_AUTHORIZED` / `DEEPSEEK_PRIVATE_DATA_AUTHORIZED` | `1` / `1` |
| `DEEPSEEK_API_KEY` | 密钥，用户自己填 |
| `AI_QUEUE_AUTOPLAN` | **先 `0`**（探测期间不自动规划出题），探测通过后改 `1` |
| `AI_CALLS_PER_RUN` | `2` |
| `AI_BANK_TARGET_PER_BUCKET` | `5`（见 d） |
| `AI_MONTHLY_CAP_CNY` / `AI_GRADING_RESERVE_CNY` | `90` / `15` |
| 不要设置 | `AI_FULL_CORPUS_ENABLED`、`QUIZ_PHOTO_ANSWERS_ENABLED`、`QUIZ_DAILY_PREP_ENABLED`（镜像默认 0） |

`RAILWAY_*` 由 Railway 自动注入，不必手填。

### c) 网站上的 AI 设置（`https://<QA 域名>/static/model.html`，用 `APP_PASSWORD` 登录）

1. 「模型」选 `deepseek-flash`，「思考强度」选 `high`（判分沿用此档；出题关 thinking、审核 low 由程序按任务类型决定），「自动发现」勾选，「自动升级」不勾 → 点「保存设置」。若模型列表为空，先点「刷新官方目录」。
2. 预算与开关 → 点「保存预算与停止开关」：勾选「启用单任务/每日/每月费用额度」；**单任务（含重试）预算**至少要大于一次调用的最坏估计（`(32000 × 峰值输入单价 + 输出上限 × 峰值输出单价) ÷ 100 万`），建议先填 `1`；**每日人民币预算** `15`；**每月人民币预算** `90`；**不要勾「首次试验累计预算」**（`trial-active` 取消勾选，否则累计试验额度会挡住任务）；单任务输出 Token 上限 `8192`，重试次数上限 `2`，最大并发 `1`；「停止AI任务」**取消勾选**（探测需要它为未停止）。
3. 价格核实：页面没有单独按钮。价格由 worker 在每次运行时自动从官方价格页抓取（已 12 小时内核实过则跳过），有效期 24 小时。确认方法：登录后打开 `/v2/model/billing`，应看到当前人民币价格版本和 `verified_at`。如需立刻手动触发：登录状态下在浏览器控制台执行 `fetch('/v2/model/prices/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}',credentials:'same-origin'}).then(r=>r.json()).then(console.log)`（手机上不方便时等 worker 下一次运行即可；是否补一个页面按钮由你决定）。
4. 兼容性探测：展开「首次兼容性检查」，「请求档位」依次选 `off`、`low`、`high`，每次点「排队兼容性探测」。探测只发送合成算术，不发教材；由 worker 在下一次 5 分钟运行里执行（所以 worker 必须已部署、价格已核实）。三档都通过后（页面「能力」处显示通过）再进入下一步。
5. 探测通过后，把 worker 的 `AI_QUEUE_AUTOPLAN` 改为 `1`。

### d) 首批试跑变量

`AI_BANK_TARGET_PER_BUCKET=5` 只加在 **worker 服务**（出题规划在 worker 里执行；网站不读它）。试跑完成后删除该变量。

### e) 顺序与验收

1. 先在 `training-api-qa` 改好 Source / Builder / 变量（a），**先不要触发部署**（Railway 会把变量改动暂存，点 Deploy 才生效）。
2. 创建 worker 服务（b），配置变量、**不部署**，把 worker 服务 ID 发给我；我改守卫、过 CI、合并，之后你再部署。
3. 先部署 `training-api-qa`：preDeploy 阶段无；启动命令里的 `python migrate.py` 先跑迁移，再启动 uvicorn。验收：
   - `GET /health` → `{"status":"healthy"}`；
   - `GET /db/health` → 200，`"status":"healthy"`，`"database":"connected"`，`migration_count` = 37，`formal_training` = `{"specification":"xiaohan-60q-300pt-v4","questions":60,"points":300,"duration_seconds":…}`；503 且信息为 `V2 schema migrations are incomplete` 表示迁移没跑完；503 且 `detail.code` 为 `not_ready_for_training` 表示业务还没就绪，`detail.missing` 列出缺的核心表或「科目 × 题型」当前题量（有 / 需要，需要 = 一张卷子的题数）——这只影响 `/db/health`，Railway 的健康检查用的 `/health` 不受影响；`Unknown QUIZ_DAILY_SPECIFICATION` 表示规格变量不对；
   - 登录后 `GET /v2/model/spend` 返回今日/本月、上限 90、预留 15。
4. 再部署 worker：日志出现正常结束（cron 任务每 5 分钟起一次、`restartPolicyType: NEVER`）；若报 `Railway target identity mismatch: SERVICE`，说明第 1 点的代码改动还没合并。
5. 做 c) 的设置与探测，再把 `AI_QUEUE_AUTOPLAN` 改 `1`，然后通知我做第 8.5 步。

## 正式站切换（第 10 步：搬家方案，仅方案，尚未执行）

> 状态：**只有方案，没有动手**。正式站没有改过任何东西。守卫改动、搬家脚本都等你「第 10 步」通知后才在单独分支（`prod-move/…`，不合并进 `feat/60q-v2-spec`）上实现。

### 1. 已核实的事实

- 正式站（项目 `xiao-ustc-quiz`，环境 `production`，服务 `quiz-backend` + `Postgres`）数据库实测（用户从 Railway 后台确认）：**没有 `schema_migrations`，没有 `papers_v2`**；只有 `answers、attempts、paper_questions、papers、questions、review_schedule、topics、users` 八张表（即 `001_initial.sql` 的结构）；`papers` 4 行（2026-09-26、10-27、10-28、10-30），`user_id` 都是 1。
- 服务变量只有 `APP_PASSWORD`、`DATABASE_URL`、`SESSION_SECRET`；preDeploy = `python migrate.py`；上一次部署可回滚（Railway 标记 `canRollback`）。
- 结论：**不能在原库的 `public` 里直接跑新迁移。** 本地模拟（用 `001_initial.sql` 建出与正式站相同的八张旧表）：
  - 当 `search_path` 里有旧表所在的 schema（即忘了设 `QUIZ_DB_SCHEMA`）时，`migrate.py` 会改动旧表（在旧 `questions` 上加列/索引，并在旧 schema 里建出 83 张表）——**这正是要防的事故**。
  - 当 `search_path` 只含新 schema（`db.get_db` 对 `QUIZ_DB_SCHEMA` 的做法：`SET LOCAL search_path TO <新 schema>`）时，旧 schema 的列、索引、触发器、行数**逐项不变**，新 schema 里建出 83 张表（含自己的 `schema_migrations`）。

### 2. 总体方案

在**同一个正式 Postgres**里建一个**全新的 schema**（暂定名 `prod_v2_20261101`，最终以执行当天日期为准），新系统只在该 schema 内读写；`public` 里的八张旧表原样保留、不碰、不删。数据来自 QA 库，用「先演练、后正式」的方式搬。

```
QA 库 schema source_staging_ocr_20261030
   │  pg_dump（结构全部 + 选定表的数据）
   ▼
本地/临时库演练：恢复 → migrate.py → 健康检查 → 抽样核对
   │  同一份转储文件
   ▼
正式 Postgres：新建空 schema → 恢复 → 校验 → 配置 QUIZ_DB_SCHEMA → 部署新代码
```

为什么用 `pg_dump --schema` 而不是逐表 INSERT：公开题目的触发器（028）、不可变表、外键顺序和 `schema_migrations` 的 sha256 校验都会随结构一起完整带过去；逐表 INSERT 容易被触发器拒绝或留下半套数据。恢复后 `migrate.py` 只会应用转储中没有的新迁移。

### 3. 搬哪些数据

| 搬 | 表 |
|---|---|
| 教材/资料原文及其审核记录 | `source_documents、source_units、source_pages、source_original_parts、source_versions、source_review_events、source_supplements、source_text_annotations、source_bibliography_reviews、formula_search_renderings、source_mapping_proposals` |
| 真题与知识关系 | `exam_records、exam_transcript_candidates、exam_scopes、exam_samples、exam_annotations、knowledge_links、knowledge_dependencies、past_exam_items、question_origins` |
| 已入库题目及其审核证据 | `reviewed_questions、question_audits、auto_question_states、auto_quality_events、auto_knowledge_events、candidate_review_packets、candidate_revision_links、candidate_human_decisions、candidate_science_assessments、legacy_question_publications、legacy_proposals、legacy_hints、legacy_page_calibration、legacy_reuse_state` |
| 用户与设置 | `users`（仅正式站那一个登录用户，见下）、`ai_settings`（复制后逐项核对金额、并发、`stopped`）、`ai_model_catalog`、`selection_policies` 及其 assignments |
| 出题/审核的任务记录 | `ai_jobs`、`ai_call_events`：**需要用户决定**（见第 7 节问题 2） |
| **学习数据（全部搬，见 3.1）** | `papers_v2、paper_items_v2、selection_runs、training_sessions、answer_drafts、answer_images、attempts_v2、answers_v2、answer_timings、grading_events、answer_reflections、reviews_v2、fsrs_cards、fsrs_events、question_reports（“这题有问题”反馈）、practice_sets、handwriting_*、notion_state、notion_sync_jobs、notion_mistake_pages、notion_sync_events、archive_consent、selection_policy_assignments` |
| **费用账本（搬，见 3.3）** | `ai_cny_events、ai_cny_adjustments、ai_cny_reservations、ai_cny_prices、ai_cny_price_resolutions、ai_budget_reservations、ai_settings_events`（本月部分） |

> 修订说明：本节早先版本把上面两行列为「不搬」，理由是 QA 上的测试作答会污染正式记录。这个前提不成立：你现在每天在 QA 上学习，这些就是真实学习数据，必须一起搬。污染的只有「测试垃圾」，见 3.2，按明确规则排除，而不是整类不搬。

#### 3.1 学习数据：全部搬，关联不变

- **ID 原样保留**：`users.id`、`papers_v2.id`、`attempts_v2.id`、`questions`（`reviewed_questions.id`）等一律不改号，所以题目 ID、试卷 ID、用户 ID 之间的外键天然一致，不需要映射表。正式 schema 里只放那一个学习者用户行，`id` 取 QA 里该学习者的 `id`，`username` 改为正式站登录用的用户名（执行前你告诉我；我只读 `username`）。
- **搬运方式**：结构和「题库/资料」仍用 `pg_dump --schema`（第 2 节）；学习数据不能靠 `pg_dump` 过滤行，也不能在新库里逐行 `DELETE`（`answers_v2`、`question_audits` 等有不可变触发器）。改为**按选定行复制**：搬家脚本（设计见 3.5）用 `COPY (SELECT … WHERE paper_id IN <保留集>) TO STDOUT` → `COPY … FROM STDIN`，按外键顺序（`users` → `papers_v2` → `paper_items_v2` → `selection_runs` → `training_sessions` → `answer_drafts / answer_images` → `attempts_v2` → `answers_v2 / answer_timings / grading_events / answer_reflections / reviews_v2` → `fsrs_cards → fsrs_events` → `question_reports` → `notion_*`）写入，**一个事务**；身份列用 `OVERRIDING SYSTEM VALUE` 保留原 ID，写完后 `setval` 把序列推到最大值之后。
- **复习卡片和错题本**：`fsrs_cards`、`fsrs_events` 按用户整表搬（它们只含 QA 学习者自己的复习状态）；错题本不是单独的表，而是「`answers_v2` 中得分低于满分的题 + 其 FSRS 卡片」，所以随这两部分一起到位；`notion_mistake_pages` 也搬，避免正式站重复建 Notion 页面。
- 「这题有问题」反馈（`question_reports`）、每题用时（`answer_timings` 与 `answer_drafts.value.seconds`）、判分记录（`grading_events`，只追加）、反思（`answer_reflections`）、手写转写（`handwriting_*`）、照片答案（`answer_images`）原样搬。`answer_images` 的图片字节在表里，随表一起走。
- 题目与卷子的引用必须在目标里存在：因为 `reviewed_questions` 整表搬（第 3 节首表），`paper_items_v2` 里保存的题目快照（`snapshot`，等于开卷时的完整题目内容）和 `reviewed_questions` 的外键都成立；保留集里任何一张卷子引用到目标没有的题目，则整个搬家中止。

#### 3.2 排除规则：只排除测试垃圾

排除的是下面三类，**其余一律搬**。规则按「创建时间 + 规格 ID + 用户」判断，不按表整体丢弃：

1. **GPT 时期与 v4 规格切换之前的测试作答**：卷子 `papers_v2` 满足下面**任一**条件即排除，它名下的 `paper_items_v2 / selection_runs / training_sessions / answer_* / attempts_v2 / answers_v2 / 判分 / 反思 / 用时 / 反馈 / archive 任务` 一并排除：
   - `blueprint->>'specification'` **不等于** `xiaohan-60q-300pt-v4`（GPT 时期的 28 题卷、`xiaohan-60q-150pt-v2`、各种 `synthetic-test-spec` 等）；
   - `papers_v2.created_at` **早于 v4 切换时刻** `T_v4`。`T_v4` 的取法：QA 上第一张 v4 规格卷子的 `created_at`；脚本把候选值列在清单里，由你确认或改成别的时刻，**不是脚本自己决定**。
2. **CI / 测试用户的全部数据**：`users.username` 不是正式学习者的用户（包括 `test-owner`、`ci-*`、`e2e-*`、`stranger`、`quiz_live_test_*` 夹具创建的用户等）。规则写成**白名单**：只保留你指定的那一个用户名，其余用户名及其全部数据都排除；脚本把「被排除的用户名及各自行数」列出来给你看，避免误排除。
3. **测试垃圾的衍生行**：仅属于被排除卷子/用户的 `fsrs_cards / fsrs_events`（卡片的 `user_id` 属于被排除用户）、`notion_sync_jobs`、`notion_mistake_pages`、`notion_sync_events`、`question_reports`、`archive_consent`。

**搬家前先输出清单，等你确认**（脚本的 `plan` 子命令，只读，不写任何库）。输出分两张表，每行有数量：

| 将搬 | 将排除 |
|---|---|
| 每张表保留的行数；卷子数、已交卷数、作答数；FSRS 卡片数及其中到期数；错题数；反馈数；`T_v4`；保留的用户名 | 每张表排除的行数；排除的卷子（逐张列出：id、日期、规格、创建时间、排除原因）；排除的用户名及其行数 |

清单同时给出「**没被任何规则命中、但也不在保留集**」的卷子数，必须为 0；不为 0 脚本拒绝继续（说明有规则没覆盖到的情形）。你回复「确认」后才执行下一步，确认的是一份带哈希的清单（脚本执行时重算清单，哈希不一致即中止），防止确认之后数据又变了。

#### 3.3 费用账本：本月已花的钱要搬，月上限连续

- 月上限是按本月 `ai_cny_events`（实际记账）、`ai_cny_adjustments`（人工校正）、`ai_cny_reservations / ai_budget_reservations`（预占）累计出来的，不是一个独立计数器（`cost_control` / `cny_billing`）。若不搬，正式站会把本月已花的钱当成 0 重新计算，上限被绕过。
- 所以搬：**本月（按中国时区自然月）全部**费用事件、校正、预占、价格表与价格决议（`ai_cny_prices / ai_cny_price_resolutions`，月费用口径依赖它们）、`ai_settings_events`（上限、`stopped` 的变更历史），以及当前的 `ai_settings` 行（上限、并发、`stopped`）。上月及更早的费用记录不搬，留在 QA。
- 搬完后用同一个函数在两边各算一次「本月已花 / 剩余」，**两边必须相等到分**；`stopped` 仍保持 `true` 直到你确认再放开。
- 注意：这与第 7 节旧的风险 4 相反。旧结论是「默认不复制 ai_cny_*，避免把 QA 费用算进正式站」；现在 QA 就是你实际在用的环境，QA 本月花的钱就是这个项目本月花的钱，应当计入。

#### 3.4 搬家后校验：两边逐表核对，不一致就回滚

脚本在目标 schema 的**同一个事务**里写完所有表之后、`COMMIT` 之前执行校验；任何一项不一致就 `ROLLBACK`，目标保持搬家前的样子（正式 schema 里没有任何学习数据），并输出不一致的表、键和两边的值。

1. **行数**：每张「搬」的表，目标行数 = 源端按保留集算出的行数（清单里的数字）。
2. **每张卷子**：`papers_v2.id` 一一对应；每卷的 `paper_items_v2` 条数、每个 `snapshot` 的内容哈希（`md5(snapshot::text)`）相同；已交卷的卷子，`attempts_v2.submitted_at`、`SUM(answers_v2.points)`（**总分**）、待判分题数相同；每道题的 `answers_v2.points / answer` 相同。
3. **每道错题**：每条 `fsrs_cards`，`question_id`、`due`（**下次复习日期**）、`stability / difficulty / state / reps / lapses` 与源端相同；`fsrs_events` 条数与每题最后一次事件相同。
4. **反馈与用时**：`question_reports` 的 `(paper_id, question_id)` 集合相同；每卷 `answer_timings` 的用时合计相同。
5. **费用**：本月已花合计、各来源（`ai_cny_events` 合计、校正合计、预占合计）两边相等到分；`ai_settings` 的上限、并发、`stopped` 相同。
6. **外键与孤儿**：目标里不存在指向被排除数据的外键（`paper_id / attempt_id / question_id / user_id` 都能在目标里找到）；序列值大于各表最大 ID。
7. **抽样回放**：对 3 张已交卷的卷子，用目标库重新调用 `result()`，与源端同函数的输出逐字段比较（总分、每题得分、解析）。

先在演练库（第 4 节第 3 步）完整做一遍并核对，通过后才在正式库做；正式库的写入同样走「一个事务 + 校验 + 回滚」。

#### 3.5 搬家脚本的设计（只有设计，没有实现，也没有运行）

`prod-move/move_learning_data.py`（在单独分支，不合并进 `feat/60q-v2-spec`）：

- `plan --keep-user <用户名> --cutover <T_v4>`：只读连 QA，打印 3.2 的清单和哈希。
- `apply --manifest <哈希>`：重算清单，哈希一致才继续；按 3.1 的顺序复制；3.4 全部通过才提交。
- `verify`：随时对两边重新做 3.4 的 1–7 项，只读。
- 目标 schema 名、源端 schema 名、是否 `--rehearsal` 都是必填参数，不读环境里的默认值，避免误写 `public` 或 QA；脚本拒绝目标是 `public`、拒绝源与目标是同一个库同一个 schema。
- 不打印任何答案正文、教材原文或密钥，日志只含表名、行数、ID 和哈希。

#### 3.6 旧正式站的 4 张卷子：方案 A，导出留档

旧正式站 `public` 里的 4 张卷子（2026-09-26、10-27、10-28、10-30，`user_id=1`）不导入新系统的历史（新旧表结构不同，答案没有题目快照），而是**导出留档**：搬家第 1 步的 `pg_dump -n public` 备份之外，再导出一份只读档案：`papers / paper_questions / questions / attempts / answers / review_schedule` 这几张卷子相关的行，各存成 CSV 加一份可读的 `旧正式站4张卷子.md`（每张卷子：日期、得分、每题题干/选项/你的答案/正确答案/解析），放在你的机器上，**不进仓库、不写日志**。`public` 里的八张旧表本身保持原样不动、不删除。导出后用 `COUNT(*)` 核对：4 张卷子、每卷 60 题、`answers` 行数与库里一致。

### 4. 步骤（每步都有检查点；先在演练库做一遍）

0. **前提**：第 8、8.5 步完成，QA 上的题库就是你愿意带上正式站的版本；在 QA 停止 worker（`AI_AUTOMATION_ENABLED=0` 或停服务），保证转储时数据不再变化。
1. **备份**：Railway 为正式 Postgres 做一次备份/快照；另外 `pg_dump -n public` 导出旧八张表到你手里的文件（4 张卷子的数据体量很小）。检查点：备份文件可读，`papers` 4 行。
2. **转储**：`pg_dump -n source_staging_ocr_20261030 --no-owner --no-privileges`，加 `--exclude-table-data` 排除 3 节里所有「学习数据」和「费用账本」表的行（这些表按 3.1/3.3 用搬家脚本按选定行复制，结构仍随转储建出）。教材原文在转储文件里，**文件只放在你的机器/受控位置，不进仓库、不写日志、不进 Docker 镜像**。
3. **演练**：新建临时库，建 schema，恢复，执行 `migrate.py`（`QUIZ_DB_SCHEMA` 指向演练 schema），然后核对：每张「随转储搬」的表行数 = QA 行数；学习数据与费用表此时为空，等 3.2 清单确认后由搬家脚本写入并按 3.4 核对；`/db/health` 为 200；`reviewed_questions` 中 `published` 且 `auto_question_states.status='ready'` 的数量按（科目 × 题型）与 QA 一致；抽 5 道题确认引用的教材段落能在 `source_units` 里按原文精确找到；`ai_settings` 的 `stopped` 为 `true`。演练失败则整个丢弃重来，不影响任何环境。
4. **正式站建 schema**：`CREATE SCHEMA prod_v2_…`（应用本身不允许创建 schema：`db.py` 要求 schema 必须事先存在），恢复转储到该 schema，行数核对同第 3 步。检查点：`public` 的八张表行数、列、索引与备份时一致（用第 1 步的清单逐项比）。
5. **改守卫的代码合入正式服务**（单独分支，见第 6 节）并部署到正式站之前，先把所有新变量配到服务上（旧代码会忽略它们）：`QUIZ_DB_SCHEMA`、`QUIZ_DEPLOYMENT_PROFILE`、`QUIZ_APPROVED_*`、`QUIZ_DAILY_SPECIFICATION`、DeepSeek 三项等（完整清单见第 8 节）。
6. **部署新代码**：preDeploy 的 `python migrate.py` 在 `QUIZ_DB_SCHEMA` 内只应用转储里没有的迁移（若转储来自最新 QA，则为空操作）。失败即不切换，旧版本继续服务。
7. **冒烟**：登录、打开今日页、查看 `/v2/model/spend`；此时 `ai_settings.stopped=true`，不会产生模型费用。
8. **启动 AI**：新建 worker 服务，`AI_BANK_TARGET_PER_BUCKET` 视库存需要设置，再把 `ai_settings.stopped` 置为 `false`；观察一整天的费用与进度。

### 5. 回滚

| 阶段 | 回滚方法 | 数据影响 |
|---|---|---|
| 第 1–4 步（尚未部署新代码） | `DROP SCHEMA prod_v2_… CASCADE`（只删新 schema） | 旧八张表从未改动 |
| 第 5–7 步（新代码已部署） | 在 Railway 回滚到上一次部署（`7cf3bb5a…`，旧代码），并删除/忽略 `QUIZ_DB_SCHEMA`；旧代码仍读 `public` 的八张表 | 新 schema 留着不动，可再次启用 |
| 已产生学习记录之后 | 不要 `DROP`；先导出新 schema（`pg_dump -n`）留档，再决定 | 新 schema 里的学习记录以导出文件为准 |

### 6. 两个阻断项的设计（单独分支实现，不合并，等你「第 10 步」通知）

当前代码的两个阻断：

1. `deployment_target.verify_isolated_target()` 看到正式项目 ID / 环境 ID / 项目名 `xiao-ustc-quiz` 就拒绝；
2. `db.get_db()` 和 `auto_worker.main()` 要求 `QUIZ_DB_SCHEMA` 匹配 `source_staging_*`，`deployment_target` 还要求它等于固定的 staging 名。

设计：**加一个明确的正式站档案，而不是删除保护。**

- 新档案 `QUIZ_DEPLOYMENT_PROFILE=xiao-ustc-prod`，放行条件**全部**满足才通过：
  - `RAILWAY_PROJECT_ID` = `c8ace6c2-e725-4e19-b064-3c71b44d4fde`，`RAILWAY_ENVIRONMENT_ID` = `54a1a3e3-9a24-4182-8441-5670d1ba9f04`，`RAILWAY_ENVIRONMENT_NAME` = `production`；
  - `RAILWAY_SERVICE_ID` 属于固定白名单（现有 `quiz-backend` = `1c4b68d9-…`，以及第 8 步之后新建 worker 的服务 ID，执行时填入）；
  - 对应的 `QUIZ_APPROVED_RAILWAY_*_ID` 与实际一致，`QUIZ_ISOLATED_TARGET_APPROVED=1`；
  - `QUIZ_DB_SCHEMA` 必须**恰好等于**代码里钉死的新 schema 名（常量，不是前缀匹配），且不是 `public`；
  - 数据库身份沿用现有 `QUIZ_APPROVED_DB_{HOST,PORT,NAME,USER,SYSTEM_ID}` 校验（私网地址、固定库名/角色/集群标识）。
- 其它任何项目、环境、服务、schema、未知档案**仍然拒绝**；正式项目在没有该档案时**仍然拒绝**（保护不变）。QA 档案和 `source_staging_*` 规则保持原样。
- 运行时再加一道断言（防止再出现上面「模拟 B」的事故）：正式档案下每次连库后确认 `search_path` 只含该新 schema，且 `to_regclass('public.papers')` 不在 `search_path` 内可见；迁移与应用都不可能再碰旧表。
- `auto_worker.main` 的 `source_staging_` 前缀检查改为调用同一个「该档案允许的 schema」函数，不再自己判断。
- 测试：参数化覆盖「正式档案 + 正确身份 → 通过」、「缺任何一项 / 项目或环境或服务 ID 不对 / 其它 schema / `public` / 无档案的正式项目 / QA 档案用于正式项目 → 拒绝」，并加一个把八张旧表放在另一个 schema 里的迁移回归测试（即本次模拟 A/B）。

### 7. 风险与需要你决定的问题

风险：

1. **误碰旧表**：只要 `QUIZ_DB_SCHEMA` 漏设，`migrate.py` 就会改旧表（模拟 B 已证实）。靠第 6 节的档案 + 运行时断言防住；执行前再做一次正式库「改前/改后」对比。
2. **转储里的教材原文**：是你的私有资料，转储文件不得入仓库/日志/镜像；演练库用完即删。
3. **QA 与正式的身份混淆**：`ai_settings`、`users` 复制后必须人工核对（尤其预算和 `stopped`）。
4. **月费用口径**：本月已花的费用必须搬（3.3），否则正式站的月上限会从 0 重新累计；搬完两边必须相等到分。
5. **题目的审核证据依赖任务记录**：`ready()` 需要「真实完成的审核调用且载荷可重算」，若不带 `ai_jobs/ai_call_events`，复制来的已入库题可能不被认作 ready（取决于重算方式，演练时会验证，不通过就改为带上这两张表）。
6. 搬家期间 QA 必须停写；转储之后在 QA 产生的新题不会自动同步。
7. 未知：正式 Postgres 角色是否有建 schema 的权限（演练前先用只读检查 `has_database_privilege`，不行则由你在 Railway 后台用管理角色建 schema）。

需要你决定：

1. **旧 4 张卷子是否导入新历史**（工作量评估，见第 9 节）。
2. `ai_jobs / ai_call_events` 是否随库带过去（建议：演练两种，选能让已入库题保持 ready 且不带计费记录的那种）。
3. 新 schema 的最终名称；旧 `users` 表那一行的用户名。

### 8. 环境变量与服务清单（搬家后）

服务：保留 `quiz-backend` + `Postgres`；新增 AI worker 服务（`deploy/railway-automation/Dockerfile` + `worker.railway.json`）。

`quiz-backend` 与 worker 共同：`DATABASE_URL`（私网地址）、`QUIZ_DEPLOYMENT_PROFILE=xiao-ustc-prod`、`QUIZ_ISOLATED_TARGET_APPROVED=1`、`QUIZ_DB_SCHEMA=<新 schema>`、`RAILWAY_*` 对应的 `QUIZ_APPROVED_RAILWAY_{PROJECT,ENVIRONMENT,SERVICE}_ID`、`QUIZ_APPROVED_DB_{HOST,PORT,NAME,USER,SYSTEM_ID}`、`QUIZ_PROTECTED_DB_SYSTEM_IDS`（不含正式站自己的集群标识）、`QUIZ_DAILY_SPECIFICATION=xiaohan-60q-300pt-v4`。
`quiz-backend` 另需：`QUIZ_AUTO_GRADE_ON_SUBMIT=1`、`DEEPSEEK_API_KEY`、`DEEPSEEK_API_AUTHORIZED=1`、`DEEPSEEK_PRIVATE_DATA_AUTHORIZED=1`；拍照作答保持关闭；`APP_PASSWORD`、`SESSION_SECRET` 不变。
worker 另需：`AI_AUTOMATION_ENABLED=1`、`AI_QUEUE_AUTOPLAN=1`、`AI_CALLS_PER_RUN`（默认 2）、DeepSeek 三项、可选的思考档位变量；**不设** `AI_FULL_CORPUS_ENABLED`；教材/真题原文文件随 worker 镜像（沿用 QA 做法）。

### 9. 旧 4 张卷子：评估（不动手）

旧结构是 `papers / paper_questions / questions / attempts / answers`（未经审核的旧题库）。新系统的历史只认 `papers_v2`，而 `papers_v2` 的每个条目必须指向 `reviewed_questions`（经证据审核的题）——旧题没有教材证据，**不能**硬塞进去，否则就是伪造「已审核」。可选方式：

| 方式 | 做法 | 工作量 | 说明 |
|---|---|---|---|
| A. 不导入，只留档 | 旧表原样留在 `public`；导出 4 张卷子（题面、你的作答、得分）为 Markdown/CSV 交给你 | 约 0.5 天 | 零风险；新界面看不到旧卷 |
| B. 作为「旧版记录」只读导入 | 新增一张迁移 `031`（`legacy_papers_archive`：日期、总分、每题得分摘要、原题面文本），历史页以「旧版」标签显示，不进入出题、选题、FSRS | 约 1–1.5 天（迁移 + 导入脚本 + 历史页展示 + 测试） | 不创建任何已审核题；只读 |
| C. 完整导入为 `papers_v2` | 需要把旧题伪造成已审核题 | 不建议 | 违反证据政策 |

建议 A，若你要在新界面看到成绩曲线再升级为 B。需要先知道旧 `papers`/`answers` 是否有可信的得分字段（请执行只读 `SELECT count(*) FROM questions; SELECT count(*) FROM answers; SELECT count(*) FROM paper_questions;` 给我行数）。

## 回滚

Railway 保留上一次部署，可在部署列表中一键回退；数据库变更只追加，旧 28 题记录只读保留。

## 界面规范与截图自检（PR B）

- 全站 `viewport-fit=cover`；顶部留 `env(safe-area-inset-top)`，底部导航高 56 + `env(safe-area-inset-bottom)`；样式里不使用 `backdrop-filter` / `filter: blur`（有测试检查）。
- `backend/tests/test_ui_screens.py` 用 iPhone 15 描述（DPR 3）分别截「Safari 模式」和「添加到主屏幕（standalone）」两种安全区，每页各截空数据与有数据两种状态。WebKit 在本地沙箱下载不到，会被跳过；CI 里三种浏览器都装了，截图作为 `ui-screenshots` 附件上传。
- 出题进度页：首批目标卡、科目切换的题库库存、本月花费、最近任务（状态标签；取消按钮在「详情」展开后）。

## 并行出题（PR C）

- 一次 worker 运行里最多同时发起 `max_concurrency` 个模型请求（模型页「并发上限」，1–4；新用户默认 2，**已有用户的值不会被迁移改动，需在模型页自己改成 2**）。总调用数仍受 `AI_CALLS_PER_RUN` 限制。
- **一次运行可以连续跑多批**（cron 仍是每 5 分钟启动一次）：一批（`AI_CALLS_PER_RUN` 次调用）结束后，如果距本次运行开始不到 3 分 30 秒（`AI_START_CUTOFF_SECONDS`，默认 210，范围 30–240），就立即规划并领取下一批；超过 3 分 30 秒不再发起新调用。已发出的调用最多等到 4 分 30 秒（`AI_RUN_SECONDS`，默认 270，范围 60–270）：到点仍没有结果的，费用按估算计入、任务重新排队，所以一次运行一定在下一次 cron 之前结束。低峰规则、预算、最大并发、按缺口优先、503 退避等规则不变。遇到队列空、高峰、额度用完、停止开关、一批里大多数调用返回 503/429、调用超时未回来时，直接结束本次运行；最多连续 8 批。
- **同一时间只有一个 worker 在跑**：运行开头用数据库 advisory lock（`pg_try_advisory_xact_lock`，随连接结束自动释放，崩溃/被杀也不会残留）；拿不到锁就立即退出，日志写 `skipped=already_running`，不记录运行心跳。
- 运行日志 `worker-run` 增加 `batches=N`（本次运行实际调用了几批）和 `exit_reason`：`queue_empty`、`peak_hours`、`budget_exhausted`、`automation_stopped`、`platform_busy`、`time_cutoff`、`overran`、`max_batches`、`shutdown`、`already_running`、`disabled`、`error`。
- 每次领取任务时先拿一把数据库全局锁：后一个领取一定看到前一个已写入的预留，所以日预算、本月上限、单任务上限、`max_concurrency` 在并行下仍然精确（有多连接竞速测试）。同一道题的出题/审核/修复不会同时被两个请求领取。
- 运行日志多了 `concurrency=`（本次开的并发数）、`parallel_peak=`（实际同时在跑的最大数）、`duration_s=`（整次运行秒数，含运行前后的准备工作）；`worker_heartbeats` 记录同样的并发数和用时，出题进度页的「后台服务」说明里能看到。
- 建议起步：`AI_CALLS_PER_RUN=4`、`max_concurrency=2`（约 2 轮，每轮 45 秒左右，一次运行 1.5–2 分钟）。观察几次 `duration_s` 都小于 150、没有 `overran=yes`，再把 `AI_CALLS_PER_RUN` 提到 6。并发不要高于 3：花费上限与限速都是按预留额度计，并发越高，临近额度时越早被挡住。

## 模型与费用页、统一登录

- 「模型与费用」页（`/static/model.html`，「我的」下的子页面）只有一个固定在底部的「保存」按钮，调用 `POST /v2/model/save`：模型、思考强度、自动发现、允许升级、三项费用上限和开关、累计试验上限、最大并发（1–4）、输出长度、重试次数在**同一个事务**里保存；保存后页面会核对服务器返回的值，不一致就显示出来。「停止 / 恢复 AI 任务」是危险操作区里的单独动作（`POST /v2/model/stopped`），都要二次确认。
- 最大并发保存后变回 2 的原因：旧页面有两个保存按钮，「保存设置」只提交模型和开关、**不提交最大并发**，只有「保存预算与停止开关」才提交；用户点了前者，刷新读到的自然还是旧值。服务器端 `/v2/model/limits` 一直能保存 1–4，迁移 034 只改新用户的默认值、不改已有用户。
- 登录：全站只有一个登录接口 `/auth/login` 和一个会话 cookie（`ustc_quiz_session`，path `/`，30 天）。子页面（模型与费用、出题进度）共用 `session.js`：先问 `/auth/me`，只有服务器返回 401 才显示登录卡，其他错误只显示错误提示、不会再要求登录。历史只读档案、学习建议、归档状态都是主应用里的视图，本来就共用同一个登录。`review.html` 属于另一个独立的人工审核服务（自己的审核账号），不属于这个学习应用，暂不合并，记入「以后清理」。
- iOS 的限制：Safari 与「添加到主屏幕」的网页应用各有各的 cookie 存储，在 Safari 里登录不会带到主屏幕应用里；主屏幕应用内部所有页面共用同一个登录。

## 减少浪费：引用匹配、自动修正、先修复一次、审核重问

**看数据**（不调用模型、不写库）：登录后打开 `/v2/model/waste/stats?hours=24`（最近 24 小时被挡/转修复的原因，按次数从高到低，附「现在怎么处理」）和 `/v2/model/waste/replay?hours=24`（把最近失败的出题草稿用现在的规则重跑一遍：能直接通过多少、转修复多少、仍被挡多少，以及各匹配层的引用与数据库原文对照）。出题进度页也有「近 24 小时被挡原因」卡片。在 Railway 里也可以运行 `python -m app.waste_report stats|replay --hours 24`。

**教材引用匹配**（`evidence_match.py`，出题、单选干扰项、真题答案、审核引用都用它）：只在这道题请求里实际发给模型的段落中找；先看模型写的那个段落，找不到再看请求里的其他段落；同一层若在不同位置匹配到不同的原文就算不通过；成功后存的一律是数据库里的原文，页码等仍由数据库填；每条引用匹配在第几层记在任务记录里（进度页详情可见）。
1. 原样；2. 规范化（去空白换行、全角半角、统一标点、上下标、行末断词连字符）；3. 只比较汉字字母数字；4. 省略号拼接（各段在同一段落内按顺序找到，每段至少 4 个字）；5. 跨相邻段落（请求里连续编号的两段，至少 8 个字，拆成两条引用分别存）；6. 严格模糊（相似度 ≥ 0.95、至少 8 个字，数字/英文术语/希腊字母/化学式必须一致；**另外**，有/无、增/减、升/降、抑/促、上/下 等会反转命题的字也必须一致；OCR 混淆表只在这一层用）。

**可以自动修正的格式问题**（`auto_fixes.py`，不改内容，任务记录里标注做了哪些）：填空标准答案补进可接受答案；单个空的各种写法统一成 `____`；单选选项键/前缀/答案写法统一成 A–D；数字字符串转整数；非填空题的可接受答案、非单选题的选项删除；可接受答案里的空字符串删除；评分要点里不在题目标签中的标签从评分要点里删掉（不并入题目标签）；difficulty 不在三档时映射，否则用 core；max_points 写错但评分要点合计正好等于规格分值时改回规格分值；模型输出外面包了代码块或说明文字时提取其中完整的 JSON（残缺的 JSON 仍然失败，直接重新出题）。

**先修复一次，再作废**：引用经 6 层仍对不上、评分要点分值不等于满分、单选重复选项或答案不在选项里、填空的空不是一个、名词解释/简答/问答缺评分要点或要点数量不对，不再直接作废，而是排一个「修复」任务（`kind='repair'`，payload 里有 `repair_of_job`）：带上草稿、具体错误和这道题用过的段落原文，只要求改错的地方，同一个出题模型、按修复计费、受同样预算约束。修复后的版本重新走全部检查和审核，并算作已修复过一次（审核没过不再第二次修复）；仍不合格才作废，原因写成「修复后仍……」。空选项、空白写法不可识别等修不了的问题仍直接作废。审核判「有歧义 / 有第二个正确选项」本来就是修复一次后重新审核，已有测试确认。

**审核答案格式不对**（字段缺失、理由太短、option_checks 不完整等）：不再把题目判为不通过，重新请求一次审核（算一次审核调用，任务键加 `:format_retry`）；第二次仍不对才判「审核失败」，题目标记 `review_format_failed`，既不修复也不作废。

## 页面外壳：文档不滚动，只有内容区滚动（iOS 26 顶部模糊）

- 所有页面的 viewport 是 `width=device-width, initial-scale=1`（不再用 `viewport-fit=cover`），所以系统把页面放在状态栏和 Home 指示条之间，网页里不再需要任何安全区留白。`apple-mobile-web-app-status-bar-style` 保持 `default`，`theme-color` 浅色 `#F4F6FA`、深色 `#0E1014`。
- `html`、`body` 固定高度（`100dvh`，键盘弹出时按可见区域高度）且 `overflow: hidden`；`body` 是三行网格：顶部 16px 空白 → 可滚动内容区（主应用的 `.workspace`、子页面的 `main.page`，`overflow-y: auto`）→ 底部栏（主应用的导航、模型页的保存栏）。之前加的状态栏固定色带已去掉。
- 每个页面记住自己的滚动位置；输入框获得焦点后滚到可见区域；答题页的固定底栏、弹窗、提示条都相对可见区域。
- 添加到主屏幕时 iOS 会读取状态栏样式、viewport 和图标：这次改了 viewport，**请删掉主屏幕上的旧图标，在 Safari 里重新「添加到主屏幕」**；只是刷新或关闭重开不会重新读取。
- 如果这样改完真机上标题仍然模糊，那是系统对状态栏区域的强制效果，网页无法关闭。

## 后端 PR A：审核能跑、费用按真实情况计、重启不卡、停止原因说清

**单选一道都进不了库的一个直接原因（已修）**：审核请求给审核员的教材是「每条引用一个视图」。单选题自己的依据加上每个错误选项的教材证明，很容易超过 4 条，而执行前的硬上限是一次请求最多 4 段教材，于是审核每次都在本机被拒（`ValueError:evidence_exceeds_hard_cap`），反复 retrying，题永远到不了审核员手里。现在超过 4 条时按段落合并成一段一个视图（4 条以内保持原样，已入库题的审核记录仍然匹配）。任务失败代码现在会写出我们自己的固定原因（`ValueError:原因`），自家的 ValueError 最多重试 1 次；输出被长度上限截断记为 `provider_output_truncated`（不再当成格式错误）；模型回答的大小上限改按 UTF-8 字节算（原来按 `\uXXXX` 转义算，中文一个字膨胀成 6 字节，约 2 万字就会超限）。

**费用口径**（`cny_billing.COUNTED`）：已结算的实际费用 + 正在运行（租约有效）的调用按最坏预留 + 已结束但拿不到用量的调用按「最近 50 次同类调用实际费用的中位数」估算（没有历史时退回它自己的预留）+ 对账后的修正值。预留按当时时段价格算（低峰按低峰价，日历未核实时才用高价）。`/v2/model/reconciliation` 显示构成（已结算 / 已对账 / 运行中 / 估算：笔数与金额）。

**对账**（`cost_reconcile.py`，表 `ai_balance_snapshots` / `ai_cny_adjustments` / `ai_cost_corrections`，都只增不改）：worker 每次运行检查——距上次读数满 1 小时，或北京时间新的一天里还没读过（0 点后第一次运行），就调用 `GET https://api.deepseek.com/user/balance`。两次读数之间余额的实际减少额，减去已知用量的调用后，就是那些「拿不到用量」的调用真正花的钱；按各自估算占比分摊（不超过各自的预留），多算的退回额度，每次修正记录时间、修正前后、差额。余额上升（充值或退款）只记读数、不修正；接口失败不影响出题，下次再对（日志里 `balance=failed:…`）。余额精度是 0.01 元，所以每小时的修正有约 ±0.01 元的取整误差。`POST /v2/model/reconcile` 可以手动对一次。

**租约与重启**：普通任务租约 180 秒（HTTP 超时 150 秒），要跑更久的 600 秒；过期后下一次领取自动收回并重新排队，预留不再按「运行中」计。每个 worker 进程有自己的运行标识，写在它领的租约上：每次运行开始先把「上一个进程留下、已超过 40 秒还在 running」的任务收回（部署重启后不用再等租约过期；日志 `reclaimed=N`）。收到 SIGTERM 后不再领新任务；已领但还没发出请求的任务立即退回（成本记 0）；已发出请求的最多等 30 秒拿结果，拿不到的按中位数估算并重新排队。

**额度用完的提示**：领取被挡时记录是哪一种额度（每日 / 每月 / 单任务 / 累计试验 / 月度总上限），worker-run 日志和进度页「停止原因」显示「今日额度已用完」等；每日额度用完时首页和进度页显示「今日 AI 额度已用完，明天 0 点（北京时间）自动继续」，预计时间从明天 0 点起算。费用卡片显示「实际已花」「预留中」「上次对账」。

**数据**（`/v2/model/waste/stats`）新增：每个「科目 × 题型」近 N 小时的出题次数、格式被挡、转修复、修复失败、审核通过/不通过、修复后入库、入库；审核不通过的问题类型分布（歧义、科学性、第二个正确选项、证据不支持、引用不符、题干泄露答案……），每类 2 个样例（题干前 60 字 + 审核理由前 160 字，不含教材）；单选逐步（出题 → 格式与证据检查 → 审核调用 → 审核结论 → 入库）；出题选段落过滤前后的段落数（太短、图表题注、表格、目录、章节标题、OCR 质量标记）。一次性原因（如 `superseded_scoring`）单独标注、在进度页折叠。

所有 JSON 回复的 `Content-Type` 带 `charset=utf-8`。

## 审核标准 PR（审核契约 10）

- 审核员拿到和出题时同样的**整段教材**（每个被引用的段落一份，最多 4 段）；题目的引用只是引用凭证，审核员的 claims 可以引这些段落里任何一处原文（按同样的 6 层规则匹配）。整段教材加真题风格材料放不进一次请求（24000 字节）时，自动退回旧的「只发被引用的句子」（契约 9）。
- 知识点映射由程序按引用生成（每个知识点指向本题引用的全部段落），审核员留空 `mapping_evidence`。
- 风险说明（`risk_resolutions`）只针对教材段落的 OCR 质量标记，真题风格材料不再要求。
- 单选题的审核用 `high` 思考强度（环境变量 `AI_REVIEW_THINKING_SINGLE_CHOICE` 可改，默认 `high`），其他题型仍是 `low`（`AI_REVIEW_THINKING`）。
- 查重：只和同科目同题型已发布的题比较，相似度阈值 0.9（原来是和所有已发布的题比、0.85）。
- 出题提示词：单选的每个错误选项都必须能在所给段落里找到可逐字引用的反驳依据，找不到就换掉这个选项。
- 兼容：旧契约（9 及更早）的审核记录按它们自己的契约重算与比对，已入库题的审核记录仍然有效。
- 前后对比：`/v2/model/waste/stats` 的 `review_by_contract` 按审核契约（9=旧、10=新）× 题型 × 思考强度给出近 72 小时的审核数、通过数、通过率和平均每次审核费用；单选在 high 下的通过率和平均费用就在这里和旧契约并排。

## One application shell (hash routes)

All learner pages are views of `/training`: `#/home`, `#/exam`, `#/mistakes`, `#/stats` (+ `/weekly`, `/insights`, `/history`, `/source-status`, `/archive`), `#/profile`, `#/progress` (出题进度), `#/model` (模型与费用). `/progress` and `/static/model.html` redirect into the shell. Moving between pages never reloads the document; the browser's back stack (and the iPhone edge swipe) restores the previous page with its scroll position; the subbar (back + centred title) is the same on every sub page. One login: an unauthenticated visit shows the login page and returns to the page asked for.

Speed: `Data.swr` shows the cached copy of a page at once and refreshes it behind (a visible retry card on network errors); the server keeps a 30 s `StaleCache` for the progress page (`?fresh=1` bypasses it, cancelling a job invalidates it) and memoises the OCR file integrity hash per `(document, sha)`. `GET /v2/perf` (learner key) returns p50/p95/max per route over the last 500 requests; requests over 300 ms are logged as `slow request`. Every response carries `Server-Timing: app;dur=…`.

Home-screen (standalone) mode: the bottom bar and the exam's fixed bar get a fixed 30 px bottom gap through `@media (display-mode: standalone)` (and `html.standalone` for iOS' `navigator.standalone`); `viewport-fit=cover` stays off and Safari is unchanged. The service worker cache is `v19-one-shell`; pages, scripts and styles of the shell are sent with `Cache-Control: no-cache` (revalidated on every load, ETag keeps it cheap).


## Notion 同步（训练记录 + 错题本）

服务器端用 Notion **内部集成**（Internal Integration）。两个环境变量（Railway → 服务 → Variables），密钥只放在这里，不会出现在页面、日志或接口返回里：

| 变量 | 说明 |
| --- | --- |
| `NOTION_TOKEN` | 内部集成密钥（`secret_…` 或 `ntn_…`） |
| `NOTION_PARENT_PAGE_ID` | 已分享给该集成的页面 ID（32 位字母数字，也可以直接填整条页面链接） |
| `PUBLIC_BASE_URL`（可选） | 本网站地址，用于「训练记录」里的成绩页链接 |

在 iPhone 的 Safari 上设置：

1. 打开 `notion.so/my-integrations` → 新集成 → 类型「内部」→ 选工作区 → 提交，点「显示」复制密钥。
2. 在 Notion 里新建一个空白页面（如「学习档案」）→ 右上角「…」→「连接」→ 选这个集成。
3. 同一页面「分享」→「复制链接」；链接最后那串 32 位字母数字就是页面 ID。
4. Railway 里加 `NOTION_TOKEN`、`NOTION_PARENT_PAGE_ID`（可选 `PUBLIC_BASE_URL`），保存并等重新部署。
5. 网站「我的 → 归档状态」：先点「测试连接」（只读，不写数据），通过后打开开关，再点「立即同步」。

行为：开关默认关闭，关闭时不向 Notion 发送任何数据。首次同步在父页面下创建「训练记录」（每套卷子一行：日期、两科得分/满分、总分、各题型得分率、用时、是否含复用题、成绩页链接）和「错题本」（每道失分题一行：科目、题型、章节、题干、我的答案、参考答案、得分/满分、评分要点逐条得分和理由、依据位置、错误次数、下次复习日期）；已有同名数据库就复用。交卷并判完分后由后台任务同步；失败自动重试 3 次（1 分钟、5 分钟、30 分钟后），第 3 次失败后等「立即同步」。同步是幂等的：试卷按试卷 ID、错题按题目 ID 找到原条目并更新，不会重复新建。不发送整段教材原文，只写「依据：教材第 X 章 · 第 Y 页」。

## 出题质量（审核契约 11）

- 真题题目的审核不再把自己的来源条目作为风格示例；审核员不用示例判重复，重复由程序和已发布题比较（同科目同题型，0.9）。因这个原因被挡的真题题目重新排队审核（不重新出题）。
- 评分要点只需「意思被所给段落支持」（可概括、改写）；引用仍逐字匹配。出题提示词：评分要点只能来自所给段落，被截断的段落末尾不写进评分要点；单空填空题只有一条评分要点（这个空的答案，满分）。
- 真题导入：由题目文本决定题型（A–D 选项 → 单选，多选标记 → 不支持；空格标记 → 填空），章节标题不再拼进上一题；已导入的条目在第一次规划时重新分类一次，前后数量在 `waste/stats.past_exam_route.reclassification` 和日志里。
- 教材检索：名词解释先在全部段落里逐字找这个名词（不受长度和 OCR 标记过滤），带前后相邻段落，最多 4 段；找不到则不调用模型（`term_not_in_textbook`）。只有严重 OCR 标记（`corrupt_ocr`、`untranscribed_image`、`image_dependent`、表格、`ocr_uncertain`、缺页）让段落不可用；`chemical_structure` 和 `scientific_notation_requires_source_review` 属轻微标记，段落保留，审核员不必解释。
- 规划：按缺口比例分配出题名额，单选不低于最近 20 次请求的 30%；真题名词解释仍最多占 20%（`PAST_TERM_MAX_SHARE`），通过率超过 30% 后设为 1 恢复。
- 部署后对比：`python -m app.waste_report compare --at <部署时间> --hours 6` 给出前后 6 小时各题型的审核通过率、入库数和每入库一题的费用。


## 登录与会话（安全加固）

- 登录失败按来源计数：同一来源 10 分钟内失败 10 次，锁定 15 分钟（返回 429 和中文提示「登录失败次数过多，已暂时锁定，请 15 分钟后再试。」，带 `Retry-After`）。来源取平台代理追加的最后一个 `X-Forwarded-For`。
- 退出登录会让当前这个会话失效（会话号写入 `revoked_sessions`）；修改 `APP_PASSWORD` 后所有旧会话立即失效（会话里带密码版本号）。部署本次更新后，旧格式的登录 Cookie 也会失效，需要重新登录一次。
- 密码、`X-API-Key`、`Authorization`、`X-Review-API-Key` 一律按 UTF-8 字节做常量时间比较，中文密码或非 ASCII 请求头得到 401/403，而不是 500。
