# 38题科学审校与资料修复实录（2026-10-28）

本轮仅本地、无付费调用。Codex实际逐题阅读并作出证据约束科学判断；不是独立DeepSeek多轮验收，也不是人类签署。新增审校表明确formal_gate_passed=false，不能被自动组卷读取为ready。

## 审校与版本实际结果

原38版本结论：25通过、9需修订、4存疑。9份修订逐项核对后建立不可变新题稿：8份为证据/答案/评分范围审校通过，1份仍存疑。**当前38现行：33本轮审校范围通过、5存疑；完整正式科学审核门禁通过0、ready0、发布0**。33不是33道可用题，更不是100%科学正确声明。

原45份题稿全部保留，现54版本、16条修订关系、现行38；新增47条不可变审校事件分别覆盖38旧版本和9新版本。没有写入人工决策、没有把局部分析伪装独立模型调用。原8修订之外新增第33题：末项拓扑保持评分未在题干明确要求，已补明提问。

|原ID→现ID|学科|题型|本轮现版本结论|审校要点（不含私有题干/答案）|
|---|---|---|---|---|
|3→3|biochemistry|single_choice|通过（局部，非ready）|电荷与迁移方向、唯一答案|
|5→5|biochemistry|fill_blank|通过（局部，非ready）|外切校对与聚合方向区别|
|6→46|biochemistry|fill_blank|通过（局部，非ready）|反应原料与氧化产物区别；风格换引文|
|7→7|biochemistry|fill_blank|通过（局部，非ready）|还原力与五碳糖双空分别评分|
|9→9|biochemistry|term|通过（局部，非ready）|米氏条件、半最大值与量纲|
|10→10|biochemistry|term|通过（局部，非ready）|净电荷零与pH定义区别|
|11→11|biochemistry|term|通过（局部，非ready）|模板及子代旧新链逻辑|
|12→12|biochemistry|term|通过（局部，非ready）|构象辅助与功能例子|
|13→47|biochemistry|term|通过（局部，非ready）|删除无引用扩展结论|
|14→14|biochemistry|short_answer|通过（局部，非ready）|纯非竞争条件与动力学式|
|15→15|biochemistry|short_answer|存疑|第一维条件表述存疑、同卷提示风险|
|16→16|biochemistry|short_answer|通过（局部，非ready）|密码映射方向和例子逐项核对|
|17→17|biochemistry|short_answer|存疑|连接底物与成熟步骤的适用范围待收窄|
|18→48|biochemistry|essay|通过（局部，非ready）|补齐方向因果链并重拆20分|
|19→49|biochemistry|essay|通过（局部，非ready）|去除隐性评分条件|
|22→22|cell_biology|single_choice|存疑|核心科学可支持，问法与干扰项深度需提升|
|24→50|cell_biology|fill_blank|通过（局部，非ready）|补充核心组成直接引文|
|25→25|cell_biology|fill_blank|通过（局部，非ready）|主要骨架与唯一动力区别|
|26→26|cell_biology|fill_blank|通过（局部，非ready）|起始与执行阶段分工|
|27→27|cell_biology|fill_blank|通过（局部，非ready）|扩散与荧光恢复机制，避免完全恢复泛化|
|28→28|cell_biology|term|通过（局部，非ready）|双基因组依赖与半自主边界|
|29→29|cell_biology|term|通过（局部，非ready）|巨自噬区别于其他自噬|
|30→30|cell_biology|term|通过（局部，非ready）|监控机制与时相区别|
|31→31|cell_biology|term|通过（局部，非ready）|降解与暂存回收区别|
|32→51|cell_biology|term|通过（局部，非ready）|改用基本结构直接证据|
|33→54|cell_biology|short_answer|通过（局部，非ready）|补明题干要求以匹配末项评分|
|34→34|cell_biology|short_answer|通过（局部，非ready）|典型死亡过程与例外边界|
|35→35|cell_biology|short_answer|通过（局部，非ready）|小分子通道、偶联及通透条件|
|36→36|cell_biology|short_answer|通过（局部，非ready）|刺激/抑制方向与两种酶反应分开|
|37→52|cell_biology|essay|通过（局部，非ready）|加工按所选货物适用条件评分|
|38→53|cell_biology|essay|存疑|删短引文未解决来源附带科学冲突|
|39→39|biochemistry|single_choice|通过（局部，非ready）|动力学与热力学区别、逐项排除|
|40→40|biochemistry|single_choice|通过（局部，非ready）|核酸骨架与其他化学键区分|
|41→41|biochemistry|single_choice|存疑|两维顺序正确，原文条件风险未解除|
|42→42|biochemistry|fill_blank|通过（局部，非ready）|延长方向及题干明示两端|
|43→43|cell_biology|single_choice|通过（局部，非ready）|波长与分辨能力而非简单放大|
|44→44|cell_biology|single_choice|通过（局部，非ready）|膜脂物性与相变条件|
|45→45|cell_biology|single_choice|通过（局部，非ready）|典型酸性最适值与普遍断言区别|

五道现版本存疑对应原ID15、17、22、38、41。前两道二维电泳题共用原文条件风险且互相提示；17需收窄连接底物措辞；22需提升问法和干扰项；38来源附带底物/酶名冲突不能靠删短摘录视为科学解决。未以数量压力放行。

## 可查询资料修复

- 新增019、020两个追加迁移，本地实际执行到20；原001—018文件SHA全部一致。
- 2处OMML原XML、哈希、DOCX位置、父正文、上下文入库；以独立omml-search/2保留分数、下标、函数参数和浓度方括号，可通过app.evidence_supplements.search_formulas检索。初版检索表示遗漏分隔符，验证时发现后追加v2，不篡改原始结构或旧记录。当前检索只用v2。该辅助检索尚未接入云端或前端。
- 原25处未转录图像全部有数据库原始字节、SHA、来源/父正文关联；全DOCX扫描额外覆盖3处图片段落，合计28图像补充记录，另2公式，共30条。25处逐一对照前轮清单和原始字节SHA读回；没有猜图、没有声明图像科学核验完成。
- 30条主问题关联注释绑定补充索引与原解析成员，未新增或拆散1921主问题。
- 2003细胞题量冲突再次核对原DOCX/定位，仍为标题1与正文编号12；新增不可变冲突注释，不改原文、不认定官方数量。
- 2009/2010共20道未知官方题型完成逐题问法分析：首题可记录成对术语解释，其余记录开放作答族；不据长度或分数强分简答/论述。保留官方类型未知；特别记录FET疑似缩写转录、姓名错字、线性进化前提存疑及图片依赖。
- 77组重复来源分类已入库为77条不可变注释，保留发生位置/年份/来源SHA，不删除或写入官方考频。
- 共128条注释：30补充关联、20题型分析、1冲突、77重复。教材章节/知识标签索引沿用真实定位，9新稿更新其引文；未批量确认3139映射或11044名称。

## 正式28题缺口

|学科|题型|现行候选|ready|要求|缺口|
|---|---|---:|---:|---:|---:|
|biochemistry|single_choice|4|0|3|3|
|biochemistry|fill_blank|4|0|3|3|
|biochemistry|term|5|0|4|4|
|biochemistry|short_answer|4|0|3|3|
|biochemistry|essay|2|0|1|1|
|cell_biology|single_choice|4|0|3|3|
|cell_biology|fill_blank|4|0|3|3|
|cell_biology|term|5|0|4|4|
|cell_biology|short_answer|4|0|3|3|
|cell_biology|essay|2|0|1|1|

两科各14题、75分；全卷28题、150分、60分钟不变。每科缺3单选/3填空/4名词/3简答/1论述，正式总缺28；不尝试用候选凑卷。旧60题仅保留产品差异/兼容历史，不混用。

补齐顺序：先让33道本轮支持题完成证据独立复检、答案/rubric独立检查和确定性门禁；再处理5存疑。优先选不互相提示的题组，特别不能同卷机械放入二维电泳15/41。现有候选数量覆盖配额，当前主要缺审核而非缺候选数。

可补证方向（仅作为证据任务，不预设考研重点）：生化DNA复制正文单元1190/1192/1197/1204及对应2006转录风格；酶动力学5312/5338/5339、蛋白化学703/5073；细胞核小体8175/8297、分泌路线7532/7535/7550、信号8613/8614/8759及2009/2010汇编实验问法。每一新题仍需具体引用、答案、评分和独立门禁，不把章名当证据。

## 实际验证与保护

61项针对性测试通过，0失败/跳过（1条现有Starlette弃用警告）。覆盖追加迁移保留历史成绩、不可变题稿、自动门禁、公式结构、篡改拒绝、检索、幂等审校；测试使用真实PG连接的rollback隔离schema。没有重复410全量测试。测试首次因新增迁移后旧计数断言18失败及新夹具误解包失败，修正为20并保留原历史内容断言后重新通过。

独立新连接读回：六来源/1915页标/12396正文不变；原38内容对象相等、20迁移SHA匹配、38现行引用精确匹配、25图像原字节SHA匹配、两公式v2检索成功；ready函数对所有现行题均false。source_review_events=0，ai_call_events=0。未创建云资源、未恢复Railway、未启Worker、未部署、未推送、未改原生产。

私有逐题完整分析/证据/评分定位：`/workspace/private-intake/ustc-restore-rehearsal-a84gic2k/science-review-20261028/candidate38-science-private.json`；独立读回`readback-summary.json`；执行回执`execution-private.json`。同目录新建database-after-review.dump及新SHA清单，用于保全本轮本地追加内容，未做这份新dump的异库恢复验收，未外传。原63,472,028字节备份原件及私人GitHub旧快照均不变；本轮新审校尚只有本地副本，不冒称已异地保全。

正式门禁阻塞：当前科学/答案接收流程要求真实、隔离的任务执行记录；本轮明确禁用DeepSeek，不能把Codex单一上下文审校伪造成这些记录。未来启用需明确授权费用及数据发送范围；这不是重新要求逐题人工审批。
