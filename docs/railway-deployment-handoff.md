# Railway执行交接（不含Secret；先只读，不自动部署）

状态：**已准备但尚未执行**。用户提供的外部只读核查不是本Codex独立查询。本地从48739e48继续；远端PR #1仍为cadf521、Draft。最终待同步目标为本轮最终消息记录的完整SHA；执行方在同一checkout运行`git rev-parse HEAD`解析，不能把本文件所在提交的父提交或旧cadf521当成新目标。代码未推送前，不修改Railway为不存在于GitHub的SHA。

## 固定资源边界

| 对象 | UUID/名称 | 本轮权限边界 |
|---|---|---|
| 禁止修改原项目 | c8ace6c2-e725-4e19-b064-3c71b44d4fde / xiao-ustc-quiz | 不改来源、Secret、卷、服务、数据库或部署；仅可读取计费用量所需元数据 |
| 禁止修改原环境 | 54a1a3e3-9a24-4182-8441-5670d1ba9f04 | quiz-backend、Postgres、用户报告500MB卷保持不变 |
| 唯一测试项目 | c6028313-e1f4-43cc-a63d-b7ff474a3a24 / amiable-vibrancy | 不重复创建项目 |
| 唯一测试环境 | ca9c95e7-81b8-495e-a51b-f01607a8eeab / production | production只是名称，不能改用同名原环境 |
| 唯一现有测试服务 | 15083462-a2f4-49d0-9a72-0873208e0669 / automation-staging | 不重复创建服务 |
| 用户提供的暂存Patch | a68e1281-f8a6-4c14-9570-0fabfbb1180c | 16项、STAGED、非破坏性；用户查询时无Deployment ID；此处尚未独立读取原diff |

无Secret输入见`deploy/railway-automation/observed-user-receipt.json`。未知字段为null，不能补成false或0冒充证据。

## 连接方立即执行的只读操作

1. 读取上述项目/环境/服务的**实际UUID**、服务Source、active配置、暂存Patch全部diff、Patch状态、最后部署、自动部署/PR预览开关、域名/卷。不要查询或回传Secret值，只回传变量名及三项AI开关是否为0；Patch diff先服务端脱敏。若工具只能返回全部变量明文，不调用该明文字段，缺项标记未知，不把凭据带入对话。枚举工作区所有服务及用量，不能只查测试项目。
2. 核对Patch仍是STAGED且无服务启动。若已改变，停止使用旧Patch ID，返回新ID、差异和部署ID；不能擅自撤销别人的变更。
3. 确认开发分支push与PR同步不会触发自动部署/预览。回执中的false必须表示当前已生效的监听关闭（同时核对active与staged配置和项目PR预览），不能只抄尚未提交的desired=false。未知时不推送。若需关闭测试服务的自动部署，先确认是这个服务；仅暂存关闭、检查是否触发部署，不调用deploy/apply。无法保证仅暂存的接口不要调用。
4. 当前GitHub Actions只有Backend acceptance，push/pull_request运行测试，无部署步骤；这不证明Railway独立GitHub监听关闭。`gh pr view 1 --json headRefOid,statusCheckRollup`核对远端。

本地验证回执（只验证所提供数据，不代表实时查询）：

```sh
PYTHONPATH=backend .venv/bin/python -m app.deployment_check --receipt deploy/railway-automation/observed-user-receipt.json --head "$(git rev-parse HEAD)" --phase push
```

当前预期退出2：自动部署/预览未确认关闭。连接方填入刚刚读取的非密钥回执再验。确认安全后Codex推送原分支、跑CI；连接方再将GitHub实际HEAD与验收SHA、暂存Source SHA逐一核对。

## 配置逐字段操作清单

下列16组是**核对目标**，不是伪造原Patch16项完整内容；补安全字段后变更数可以增加。所有新旧值须实际读取并保存无密钥diff。下面“暂存”不授权部署；若工具修改即会启动，先停止并请求批准。

| 字段组 | 当前已知旧值 | 目标/操作 | 暂存/触发/费用与验证 |
|---|---|---|---|
| 1 Source仓库 | 用户确认已暂存指定GitHub来源 | Sakuraone0424/text，核实仓库根目录`/` | 仅核对，绑定变更可能触发构建，未确认不写 |
| 2 分支 | feat/reviewed-dual-subject-28 | 保持；不改main/原production | 只改测试服务，确认autoDeploy关闭 |
| 3 Source提交 | cadf521c32b4f5b919655a303b18c95b89f23a31 | **安全push及新CI后**精确设置本轮验收SHA | 只暂存；部署前再次比较，不能提前声称新代码上线 |
| 4 Dockerfile | 存在但具体值未独立取得 | deploy/railway-automation/Dockerfile | 核对实际build context包含backend/；不使用根旧railway.json覆盖 |
| 5 RAILWAY_DOCKERFILE_PATH | 用户确认存在，值未知 | 与第4项一致；当前工具若使用原生build.dockerfilePath，以受支持字段为准，消除冲突前先读diff | 不猜新旧接口参数，不设置两个矛盾来源 |
| 6 构建上下文/依赖 | 未独立取得 | 仓库根；Python3.12、SDK0.2.1rc2、PG17客户端；optional CA不要求云端存在该Secret | 本地新镜像构建已完成，云端仍需验收 |
| 7 启动命令 | 未独立取得 | 优先不覆盖Docker CMD；CMD用sh展开PORT、uvicorn app.main:app、0.0.0.0 | 避免命令中原样传`${PORT}`却不展开；只暂存 |
| 8 健康检查 | 用户确认已配置，具体路径未取 | /health；新版需要身份+DB迁移就绪才200；DB未配应503 | 不能把503改为无条件200以通过部署；备选/db/health |
| 9 端口/监听 | 未独立取得 | 平台PORT、0.0.0.0；不是本机8091 | 实际HTTPS请求再验证，不先开新域名 |
| 10 运行用户/重启 | 未独立取得 | Docker USER postgres（非root），单副本，有限ON_FAILURE重试 | 不运行数据库daemon，不开启额外Worker；持续启动需费用批准 |
| 11 AI变量 | 用户报告禁用变量，三项实际值待读回 | AI_AUTOMATION_ENABLED=0、DEEPSEEK_API_AUTHORIZED=0、DEEPSEEK_PRIVATE_DATA_AUTHORIZED=0 | 读回均0，无付费试探 |
| 12 自动作业 | 未独立取得 | AI_QUEUE_AUTOPLAN=0、AI_FULL_CORPUS_ENABLED=0、QUIZ_DAILY_PREP_ENABLED=0、QUIZ_PRIVATE_BACKUP_ENABLED=0 | 先验收后分别启用；不创建独立Worker |
| 13 平台身份必须存在 | 新代码字段 | QUIZ_REQUIRE_RAILWAY_IDENTITY=1、QUIZ_AUTO_ONLY=1 | 缺身份fail-closed，不将staging字符串作为批准 |
| 14 UUID批准 | 新代码字段 | QUIZ_APPROVED_RAILWAY_PROJECT_ID / ENVIRONMENT_ID / SERVICE_ID分别为上表测试三ID；QUIZ_DB_SCHEMA=source_staging_real_20261026 | QUIZ_ISOLATED_TARGET_APPROVED保持0，直至实际资源/连接及授权检查完成才设1 |
| 15 数据库双重身份 | 目标未获批/未提供 | 优先新库私网Secret DATABASE_URL；公网仅允许实际可信证书的verify-full；独立核对后设置QUIZ_APPROVED_DB_HOST/PORT/NAME/USER/SYSTEM_ID，必要时QUIZ_PROTECTED_DB_SYSTEM_IDS | 不复制原production URL；此时不写Secret、不建库、不恢复；见恢复文档 |
| 16 认证/HTTPS/备份 | 未独立取得 | 用户在服务端Secret设置APP_PASSWORD、SESSION_SECRET、QUIZ_API_KEY等；QUIZ_PUBLIC_ORIGIN精确批准HTTPS origin；备份路径/卷在批准后配置 | 不回传值，不写Git；HTTPS、私有传输与存储另需明确授权 |

## 受支持配置接口与执行闸门

当前官方已弃用Config as Code，新服务不能新启用，旧绑定2026-12-01截止。**保留根旧railway.json，不修改原生产绑定。** 有权限的执行方先在仓库外私有目录对精确测试目标运行官方CLI`railway config pull`，将已有配置导入而非新建项目；再`railway config plan`核对diff。如果服务仍由旧JSON管理，按官方迁移流程核对该测试服务，不能全项目盲目apply。不要凭空调用旧接口或杜撰GraphQL字段。`config pull`不等于已读取Staged Patch，须单独核对。

```sh
PYTHONPATH=backend .venv/bin/python -m app.deployment_check --receipt /private/fresh-executor-receipt.json --head "$(git rev-parse HEAD)" --phase deploy
```

deploy预检要求新SHA一致、AI关闭、工作区预计用量≤额度且完整、付费启动/私有传输/恢复授权、空库与备份交接确认。它不创建资源、不apply、不上传、不启动，不是授权替代品。

真正部署只有费用、恢复与授权条件通过后由连接方执行，记录新Patch ID、精确SHA、Deployment ID、日志、域名及用量。失败时停留在未部署状态，保留旧Patch/read-only快照；不得回滚原production、删卷或清库。若新测试服务已启动，停止/回退也仅限用户批准的新服务操作；先记录日志与私有数据持久状态。

## 第一轮连接方回执补充

用户转交的外部只读结果：上述Patch仍STAGED、16项、destructive=false；测试部署数0；源码仍固定cadf521；Dockerfile与/health匹配；9项AI/执行控制变量及RAILWAY_DOCKERFILE_PATH只能读取名称，值隐藏，故ai_flags仍为null。原两服务SUCCESS、无暂存修改、500MB卷。两个项目Webhook列表为空**不能证明GitHub Automatic Deployments或PR预览关闭**。

当前连接工具不提供上述开关或完整账单字段。下一步由用户在控制台查看测试服务实际生效的Automatic Deployments与项目PR预览开关（包含任何暂存覆盖），回传对应UUID、页面截图/观测时间；并取得整个My Projects当前账期Usage及预测。不要提交Patch或启用服务来探测。只确认关闭后，更新无密钥回执再运行push预检；费用与恢复等门禁继续独立阻断。
