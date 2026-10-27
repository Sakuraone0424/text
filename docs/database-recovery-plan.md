# 私有备份交付与隔离恢复

状态：包可读且SHA一致为**已完成且独立验证**；用户取得/持久保存为**因权限或交付能力阻塞**；恢复脚本为**已准备但尚未执行**。

## 原包与实际交接能力

63,472,028字节；SHA256 `4e84fde3fdaaabded29005d9c05eeb4543b714d90673c55b3958dac84cbf081a`。本轮仅重新核对包SHA、位置/权限及manifest，不重复以前已完成的全库哈希扫描。文件在仓库外0600；包含dump、manifest、RESTORE.txt，18迁移、68表，登录会话数据明确不导出。

位置由 `/workspace/private-intake/first-batch-20261026/active-backup.json` 的index指向；实际包在snapshot-a5359e90696142d18b9d1beb2c3074fe。当前会话有入站附件download_file工具，但无出站upload/artifact工具；/mnt/data不存在，/workspace/shared和library-files未有官方持久导出约定。提供现有工作区文件链接供用户尝试，不声称该链接在iOS必然支持下载。**未收到真实保存回执，仍不算长期保全。** 不另造sandbox链接，不把复制到同机另一目录当异地备份。

若当前界面支持下载：Safari或ChatGPT文件界面保存到用户“文件”App，核对大小和SHA，记录实际保存回执。若不支持：须用户指定授权私有存储或认证HTTPS渠道，再说明认证、有效期、TLS、日志、不公开链接、保留期限与费用并执行；不自动上传Drive/对象存储。未确认附件上限，不能臆造限制，故没有制作无处交付的分卷。若确需分卷，应对每卷与合并包计算SHA，保留顺序和二进制合并方法；原包永不删除。

本ZIP仅是原Codex隔离source_staging_real_20261026的备份，**不是原Railway production全库备份，不能假设包含其旧成绩**。方案C切换之前仍需独立授权的生产备份、历史成绩校验与兼容迁移；当前绝不能停旧服务或覆盖500MB卷。

## 实测容量与兼容

本轮仅做只读容量/版本查询：原专用库约436,401,843字节；schema关系合计426,655,744字节；六份原字节合计7,541,935字节，最大单文件1,749,006字节；服务端PG17.11，仅plpgsql1.0。源表与索引已存在，不需要重新OCR或重导文件。

原Railway500MB卷已有旧成绩，不能恢复其上。即使空卷，源关系约427MB还不含目标PG基础文件、WAL、恢复峰值和增长。建议独立PG17新目标至少2GB起步，另预留私有包/展开dump约127MB位置；按实际`pg_restore --list`、WAL及云端磁盘指标核查，不能用压缩包63MB推断数据库只需63MB。当前没有云端容量读数或新库批准，不创建资源。恢复校验对大字节表按单行读取，其余批量游标，避免把整个原文集载入内存；临时验证进程仍需观测峰值，不能据此承诺固定小内存可运行。

## 已实现脚本

`backend/app/recovery_check.py`支持package、empty-target、restore、verify-restored。默认不连接、不创建库；restore必须同时有明确私有传输及写入授权。任何动作先校验传入的可信包SHA，源schema固定。示例`ARCHIVE`为仓库外现有包路径，`PG_BIN`为PG17客户端目录：

```sh
PYTHONPATH=backend .venv/bin/python -m app.recovery_check package --archive "$ARCHIVE" --sha256 4e84fde3fdaaabded29005d9c05eeb4543b714d90673c55b3958dac84cbf081a
# 以下命令仅在目标与授权实际配置后执行：
PYTHONPATH=backend .venv/bin/python -m app.recovery_check empty-target --archive "$ARCHIVE" --sha256 4e84fde3fdaaabded29005d9c05eeb4543b714d90673c55b3958dac84cbf081a
PYTHONPATH=backend .venv/bin/python -m app.recovery_check restore --archive "$ARCHIVE" --sha256 4e84fde3fdaaabded29005d9c05eeb4543b714d90673c55b3958dac84cbf081a --pg-bin "$PG_BIN"
PYTHONPATH=backend .venv/bin/python -m app.recovery_check verify-restored --archive "$ARCHIVE" --sha256 4e84fde3fdaaabded29005d9c05eeb4543b714d90673c55b3958dac84cbf081a
```

未在真实目标执行以上连接/恢复命令。测试用合成包、模拟restore子进程，以及真实测试库的rollback只读身份/非空拒绝检查；不是异库恢复演练通过。

## 授权与双重身份

- 独立项目/环境为交接表固定UUID；目标PG服务UUID仍未知，必须实际读取且与API服务不同。设置RECOVERY_TARGET_PROJECT_ID、RECOVERY_TARGET_ENVIRONMENT_ID、RECOVERY_TARGET_DATABASE_SERVICE_ID；RECOVERY_READ_AUTHORIZED只有读取授权后设1。
- RECOVERY_DATABASE_URL仅通过目标服务端Secret；独立核对后设置QUIZ_APPROVED_DB_HOST/PORT/NAME/USER/SYSTEM_ID。恢复工具先验证端点、库名、用户，拒绝hostaddr/service/options覆盖；连接后读取current_database、session_user和pg_control_system集群标识，再确认PG17。公网数据库端点必须sslmode=verify-full（并实际验证可信证书）；否则只能在Railway受控私网执行恢复，不能通过公开TCP代理明文发送dump/凭据。不是只看数据库同名“railway”。本地普通API/迁移连接也只允许明确loopback；本地CLI不能因没有Railway环境变量而直接使用远端生产URL，远端操作需经指定平台身份或独立恢复工具。原生产/原源库的已知集群标识可列入QUIZ_PROTECTED_DB_SYSTEM_IDS。
- 真恢复另需RECOVERY_WRITE_AUTHORIZED=1和RECOVERY_PRIVATE_TRANSFER_AUTHORIZED=1；未获批准保持未设置。不能因这些环境变量存在就绕过固定项目/环境、独立DB服务、端点、集群、空库和包校验。
- 空库检查覆盖全部非系统schema的表、分区、序列、视图、物化视图、外表及用户函数/类型/额外扩展，不只是source_staging为空；不符合就拒绝，不自动清理。
- 恢复用独立操作账号，应用用不同低权限账号。目标管理员可单独GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system()给应用身份，以读取集群标识；不授超级用户、CREATEDB、CREATEROLE或生产权限。应用仅CONNECT、schema USAGE、必要SELECT、业务表DML/序列权限；源原字节/迁移/不可变事件不授UPDATE/DELETE。实际权限清单须结合18迁移和API路径审核，再在目标HTTP写入测试验证；不能以GRANT ALL替代。

## 真恢复、读回与回滚验收

脚本先检查PG17客户端、独立空目标，再解出0600临时dump并核对内层SHA/大小；以参数数组执行pg_restore，明确`--dbname`、single-transaction、exit-on-error、no-owner/no-acl。凭据仅通过最小libpq环境传入，不出现在命令行；不继承PGOPTIONS/PGSERVICE，不执行clean/create/drop/truncate，stderr不公开输出正文。

成功退出后**新连接**逐表对比68表清单及UTC PostgreSQL17 to_jsonb行哈希、18迁移及仓库SHA、六文件身份和原字节SHA。应得12,396单元、1,915页标、38现行/45版本/7修订，不能只看pg_restore退出码。登录会话恢复后应为空。

随后还需授权的应用账号实际登录API、读取资料状态、保存预算0/AI暂停的配置、独立连接读回；重启API/数据库后重复读取，检查卷持久性。该真实写入/重启测试**尚未完成**，脚本返回api_write_restart_tested=false，不伪造学习成绩或提前生成28题。

失败时保留源库、原包、目标日志；单事务错误应回滚本次恢复，但仍须独立检查目标。读回失败不自动删schema或重试；只有重新获准的真正空目标才能再次恢复。不修改原production，不以清空旧成绩解决冲突。切换旧系统属于方案C的独立批准，当前不执行。
