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
| `training-api-qa`（网站） | `backend/Dockerfile` | 启动命令 `python migrate.py && uvicorn app.main:app --host 0.0.0.0 --port $PORT`；健康检查 `/health` |
| `training-worker-qa` | `deploy/railway-automation/Dockerfile` | 启动 `python -m app.auto_worker`；cron `*/5 * * * *`；重启策略 `NEVER` |

`deploy/railway-automation/worker.railway.json` 只作为上述设置的参考，不会被任何服务读取。正式站（`xiao-ustc-quiz`）运行 `main`，不受此变更影响；第 10 步切换时需在其服务级设置里同样写明。

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

## 日期逻辑（项目至 2026-12-21）

2026 年节假日日历已核实（国务院通知），11 月 1 日至 12 月 21 日没有法定节假日；`tests/test_cost_control.py` 逐半小时核对这段时间内低峰判断（`cost_control.is_peak`）与计费模块（`cny_billing.period`）一致，且不会出现「未知」档。月/日统计窗口按北京时间切分，跨 12 月 1 日已测。CNY 价格每次随调用刷新 24 小时有效期，与日期无关。不需要 2027 年日历。

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
- Start Command：保持 `python migrate.py && uvicorn app.main:app --host 0.0.0.0 --port $PORT`（Dockerfile 的 CMD 只启 uvicorn，迁移靠这条）。
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
   - `GET /db/health` → 200，`"status":"healthy"`，`"database":"connected"`，`migration_count` = 31，`formal_training` = `{"specification":"xiaohan-60q-300pt-v4","questions":60,"points":300,"duration_seconds":…}`；503 且信息为 `V2 schema migrations are incomplete` 表示迁移没跑完，`Unknown QUIZ_DAILY_SPECIFICATION` 表示规格变量不对；
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

**不搬**（QA 上的测试作答与计费，搬了会污染正式学习记录和月费用）：`papers_v2、paper_items_v2、attempts_v2、answers_v2、reviews_v2、training_sessions、answer_drafts、answer_images、handwriting_*、fsrs_*、grading_events、answer_reflections、selection_runs、question_reports、ai_budget_reservations、ai_cny_*、ai_settings_events、ai_review_campaign*、qa_automation_*、archive_*、automation_jobs、reviewer_*`。实现上用 `pg_dump --exclude-table-data=…`：这些表的结构仍会建出（外键保持完整），只是空表。

用户行：正式站登录靠 `APP_PASSWORD` + `SESSION_SECRET`，会话令牌里是用户名；新 schema 里只放一个用户行，用户名与现有登录一致（执行前请你告诉我旧 `users` 表里那一行的用户名，我只读 `username`，不读别的），`id` 不要求等于旧表的 1。不复制 QA 的其它用户行。

### 4. 步骤（每步都有检查点；先在演练库做一遍）

0. **前提**：第 8、8.5 步完成，QA 上的题库就是你愿意带上正式站的版本；在 QA 停止 worker（`AI_AUTOMATION_ENABLED=0` 或停服务），保证转储时数据不再变化。
1. **备份**：Railway 为正式 Postgres 做一次备份/快照；另外 `pg_dump -n public` 导出旧八张表到你手里的文件（4 张卷子的数据体量很小）。检查点：备份文件可读，`papers` 4 行。
2. **转储**：`pg_dump -n source_staging_ocr_20261030 --no-owner --no-privileges` + 上表的 `--exclude-table-data`。教材原文在转储文件里，**文件只放在你的机器/受控位置，不进仓库、不写日志、不进 Docker 镜像**。
3. **演练**：新建临时库，建 schema，恢复，执行 `migrate.py`（`QUIZ_DB_SCHEMA` 指向演练 schema），然后核对：每张「搬」的表行数 = QA 行数；「不搬」的表行数 = 0；`/db/health` 为 200；`reviewed_questions` 中 `published` 且 `auto_question_states.status='ready'` 的数量按（科目 × 题型）与 QA 一致；抽 5 道题确认引用的教材段落能在 `source_units` 里按原文精确找到；`ai_settings` 的 `stopped` 为 `true`。演练失败则整个丢弃重来，不影响任何环境。
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
4. **月费用口径**：若复制 `ai_cny_*`，QA 的 11 月花费会算进正式站的 90 元上限，所以默认不复制（见上表）。
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
