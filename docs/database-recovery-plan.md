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

## 新版API运行状态的待解释项

用户报告新测试API部署dbd42628-159d-4c33-a03b-1ee333482277、SHA63042da、/health200，但没有独立测试PG。当前代码在QUIZ_REQUIRE_RAILWAY_IDENTITY=1且无合格DB时应返回503。因此200不能证明数据库就绪。云恢复前须只读核对该服务实际QUIZ_REQUIRE_RAILWAY_IDENTITY值及DATABASE_URL是否存在/脱敏目标身份（不读取或回传凭据），确保未借用原生产；不据此断言已连生产或关门禁。本轮没有云连接，无配置改动或重复Webhook检查。
