# 第二批真实资料审校结果（2026-10-27）

范围：仅本地恢复库只读审查/私有衍生文件、必要解析代码修复，不创建云PG、不恢复Railway、不启Worker、不调用DeepSeek；ready维持0。旧PG17恢复验收不重复，原63,472,028字节备份与68表内容保留。

## 1921主问题基础核验与两年题型修复

实际遍历两份DOCX的2741个已索引单元和52年份，重新检查1921道主问题/成员归属/年份/题型/图片风险。未导入新文件或把旧建议批量升级。

- 生化2006年漏识别“解释以下名词”和“回答以下问题”式节标题，造成填空题类型向后串延。修复后48道主问题不变：**20填空、11名词、17简答**，而不是48填空。原文每题/总分的算术分别20×3=60、11×2=22、17×4=68，只作为汇编文本一致性支持，不宣称官方原卷认证。
- 生化2007年“选择填空”标题原未识别，修复为mixed而非擅自断定训练单选；37道不变：**11填空、20混合选择填空、6问答**，不再31填空。
- 解析叠加版本transcript-review/1.5，后续显式生成工作项使用evidence-work/1.7；未运行批量persist。progress在新版本未生成前明确报告最新兼容已存1.6队列，不能显示候选归零或累加各历史版本。
- 细胞2003节标题明确声明1小题，正文存在编号1—12，冲突仍保留；不能猜补“12”并覆盖原文。其余旧已修复异常不重开为官方结论。
- 细胞2009首主问题包含10组成对术语子项，不展开成20个独立主问题；年主问题9不变。2010有11主问题、包含实验设计/图示子问题，没有节标题的20道类型仍保留未知，不能凭题干长短强行归类为论述。

## 77组重复：已逐组按来源位置、年份和分卷标题检查

|互斥处置类别|组数|本轮实际处理|
|---|---:|---|
|同年汇编分卷/不同标题之间重复|17|保留各paper_heading_unit与全部发生位置，未核实原卷身份前不合并|
|跨学科共通概念（先排除上类）|9|两科分开，不能当跨科污染或删除依据|
|同科跨年名词题重复|47|属于观察到的术语重复，不能直接视作OCR重录错误|
|其余跨年完整题干重复|4|保留文本/来源SHA与发生年份，待来源确认|

合计77组、181次出现。跨学科标记一共10组，其中1组同时属于同年分卷类；不能把9误报为总跨科数。生化2012原文存在“生化与分子”和“生化”两个同年标题，这是不自动合并的直接依据，不等于已确认两张官方原卷。未删除104个额外出现，也未将重复次数写入高可信考频。历史25条transcription_repeat工作项判据不同，不与77相加。

## OCR/公式/图像高风险的实际处理

- 原3条transcription_gap_reported均落在出版社“如有缺页/倒页/脱页请调换”条件声明中；确认是缺页检测上下文误报，不是当前文件缺页的证据。原标记保留，另存有原文SHA的解释事件，不冒充科学审核。
- 原1条ocr_uncertain中的方框是“用方框标出”的作答指令，并非不可识别字符；同样只记录审校解释，不静默改原文。
- 25处untranscribed_image全部在原DOCX定位到内部图片关系，25个唯一图像均能解码；提取到私有目录并记录源文件SHA、DOCX位置、关系ID和图片SHA。不是重新OCR，更不代表图像科学内容已经转录/核验。
- 全DOCX结构扫描发现28个带内嵌图段落，以及**2处原始OMML公式没有进入旧文本索引**：生化DOCX body/1371和body/1377。原文件数据未丢失，已保全XML哈希及分数/下标等AST，分别关联候选父单元11066、11071；尚未创建正式证据单元或改写原source_units。此前“重解析与已存索引一致”只是同一文本提取器的一致性，不等于覆盖全部Word数学对象。
- 图像未转录涉及22主问题；另3道引用图示的主问题尚未绑定到内嵌图，必须继续定位，不能凭文字猜图。图片在六资料之内就使用已有图片，不要求额外PDF；确实不足的题继续不可用。

## 38道候选逐题证据审查与8份定向修订建议

已实际阅读全部题干、选项/答案、rubric、教材引文和转录风格关系，不只是重复运行Schema校验。每题私有记录绑定候选key、内容SHA、教材ID/SHA/页码/单元/摘录SHA，包含分析与不确定性。来源保持transcription，所有科学审批=false、human_signed=false、bank_state_change=false；Codex辅助意见不冒充DeepSeek独立审核或专家签署。

30道为文本核心有支撑、待独立科学检查；8道形成完整本地修订建议（不是8条新增题库记录）。修订建议已通过Question模型、分值及新引文精确匹配检查，但没有插入题库、没有修改45份历史版本，也没有发布。涉及：风格引用题型不符、答案额外结论缺引用、机制因果链缺项、隐藏评分要求、引用不够直接、货物加工条件评分过严，以及引用段落附带主张冲突。补证仅使用现有六资料，不凭模型记忆补来源。

|候选ID|学科|题型|文本审校处置|问题代码|
|---:|---|---|---|---|
|3|biochemistry|single_choice|核心文本有支撑，待科学检查|pending_independent_science|
|5|biochemistry|fill_blank|核心文本有支撑，待科学检查|pending_independent_science|
|6|biochemistry|fill_blank|需补证/引用或评分修订|style_type_mismatch|
|7|biochemistry|fill_blank|核心文本有支撑，待科学检查|pending_independent_science|
|9|biochemistry|term|核心文本有支撑，待科学检查|pending_independent_science|
|10|biochemistry|term|核心文本有支撑，待科学检查|pending_independent_science|
|11|biochemistry|term|核心文本有支撑，待科学检查|pending_independent_science|
|12|biochemistry|term|核心文本有支撑，待科学检查|pending_independent_science|
|13|biochemistry|term|需补证/引用或评分修订|unsupported_answer_extension|
|14|biochemistry|short_answer|核心文本有支撑，待科学检查|pending_independent_science|
|15|biochemistry|short_answer|核心文本有支撑，待科学检查|pending_independent_science|
|16|biochemistry|short_answer|核心文本有支撑，待科学检查|pending_independent_science|
|17|biochemistry|short_answer|核心文本有支撑，待科学检查|pending_independent_science|
|18|biochemistry|essay|需补证/引用或评分修订|missing_mechanistic_link|
|19|biochemistry|essay|需补证/引用或评分修订|rubric_hidden_requirement|
|22|cell_biology|single_choice|核心文本有支撑，待科学检查|pending_independent_science|
|24|cell_biology|fill_blank|需补证/引用或评分修订|citation_not_direct_enough|
|25|cell_biology|fill_blank|核心文本有支撑，待科学检查|pending_independent_science|
|26|cell_biology|fill_blank|核心文本有支撑，待科学检查|pending_independent_science|
|27|cell_biology|fill_blank|核心文本有支撑，待科学检查|pending_independent_science|
|28|cell_biology|term|核心文本有支撑，待科学检查|pending_independent_science|
|29|cell_biology|term|核心文本有支撑，待科学检查|pending_independent_science|
|30|cell_biology|term|核心文本有支撑，待科学检查|pending_independent_science|
|31|cell_biology|term|核心文本有支撑，待科学检查|pending_independent_science|
|32|cell_biology|term|需补证/引用或评分修订|citation_scope_and_model_risk|
|33|cell_biology|short_answer|核心文本有支撑，待科学检查|pending_independent_science|
|34|cell_biology|short_answer|核心文本有支撑，待科学检查|pending_independent_science|
|35|cell_biology|short_answer|核心文本有支撑，待科学检查|pending_independent_science|
|36|cell_biology|short_answer|核心文本有支撑，待科学检查|pending_independent_science|
|37|cell_biology|essay|需补证/引用或评分修订|rubric_unconditional_processing|
|38|cell_biology|essay|需补证/引用或评分修订|source_claim_conflict|
|39|biochemistry|single_choice|核心文本有支撑，待科学检查|pending_independent_science|
|40|biochemistry|single_choice|核心文本有支撑，待科学检查|pending_independent_science|
|41|biochemistry|single_choice|核心文本有支撑，待科学检查|pending_independent_science|
|42|biochemistry|fill_blank|核心文本有支撑，待科学检查|pending_independent_science|
|43|cell_biology|single_choice|核心文本有支撑，待科学检查|pending_independent_science|
|44|cell_biology|single_choice|核心文本有支撑，待科学检查|pending_independent_science|
|45|cell_biology|single_choice|核心文本有支撑，待科学检查|pending_independent_science|

8份完整修改建议在eight-revision-proposals-private.json，逐题意见在candidate38-textual-audit-private.json；不把题干、答案或教材正文贴入公共仓库。候选15与41存在同卷线索风险，记录为选题冲突对建议，尚未声称生产选题规则已改变。对14的一部分动力学推导用手写Fraction函数核算，不eval任何OCR表达式，不能据此宣称整题科学门禁完成。

当前两科各草稿：单选4/填空4/名词5/简答4/论述2；各题型ready全部0，正式28题仍不可组。

## 权限验证、测试与接续

新恢复库中实际确认只读应用角色非superuser/CREATEDB/CREATEROLE，68表可SELECT、0表可写；pg_control_system最小函数授权在rollback事务内实测可用。云角色未创建，详细恢复/回滚/删除流程见database-recovery-plan.md。

针对本次修改运行test_evidence_work.py、test_real_handoff_tools.py、test_sources_exams.py：**47 passed、0失败/跳过、1条既有弃用警告，3.09秒**，连接本地恢复PG17的rollback隔离测试schema；保留原有断言，新增8项回归（包含参数化案例）。未重复410全量测试。Ruff F/E9和diff检查通过。实库68张原表行数与Manifest仍一致，45版本/38现行/ready0不变。

私有工作目录`/workspace/private-intake/ustc-restore-rehearsal-a84gic2k/second-audit`。其中main1921-audit-private.json、duplicate77-adjudication-private.json、ocr-context-adjudication-private.json、untranscribed-media-map-private.json、docx-structures-private.json、two-omml-artifacts-private.json、candidate38-textual-audit-private.json、eight-revision-proposals-private.json均保存具体证据，不对外上传。公开机器摘要data-quality-followup-20261027.json不含原文。

下一批重点：将2个OMML对象作为可追溯衍生证据纳入索引设计、转录已有25图、确认3处图示归属；复核8份修订建议后才按不可变新版本方式入库并走完整科学/答案流程。本轮没有新增审批，不进行自动科学通过。原始备份、原库及Railway全部不变。
