# 私人备份与隔离恢复：当前有效流程

最新真实验收见[recovery-validation-20261027.md](recovery-validation-20261027.md)及[data-foundation-audit-20261027.md](data-foundation-audit-20261027.md)。**本地独立恢复已真实通过；Railway恢复未获授权、未执行。** 此决定取代此前“等待手机/Google Drive保存”“尚未异库恢复”的旧描述。

## 唯一已验证备份与保留策略

用户已接受长期位置为私人GitHub仓库Sakuraone0424/ustc-private-backup-transfer，分支backup-transfer-20261027-4e84fde3，提交12793ae231a4bce77f9ed0bfcaaf8885660e167a；原ZIP作为Git文件经明确授权上传，远端独立新clone验证通过。没有公开分享或Git LFS。用户知悉删分支不保证历史副本立即清除。

文件USTC-private-review-backup.zip，63,472,028字节，SHA-256 `4e84fde3fdaaabded29005d9c05eeb4543b714d90673c55b3958dac84cbf081a`。内含database.dump、database-manifest.json、RESTORE.txt；18迁移、68表，认证会话数据不导出。本地原件仍位于仓库外snapshot-a5359e90696142d18b9d1beb2c3074fe，0600，永不以恢复成功为由自动删除。无需再转存Drive或iPhone。

这只是Codex隔离source_staging_real_20261026备份，不是Railway原生产全库备份。实际users、attempts、attempts_v2及FSRS为0，不能假设含原生产旧成绩；方案C仍需单独的生产备份和历史成绩迁移验证。本轮不改原生产。

## 本地实际恢复步骤与接续

已在新0700目录initdb PG17.11，--auth-local=peer、--auth-host=scram-sha-256、UTF8，localhost端口55437，不同于原5432集群。通过system identifier确认独立；新空DB由管理员单独创建，整库对象检查通过。

```sh
# 使用已核验的包和独立新目标；不要把此命令指向原库
PGHOST="$PRIVATE_NEW_SOCKET" PGPORT=55437 PGUSER=agent   "$PG_BIN/pg_restore" --dbname ustc_restore_verified_20261027   --single-transaction --exit-on-error --no-owner --no-acl "$VERIFIED_DUMP"
```

没有--clean、--create、DROP或对源库的写入。恢复后新连接逐表核对Manifest的行数/排序JSONB哈希、迁移文件SHA、六来源原字节SHA；进一步重新解析原文件、核对1915页标和12396索引，检查80外键、39序列和索引。新集群实际重启后再次68表校验通过。83项针对性测试在此恢复DB连接上执行，夹具独立rollback schema。只读应用角色HTTP健康/正文/候选读取实际通过，写入被拒绝；独立probe schema提交/重启读回不是正式学习数据。

接续指针`/workspace/private-intake/backup-user-handoff/local-rehearsal-target.json`；实际PG目录`/workspace/private-intake/ustc-restore-rehearsal-a84gic2k/pgdata`。凭据配置仅0600私有文件，不进Git/报告/新备份。临时环境可能回收，可由私人仓库恢复；本地库/原包均保留。

## 云端恢复前置：尚未执行

固定Project c6028313-e1f4-43cc-a63d-b7ff474a3a24，Environment ca9c95e7-81b8-495e-a51b-f01607a8eeab（名字production），现有API15083462-a2f4-49d0-9a72-0873208e0669。目前没有独立PG服务或Worker；未来PG服务UUID必须实际创建后取得，不能拿API UUID或随意值绕过校验。原项目c8ace6c2-e725-4e19-b064-3c71b44d4fde/环境54a1a3e3-9a24-4182-8441-5670d1ba9f04明确禁止写入。

1. 先取得整个Workspace资源用量并批准一个独立PG17及建议2GB卷、有限验证窗口。不可使用500MB旧卷；实测数据/WAL见容量报告。不得以本地恢复授权替代新云资源批准。
2. 获准后确认新PG UUID、版本、数据库/用户、空库（全部非系统schema）、实际容量及集群标识；与原生产集群和本地源集群区分。还未创建时这些值保持未知，不能伪造。
3. 私有备份传输和一次真实云恢复另需批准。Secret配置使用安全服务端渠道，不把URL/密码/令牌交给对话、Git或日志；不复制旧production URL。走受控私网，或有可信证书的公网verify-full，不能公开明文TCP传dump。
4. 单独迁移/恢复操作角色负责建结构；应用角色NOSUPERUSER/NOCREATEDB/NOCREATEROLE，仅CONNECT、schema USAGE、资料SELECT及明确列出的业务表DML/序列权限；来源字节/迁移/不可变事件禁止UPDATE/DELETE。集群身份读取仅授必需的pg_control_system函数EXECUTE，不授超级用户。不用GRANT ALL替代权限清单。
5. 调用现有app.recovery_check的empty-target、restore、verify-restored；保持固定UUID、端点/库名/用户和PG集群双重校验。RECOVERY_READ_AUTHORIZED、RECOVERY_WRITE_AUTHORIZED、RECOVERY_PRIVATE_TRANSFER_AUTHORIZED仅按实际授权开启。此CLI仍是云端保护工具，不伪造Railway身份来跑本地恢复。
6. pg_restore使用单事务、exit-on-error、no-owner/no-acl，不使用clean/create。恢复后全68表/18迁移/六SHA/页标/候选45与38现行独立读回；再最小权限API登录读写、重启持久性和费用验收。没有实际云端执行就不能报告云恢复通过。
7. 失败保留新目标、日志、源包，不自动删表/schema/库，不覆盖原库。只有确认空目标且重新获准后才能重试；任何资源清理、容量增加、生产切换单独审批。

## 最新外部回执与PG17/2GB执行边界

用户提供的Railway连接方回执：原生产部署未变；测试项目只有API，无PG/卷/Worker，变量名列表未发现DATABASE_URL。/health200仅证明当前API存活，不能证明接库。本轮没有Railway查询或修改，不重复部署触发检查。

恢复容量按本地真实测量：数据约448MB、集群目录约992MB含约520MB WAL；2GB是起步容量，不是峰值保证。仅使用PG17、UTF8；备份来自17系列，扩展、排序规则、磁盘余量须在新目标实际核对。卷使用率70%预警、80%暂停新增批处理并人工评估；不自动扩容。恢复前记录实际PG服务UUID、卷UUID、集群system identifier和私网地址，任何未知身份均阻断。

### 最小角色与连接配置

管理员只在批准的新目标创建空数据库及以下角色，不复用原生产账号：

|角色|用途与权限|收尾|
|---|---|---|
|ustc_restore_operator|LOGIN、NOSUPERUSER/NOCREATEDB/NOCREATEROLE；仅新库CONNECT/CREATE，身份核查所需pg_control_system EXECUTE；no-owner恢复产生的对象归此角色|验收后NOLOGIN、撤销库CREATE，核对无遗留连接；不删除对象所有者|
|ustc_read_app|LOGIN、NOSUPERUSER/NOCREATEDB/NOCREATEROLE；库CONNECT、资料schema USAGE、68表SELECT、身份函数EXECUTE；default_transaction_read_only=on|初期仅做检索验收，0源表写权限，不是正式答题写账号|
|未来业务写角色|必须按题卷、答卷、成绩、任务、账本等业务逐表/序列列白名单，来源字节及历史事件不授UPDATE/DELETE|另行实施/验收，不用GRANT ALL或给Agent任意SQL|

本轮在本地用事务内临时授权验证最小身份读取，随后rollback；已有只读应用角色68表可读、0表可写。密码仅经可信Secret渠道配置，不写本文、源码、普通日志或备份。禁用不必要PUBLIC权限须限新库且先验证扩展依赖。

|作用域|配置要求|
|---|---|
|API|DATABASE_URL=新私网PG的只读账号；QUIZ_DB_SCHEMA=source_staging_real_20261026；QUIZ_REQUIRE_RAILWAY_IDENTITY=1|
|双重身份|QUIZ_ISOLATED_TARGET_APPROVED=1仅获准后设置；QUIZ_APPROVED_RAILWAY_PROJECT_ID/ENVIRONMENT_ID/SERVICE_ID分别为上列测试项目、环境、API UUID；QUIZ_APPROVED_DB_HOST/PORT/NAME/USER/SYSTEM_ID全部来自新目标实际值|
|恢复工具|RECOVERY_TARGET_PROJECT_ID/ENVIRONMENT_ID为固定测试身份；RECOVERY_TARGET_DATABASE_SERVICE_ID必须是未来新PG UUID，不是API UUID；RECOVERY_DATABASE_URL使用操作角色，数据库批准USER随角色相应核对|
|授权|RECOVERY_READ_AUTHORIZED、RECOVERY_WRITE_AUTHORIZED、RECOVERY_PRIVATE_TRANSFER_AUTHORIZED按实际新授权设置；不能以本轮本地授权放行云端|
|保持关闭|AI_AUTOMATION_ENABLED=0、DEEPSEEK_API_AUTHORIZED=0、DEEPSEEK_PRIVATE_DATA_AUTHORIZED=0；不启Worker或每日后台任务|

### 获准后的顺序及验收

1. 取得Workspace账期Usage、有限运行窗口费用批准、新PG/卷创建及私有备份传输/恢复批准。记录目标四项UUID（项目、环境、PG服务、卷），明确排除原项目和500MB卷。
2. 经管理员创建独立空库及角色，核对PG17、编码、扩展、全用户schema空白、集群身份、私网和容量。用批准身份运行现有empty-target保护检查；未通过不能restore。
3. 确认私网内一次性执行渠道及PG17客户端实际可用，再传输已核验原包。私人GitHub下载如需凭据，只使用另行安全配置的该仓库只读Secret，不复用ChatGPT/Codex登录凭据。没有执行渠道就停在准备状态，不额外建常驻Worker。
4. 本地/目标双验外层SHA及内部dump/manifest；执行既有restore单事务、exit-on-error、no-owner/no-acl；禁止--clean/--create/DROP。不覆写任何有数据的目标。
5. 独立新连接执行verify-restored：68表逐表行数及排序JSONB摘要、18迁移文件SHA、六源文件SHA、1915页标、12396单元、38现行/45版本、修订关联、80外键、39序列及索引。比较完整Manifest而非仅看pg_restore退出码。
6. 只读API核验真实检索、认证拒绝、身份不符fail-closed；重启新PG后再读回。未来答题写入另用业务账号及独立探针，不能把探针当真实成绩。ready仍0；公式/图像索引缺口详见本轮数据报告，恢复成功不等于科学内容齐全。
7. 验收操作角色关闭登录，保存脱敏回执及新库备份；用户后续真实学习前还须完成业务权限、科学审核与付费API授权。未实际云运行不报告恢复通过。

### 回滚与验证资源删除（均未执行）

恢复失败：保留原包、新目标和脱敏错误日志，隔离新库，不自动清空/重试；再次恢复需重新确认空目标并批准新恢复目标。API如已接新库，退回此前无DATABASE_URL配置只限测试API；开启身份门禁后预期503，不能为恢复健康200关闭保护。旧production完全不动。

验证窗口结束后，删除资源须再批准**具体新PG服务UUID和新卷UUID**：先确认没有新学习数据或已有独立可恢复备份、解绑测试API的新连接、停止仅该验证实例；核对依赖后删除获准的新服务/卷，再读取资源清单和计费停止情况。不得删除整个项目/环境、现有API、原生产PG或原备份；停止计算后保留的卷仍可能计费。没有删除回执不能报告资源已释放。
