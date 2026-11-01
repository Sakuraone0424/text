# 部署说明

## 服务

| 服务 | 构建 | 启动 |
|---|---|---|
| 网站 API（正式站、QA） | `railway.json` → `backend/Dockerfile` | `uvicorn app.main:app`；Web 进程内的 `app.worker` 由 `QUIZ_WORKER_ENABLED`（默认 `1`）控制 |
| AI 任务 worker（第 8 步新开） | `deploy/railway-automation/worker.railway.json` → `deploy/railway-automation/Dockerfile` | `python -m app.auto_worker`，cron `*/5 * * * *` |

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

## 数据库迁移

`backend/Dockerfile` 的 CMD 只启动 uvicorn，迁移由 Railway 配置执行：

- 正式站：Railway **preDeploy** 命令 `python migrate.py`。
- QA：在服务的 **startCommand** 里先跑 `python migrate.py`，再启动 uvicorn。

迁移文件在 `backend/sql/`，只追加、不修改已有文件。`/db/health` 会在迁移不完整时返回 503。

## 环境变量（新卷相关）

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 连接串 |
| `QUIZ_DAILY_SPECIFICATION` | 新卷规格，`xiaohan-60q-150pt-v2` |
| `DEEPSEEK_API_KEY` | DeepSeek 密钥 |
| `DEEPSEEK_API_AUTHORIZED` | `1` 才允许调用 DeepSeek |
| `DEEPSEEK_PRIVATE_DATA_AUTHORIZED` | `1` 才允许发送每题相关的教材段落 |
| `QUIZ_AUTO_GRADE_ON_SUBMIT` | `1`：交卷后主观题进入 AI 判分队列 |
| `AI_FULL_CORPUS_ENABLED` | **不设置或 `0`**。设为 `1` 会发送整套教材，超出用户授权 |

`deploy/railway-automation/Dockerfile` 内置的 `DEEPSEEK_*=0`、`AI_AUTOMATION_ENABLED=0` 是安全默认值，需要时由 Railway 服务变量覆盖。

## 首批试跑：临时题库目标量

`AI_BANK_TARGET_PER_BUCKET`（**临时**，试跑完成后删除）：设为正整数 N，则每个「科目 × 题型」补到 N 题（现有 + 流水线中 + 在途请求）；真题入库和旧资料复用也会在该格已够数时停止排队。设为 `5` 即 8 格 × 5 = 40 题。不设置、为空、0、负数或非数字，一律按正常规则补到两天储备（`STARTUP_RESERVE_DAYS = 2`）。月上限 90 元、判分预留 15 元不受影响。

## 日期逻辑（项目至 2026-12-21）

2026 年节假日日历已核实（国务院通知），11 月 1 日至 12 月 21 日没有法定节假日；`tests/test_cost_control.py` 逐半小时核对这段时间内低峰判断（`cost_control.is_peak`）与计费模块（`cny_billing.period`）一致，且不会出现「未知」档。月/日统计窗口按北京时间切分，跨 12 月 1 日已测。CNY 价格每次随调用刷新 24 小时有效期，与日期无关。不需要 2027 年日历。

## 正式站切换（第 10 步，只读检查结果，2026-11-01）

> 本节只来自只读检查（Railway 只读接口 + 本地模拟），**没有改动正式站**。

### 正式站现状

- 项目 `xiao-ustc-quiz`，环境 `production`，服务 `quiz-backend` + `Postgres`（Postgres 18 镜像）。
- 构建：`backend/Dockerfile`，源仓库 `Sakuraone0424/text`；**preDeploy = `python migrate.py`**（已核实）。
- 服务变量只有三项：`APP_PASSWORD`、`DATABASE_URL`、`SESSION_SECRET`。没有任何 `QUIZ_*`、`AI_*`、`DEEPSEEK_*`。
- 最近一次部署是 2026-10-27 的「redeploy」（无提交信息，实际运行的提交无法从 Railway 看出）；9 月的部署来自 `main`（最新 `0888ad9`，`main` 现在只有迁移 001–002）。**正式站数据库已应用哪些迁移，必须查 `schema_migrations` 才能确定**（见下）。

### a. 旧代码/旧库能否安全执行迁移 022–030

结论：**可以，没有破坏性改动**，前提是先备份。

- 022–030 只有两处非「新增」：022 把 `paper_items_v2` 的 `position` 检查从 1–28 放宽为 1–60（旧行全部满足新约束，不改任何历史行）；028 替换触发器函数 `protect_reviewed_question()` 并把 `auto_question_states.policy_version` 默认值改为 `auto-evidence/2`（只影响新行）；030 给 `paper_items_v2` 加带默认值的 `is_reused` 列。其余都是新表。没有 DROP TABLE/COLUMN、DELETE、TRUNCATE、UPDATE 历史数据。
- `migrate.py` 在**一个事务**里按顺序应用所有未应用的迁移，已应用的文件校验 sha256（被改过会拒绝）。失败则整体回滚，不会留下半套结构。
- 本地模拟（临时 schema，不碰任何真实库）：从只有 001–002 的库（`main` 的结构）和从 001–021 的库（28 题分支结构）出发，一次应用全部 30 个迁移均成功（0.26 秒 / 0.04 秒），重复运行为空操作。
- 注意：从 001–002 直接到 030 会一次性建立 003–021 的全部表，包括 `reviewed_questions`、`papers_v2` 等。旧的 `questions/papers/attempts/answers` 表（001–002）保持不动，但新代码不再读它们。
- 022 在 `paper_items_v2` 上短暂持锁，数据量小，可忽略。
- 切换前请在正式库执行（只读）：`SELECT name FROM schema_migrations ORDER BY name;` 并把结果交给我核对；如无该表说明正式站从未跑过 `migrate.py`。

### b. 旧 28 题历史在新代码下能否显示

结论：**能**，针对「库里已有 `papers_v2` 的 28 题」。`tests/test_training_spec_v2.py::test_stored_28q_paper_stays_readable_after_v2_default` 验证：旧卷快照不可变，新规格成为默认后旧卷仍按 28 题 / 150 分读出；新卷不会退回 28 题（`test_daily_60q_scheduler.py`）。
- 但如果正式站仍是 `main` 时代（只有 001–002 的 `papers/questions` 表），那里的历史**不会**出现在新界面：新代码只读 `papers_v2` 系列。这部分是否存在、有多少条，需要查表确认（`SELECT count(*) FROM papers;`）。若需要保留，须另写一次性只读导入，**先问你**。

### c. 切换所需环境变量与服务

服务：保持现有 `quiz-backend` + `Postgres`；另外新建一个 AI worker 服务（`deploy/railway-automation/Dockerfile` + `worker.railway.json`），与 QA 相同。

`quiz-backend` 需新增：`QUIZ_DAILY_SPECIFICATION=xiaohan-60q-150pt-v2`、`QUIZ_AUTO_GRADE_ON_SUBMIT=1`、`DEEPSEEK_API_KEY`、`DEEPSEEK_API_AUTHORIZED=1`、`DEEPSEEK_PRIVATE_DATA_AUTHORIZED=1`；拍照作答保持关闭（不设 `QUIZ_PHOTO_ANSWERS_ENABLED`）。
worker 需要：`DATABASE_URL`、`AI_AUTOMATION_ENABLED=1`、`AI_QUEUE_AUTOPLAN=1`、`AI_CALLS_PER_RUN`（默认 2）、上面的 DeepSeek 三项、`QUIZ_DB_SCHEMA`，以及思考档位 `AI_GENERATION_THINKING` / `AI_REVIEW_THINKING` / `AI_GRADING_THINKING`（可选）。**不要设置** `AI_FULL_CORPUS_ENABLED`。教材/真题原文文件需随 worker 镜像（沿用 QA 的做法）。

### 风险点

1. **阻断：部署目标守卫会拒绝正式站。** `app/db.py` 每次连库都调用 `verify_isolated_target()`，在 Railway 上遇到 `RAILWAY_PROJECT_ID == c8ace6c2-…`（正式项目）或项目名 `xiao-ustc-quiz` 即抛 `PermissionError("Original project is protected")`。不改代码，新版本在正式站连不上库，连 preDeploy 的 `migrate.py` 也会失败（失败即不部署，旧版本继续运行，不会损坏数据）。需要你明确批准后修改这一守卫（第 11 步），并把 `PROTECTED_*`、`TARGET`、`SOURCE_SCHEMA` 改为正式站的固定值。
2. **阻断：`auto_worker.main` 要求 `QUIZ_DB_SCHEMA` 以 `source_staging_` 开头**，且 `deployment_target` 要求 `QUIZ_DB_SCHEMA` 等于固定的 staging 名称。正式库没有该 schema，需放宽（同第 11 步）。
3. 一次事务应用 28 个迁移，任何一步失败整体回滚，但**先做 Postgres 备份/快照**（Railway 的 Postgres 卷备份）再触发部署。
4. 迁移不可回退（只追加）；回滚代码后新增的表仍在，旧代码会忽略它们，但 022 放宽后的位置约束不会还原（无害）。
5. 正式站目前不知实际运行的提交；若它比 `main` 新，请以 `schema_migrations` 为准。
6. 触发方式：正式服务的源分支未在只读配置里显示；**合并到 `main` 可能自动触发正式站部署**。当前只合并到 `feat/60q-v2-spec`，不碰 `main`。
7. 正式站无任何 DeepSeek 变量：切换前不会产生任何模型费用；切换后首批用 `AI_BANK_TARGET_PER_BUCKET=5` 限量。
8. `APP_PASSWORD` / `SESSION_SECRET` 保持不变，已登录会话不受影响。

## 回滚

Railway 保留上一次部署，可在部署列表中一键回退；数据库变更只追加，旧 28 题记录只读保留。
