# Railway执行交接（不含Secret；先只读，不自动部署）

状态：**最新测试API已由用户部署63042da，独立数据库尚未创建**。用户提供Deployment dbd42628-159d-4c33-a03b-1ee333482277 SUCCESS、/health200。原生产保持原部署。没有本Codex云端操作；旧“运行cadf521、等待SHA同步”仅为过去状态，后续不重复同步同一SHA。原Webhook/自动部署/推送后安全检查已闭环，不重复索要。备份已在授权私人GitHub完成独立读回，本地PG17真实恢复及83项针对性测试已完成，详见恢复报告。

## 固定资源边界

| 对象 | UUID/名称 | 本轮权限边界 |
|---|---|---|
| 禁止修改原项目 | c8ace6c2-e725-4e19-b064-3c71b44d4fde / xiao-ustc-quiz | 不改来源、Secret、卷、服务、数据库或部署；仅可读取计费用量所需元数据 |
| 禁止修改原环境 | 54a1a3e3-9a24-4182-8441-5670d1ba9f04 | quiz-backend、Postgres、用户报告500MB卷保持不变 |
| 唯一测试项目 | c6028313-e1f4-43cc-a63d-b7ff474a3a24 / amiable-vibrancy | 不重复创建项目 |
| 唯一测试环境 | ca9c95e7-81b8-495e-a51b-f01607a8eeab / production | production只是名称，不能改用同名原环境 |
| 唯一现有测试服务 | 15083462-a2f4-49d0-9a72-0873208e0669 / automation-staging | 不重复创建服务 |
| 测试当前部署 | dbd42628-159d-4c33-a03b-1ee333482277 | 用户报告SUCCESS、63042da、/health200；独立PG/Worker不存在 |
| 当前Patch | null | 原16项Patch已由用户手动提交，不再暂存，禁止复用旧Patch ID |

无Secret输入见`deploy/railway-automation/current-user-receipt.json`（observed-user-receipt.json仅保留为旧测试夹具/历史快照）。未知字段为null，不能补成false或0冒充证据。

## 下一步：独立数据库，不重复部署安全检查

63042da已经部署，无需再创建仅更新同一Source SHA的变更。现有Patch状态只以连接方实际最新读取为准，不复用旧a68e1281。当前优先取得工作区Usage和数据库恢复授权，流程见database-recovery-plan.md。

本地恢复验收已经完成，不能冒充Railway恢复。现有/health200与“无独立PG”的组合尚不能证明就绪，须核对实际身份门禁值及DATABASE_URL是否存在/脱敏目标归属；不回传凭据、不修改配置。原生产quiz-backend部署7cf3bb5a-207d-4a32-befc-917401d7738b及Postgres e6d15d87-bd61-4ec6-abaf-2e5c2f85b9e8保持不变。

## 配置逐字段操作清单

下列16组是**核对目标**，不是伪造原Patch16项完整内容；补安全字段后变更数可以增加。所有新旧值须实际读取并保存无密钥diff。下面“暂存”不授权部署；若工具修改即会启动，先停止并请求批准。

| 字段组 | 当前已知旧值 | 目标/操作 | 暂存/触发/费用与验证 |
|---|---|---|---|
| 1 Source仓库 | 现有旧部署已绑定指定GitHub来源 | Sakuraone0424/text，核实仓库根目录`/` | 仅核对，绑定变更可能触发构建，未确认不写 |
| 2 分支 | feat/reviewed-dual-subject-28 | 保持；不改main/原production | 只改测试服务，确认autoDeploy关闭 |
| 3 Source提交 | 用户报告现部署63042da7e31819237971b4289689c949af0d55b7 | 本轮保持，不重复同步 | 文档变更不作为业务代码已部署的证明 |
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

## 状态纠正

此前“0部署、16项STAGED”已被最新外部只读回执推翻，不作为当前事实。原16项由用户手动部署提交，旧代码SUCCESS不等于新版部署或真实数据库/DeepSeek验收完成。源码同步需新Patch，当前无待提交Patch。
