# 官方Responses严格JSON兼容探测：HTTP400，保持17题ready

后续授权批次已真实通过Responses兼容验证并生成首套28题正式卷，见[首卷就绪记录](first28-ready-20261028.md)。本页保留job109原始失败事实。

本轮用户明确批准在新scope确认历史job5、88未知费用并保留全部预留；先以无私有资料验证官方结构化接口，成功后继续剩余11题。原¥1、10次及自定义日/月额度没有恢复。

## 实际执行

工作分支延续`feat/reviewed-dual-subject-28`，起始提交`8309aa9`。当前有效Network secret为ready，凭据只绑定`api.deepseek.com`，未输出值或前后缀。官方模型、中文价格和余额均重新GET读取；余额¥16.55，模型目录包含deepseek-flash。

官方最新[Responses文档](https://api-docs.deepseek.com/guides/responses_api/)明确支持`text.format`；[思考文档](https://api-docs.deepseek.com/guides/thinking_mode/)规定Responses用`reasoning.effort=high`开启High。Anthropic兼容接口的`output_config`只支持effort；Chat Completions的JSON Output使用`response_format`，只保证JSON语法，不能冒充本轮Finding严格Schema的真实验收。

独立scope `80ce1ab12496bdb0ab359dbd6d6a559aaa56d3be6c441ebcae236bf617ad08fe`冻结21个剩余审核payload及一个合成Finding探测。历史unknown的原result SHA和预留被固定、逐项读回比较。没有释放、改写或伪造job5、88账单。

仅执行job109一次实际POST `https://api.deepseek.com/responses`。请求包含合成算术规则及完整Finding Schema；请求参数为deepseek-flash、reasoning.effort=high、max_output_tokens=32768、text.format.type=json_schema、strict=true，无工具、旧对话或私有资料。没有向Anthropic请求混入Responses或Chat参数。

供应商返回HTTP400，错误类型`invalid_request_error`，明确原因：`An object with no properties is not allowed.`。供应商request_id：`0234ee4a-dff9-413a-ae28-bcabc560b8e2`。这是Schema校验拒绝，未取得模型输出、usage或返回模型/High配置证据，**不能称严格JSON、High及32768已真实验证通过**。

原始request、response和receipt已私有留档。新attempt保留，job109状态blocked、费用unknown、预留¥0.326144。余额GET仍为¥16.55，但没有将余额未变化当成逐请求账单或零费用证明。按用户“新的调用如果再次发生费用记录缺失……则停止相关执行并保留证据”的要求，应用正式暂停，stopped=true；没有第二次真实请求、自动重试、Chat回退或私有审核调用。

## 离线修复，不冒充验收

失败位于Schema生成器：没有来源风险标记时，`risk_resolutions`被约束为闭合空对象，供应商不接受。剩余21份payload中也有同类情形，所以不能只绕过合成测试。

修复后的Schema对无标记情况要求模型原生输出`_no_supplied_flags`元数据，准确表达“输入未提供来源质量标记；科学不确定性仍必须放入unresolved”，不表示科学风险已解决。它处在Finding原有`risk_resolutions: dict[str,str]`字段内；不添加科学判断、不删除任何Finding字段或门禁，也不在收到响应后补字段。实际风险标记存在时仍要求每个`unit_id:quality_flag`的解释。

增加递归空对象检查、全部required字段检查、五维固定键、声明枚举与原有Pydantic校验；缺字段、部分JSON、错误模型、Low、缺usage、缓存数不一致、工具调用或不完整响应均拒绝。新wire-contract指纹绑定实际系统指令、Schema算法、High及32768，旧版本原始scope和请求保持可读；协议配置变更不能沿用旧兼容性证据。

修复后合成Schema及21份正式Schema已完成离线检查，35项针对性回归通过。没有真实复验，不能称接口ready。下一轮只需在保留job109未知费用及原预留的明确授权后，执行一次修复后的无私有兼容探测；只有其真实通过，才排入私有审核。已有17题不重复，题31只补答案轮。

## 正式卷与费用实际读回

正式ready仍为17：生化3单选、3填空、3名词；细胞3单选、3填空、2名词。简答与论述尚未补齐。真实组卷函数在回滚事务中再次返回缺11题，未生成正式28题卷，没有iPhone公网入口。

本轮1次真实API请求、0次成功推理，累计47次真实attempt（包含本次校验拒绝）。已记录的历史usage费用估算仍为¥0.87865100，不含三个unknown。累计预留¥14.181888是账本保守记录，不是实际消费。job5、88、109均保留unknown；原5次attempt及¥0.483840原预留不变。

新独立私有快照为65,483,709字节，SHA-256 `63d618a9939b5a84a0d532a2a5af4fedcdead0d5c8dd8419e74f44e8b83434d0`。旧备份保留，新分支`backup-probe109-20261028-63d618a9`保存停止现场；无重复全库恢复演练。

Railway实际部署、生产库修改及收费资源创建均未执行。既有隔离目标、空库恢复与HTTPS/认证/移动端清单保留；本任务仍无有效Railway连接可做云端实时核验。达到28题且另获隔离资源、恢复和部署授权之后，才可能交付iPhone可访问入口。
