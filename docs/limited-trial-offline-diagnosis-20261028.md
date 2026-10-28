# 限定试验第5次调用：离线诊断与暂停状态

> 历史离线诊断/方案记录，不构成当前付费调用、参数变更或解除停止门禁的授权。当前闭环状态见 [验收报告](closure-acceptance-20261028.md)。

本轮无新增真实推理调用。代码修复仅在本地独立分支，未修改main、部署或启用Worker。

## 可复现的失败点

任务5、attempt1是题39科学审核。现存SDK会话 `job-5-attempt-1` 与数据库payload生成的用户消息精确相等，payload SHA仍为冻结值 `e900b409ce54c9df97e056237220f6e1c05e1280cc961b65c9d9ff1880c44b70`。

SDK接收记录的返回模型为deepseek-flash；assistant消息仅有reasoning块，最终text为空。实际SDK `finish_reason(events)` 返回 `max-tokens`，`final_response(events)` 返回空字符串。供应商usage与数据库调用事件一致：input2435、output4096、cache read/create均0。没有单独的reasoning_tokens计数，不能把流分块数当作Token数；可以确认4096输出上限耗尽且没有最终答案。

原执行器首先检查finish_reason，抛出 `incomplete_model_response`，在JSON解析及Finding Schema/科学判断/引文/评分校验之前结束。随后ProviderFailure(200)由正常提交的finish事务记录为blocked；预算系统保留预留。HTTP200不是有效审核成功，数据库事务未丢失attempt、usage或费用事件。

原始HTTP SSE、供应商请求ID及完整wire request/response SHA没有单独持久化。SDK message UUID不能冒充供应商请求ID。现存SDK会话SHA为 `fa00746264e554e37fc0365654cd2daee598d7d55e793079d1934424cf34ef8d`，只作为会话证据哈希，不冒称原HTTP响应哈希。原始会话及完整reasoning仅保留在私有证据包，不进入公开代码或报告。离线重放旧判定与修复后的纯解析器均得到同一失败，不能复用为完整科学审核。

## 费用和预算

该调用发生于北京时间14:23:55—14:24:19，已核验价格版本和2026日历均对应高峰价。离线估算为 `(2435×2 + 4096×8)/1000000 = ¥0.037638`。此前4次兼容估算¥0.004390，五次usage合计估算¥0.042028。这些不是供应商实际扣费证明。

没有可靠的逐请求供应商账单凭据，账户余额差也不能单独证明某次实扣。本轮未以猜测的接口查询或余额差解除阻断。原ai_cny_events unknown保持原样，不填0、不改写不可变账本；5份预留合计¥0.483840，每份¥0.096768保持冻结。stopped=true，job5 blocked，其余5个审核槽位pending且attempt0，正式ready总数0。

## 安全修复

- 原relay在保持原请求材料门禁后，将实际发送的JSON字节和原始SSE落到每job私有目录，文件0600、独占创建、flush/fsync；不存认证头。响应中断也保留已经收到的字节及usage，不覆盖旧证据。
- 提取模型、响应ID、stop reason和请求/响应SHA等元数据；不把reasoning或回答正文写入任务诊断字段。
- 区分SDK异常、输出未完成、空答案、JSON错误及非对象响应。错误通过网关持久化到任务result的安全诊断字段，保持blocked、unknown及无重试；不设置成功real_api记录。
- 科学/答案Schema、引用、评分、ready及原预算上限均未放宽。没有修改scope、payload、摘录、模型、思考级别或4096输出上限，也没有修改第5次历史任务的结论。

针对性回归112 passed、0失败/跳过、1条既有Starlette弃用警告。覆盖原limited_trial、SDK本地协议、人民币计费及任务门禁，并新增reasoning-only耗尽、部分JSON、空答案、缺usage、文件不覆盖、异常流中断和失败诊断持久化测试。真实会话只在仓库外离线重放，未写入或伪造正式调用记录。

## 备份与后续授权

调用后快照在另一个本地空库实际恢复，72表摘要、20迁移、6来源SHA、5次attempt、¥0.483840预留及ready0均通过。已新增Private备份分支 `backup-trial-20261028-five-calls-41bd1df2`，提交 `30844ed4712748b3b72b860cbf57d0b5bf94f8b3`；独立远端clone核对ZIP CRC、大小、外层和dump/会话SHA通过。旧备份和main保留。

主ZIP 65198023字节，SHA256 `41bd1df20e55eb18e3d37defbc9d4e0442eec072a4365ffa8bc63d57c583de17`。另有私有SDK会话证据包，上传前检查已知凭据值及令牌模式，未打包凭据/配置文件或认证头。新快照保留真实消费，不能用旧0调用备份重置预算。

原最多10次推理已用5次，仅剩5个槽位，但还缺6份有效审核：题39科学轮不可恢复，其余5轮未执行。因此原额度无法完成全部3题两轮。当前只授权离线工作，继续任何真实调用均需新的具体指示；若仍要求完成全部6轮，至少需明确批准失败轮的新attempt及总次数调整，并重新核验累计预算。现有不可释放的每次预留¥0.096768不变时，累计11次需要¥1.064448预留，超过原¥1门禁；不能只提高次数上限。原scope也禁止同槽位重复绑定，新的重试必须有明确的新scope/策略授权，不能删除旧attempt绕过。重跑同样High/4096可能再次耗尽，任何输出上限、思考级别或冻结材料变更也需单独批准。未知费用和预算阻断必须先核对，不能自动恢复。
