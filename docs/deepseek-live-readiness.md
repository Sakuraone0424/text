# DeepSeek真实验收准备

状态：执行层代码/本地协议**已完成但尚待云端验证**；真实API、私有资料外发、38题ready库为**因授权阻塞**。

继续DeepSeek Harness官方Python SDK 0.2.1rc2，2026-10-27本轮重读PyPI版本未变化。Python负责库/证据/规则/预算/FSRS。未增加OpenAI Agents API依赖，不读取或使用ChatGPT Cookie/Plus会话，也不将Plus额度当服务器API。源码未发现api.openai.com/OPENAI_API_KEY调用路径。

本轮重新读取官方中文价格页并成功解析；SHA仍为556097caba8adfeeed6455714bffd731c7f60db520fcee06a922eba5840663a3，Flash适用DeepSeek-V4.1-Flash-1017，Pro适用DeepSeek-V4-Pro-0813。只是公开文档观察，**没有GET /models真实认证请求，不能据此声称实际可用模型列表**。本轮未往真实库刷新价格记录；实际调用前必须获取当前有效CNY观察，过期/未知/冲突拒绝。

模型选择/model及任务路由保留。Off明确thinking.disabled，Low/High/Max按官方Harness Messages output_config.effort映射；四档真实SDK对本地模拟服务协议测试通过，但真实API可能拒绝某档，必须逐档合成探测后才能开放。没有默默降级或转其他付费提供商。模型/强度只影响新任务。

AI_AUTOMATION_ENABLED、DEEPSEEK_API_AUTHORIZED、DEEPSEEK_PRIVATE_DATA_AUTHORIZED默认0；AI_QUEUE_AUTOPLAN、AI_FULL_CORPUS_ENABLED默认0。单任务/首次试验/北京时间日月预算、并发/重试/输出限制、紧急停止、幂等与租约保留。默认预算0、停止；建议数额不是授权。未知费用保留预留，实际usage与估算金额、官方账单独立；Railway币种账本另算。

Harness零Agent工具，Python预取证据；额外Session Log/遥测关闭，relay只允许一次受控官方推理请求，拒绝Search/Fetch等付费工具。检索和学生答案是外部数据，不执行其指令；模型不获得Shell、任意SQL或生产凭据。

## 有授权后的最小执行顺序

1. 可信服务端Secret配置官方Key，明确合成测试人民币上限，再GET实际目录、实际Harness逐档探测、核对usage/错误/预算拦截/无额外上传。当前这一步尚未执行。
2. 单独批准最多3道候选的必要题干答案rubric、教材及转录风格摘录；独立科学与答案轮、最多2轮修订，记录费用和真实来源。不能沿用旧静态AI分析冒充新审核。
3. 小样本通过后另批38题，仍不批全库/真实作答；每科配额ready充足才组卷。全库12,396单元累计可覆盖全部资料，须独立范围和预算批准。
4. 真实答卷发给模型须另批，逐项rubric校验与有限复评；不能确定的保持待定。正确率评估需可信参考，不把模型自评当科学准确率。

当前仍candidate38，其余状态0，真实调用0、ready0、真实试卷/批改/FSRS0。已有合成题28/150/60、科学审核/修订、评分、FSRS、预算和断线计时回归全部保留。文字主流程可优先上线；视觉转录未验证，不猜笔迹评分。ChatGPT原生应用未注册/验证，不声称推送已完成。
