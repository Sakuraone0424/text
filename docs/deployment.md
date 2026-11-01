# 部署说明

## 服务

| 服务 | 构建 | 启动 |
|---|---|---|
| 网站 API（正式站、QA） | `railway.json` → `backend/Dockerfile` | `uvicorn app.main:app`；Web 进程内的 `app.worker` 由 `QUIZ_WORKER_ENABLED`（默认 `1`）控制 |
| AI 任务 worker（第 8 步新开） | `deploy/railway-automation/worker.railway.json` → `deploy/railway-automation/Dockerfile` | `python -m app.auto_worker`，cron `*/5 * * * *` |

AI 任务（出题、两轮审核、主观题判分）由 `auto_worker` 执行，经 `app.harness_gateway` → `app.harness_runner` 调用 `deepseek-harness-sdk`（`backend/requirements-harness.txt`）与 `backend/harness/restricted.patch.yml`。这两项不能删。

出题与真题入库的规划都在每次 worker 运行时进行，且**不依赖 `AI_FULL_CORPUS_ENABLED`**：每个请求只带一道题相关的教材段落（最多 4 段、5000 字），出新题按题型缺口、考频加权选段落。该开关只控制整库知识点提取，必须保持关闭。每次运行的模型调用次数由环境变量 `AI_CALLS_PER_RUN` 决定：默认 3，范围 1 到 10（超出按边界取值，无法解析按默认）。每次调用后立即派发结果，新产生的审核任务可在同一次运行里继续执行。预算、并发和重试上限对每一次调用照常生效。一道题约需 3 次调用（出题 + 两轮审核）。

`backend/data/` 下两个旧资料文件含教材摘录，只由 `app/legacy_reuse.py` 就地读取：只复制进 AI worker 镜像（`deploy/railway-automation/Dockerfile`），不进 API 镜像，内容不写日志。

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
