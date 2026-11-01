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

## 回滚

Railway 保留上一次部署，可在部署列表中一键回退；数据库变更只追加，旧 28 题记录只读保留。
