# 题库、28 题模式与验收说明

本改动只用于独立开发分支与 PR 审查，不包含 production 部署。数据库测试只使用明确指定的开发库，在独立 schema 的强制回滚事务中运行；不会删除、重建或迁移现有 public schema。

## 旧版审查结果

基线：`d0af668104905b31ab88d902df951781defd3bd4`。

- `001_initial.sql` 的八张业务表保存用户、知识点、单选题、每日试卷、题序、提交、答案、复习计划；`002` 补充种子题外部编号。旧题仅有来源字符串，无教材版本/章节/页码/证据/评分细则/审核状态。
- `seed_data.py` 提供 72 道“系统初始题库（非真题）”，不是在线生成或大模型生成。旧 `migrate.py` 在每次执行时覆盖种子题内容和答案，存在改变历史展示/再次判分依据的风险。
- 旧组卷每学科 30 道单选，优先到期错题，再按历史错误率及未见奖励加权抽样；日内不重复，但跨天可以重用。旧批改按 A-D 精确判分，满分 60；错题按 1/3/7 天安排复习。
- 旧 `load_paper` 从当前题表读取内容，历史试卷不是独立内容快照。此次不重写历史记录，也不重算成绩；迁移/种子导入不再覆盖历史题目。
- `/db/health` 仍按旧接口报告八张旧表及旧题数量，不代表 V2 审核库存充足。

## 新版边界和未提供的规范

新增独立 `/v2` 接口及 `reviewed_questions`、`question_audits`、`papers_v2`、`paper_items_v2`、`attempts_v2`、`answers_v2`、`reviews_v2`、`legacy_question_publications` 表。

用户未提供新版规范原文、题型配额/分值、指定教材及版本。因此不内置声称符合“中科大官方规范”的配额，也不编造教材证据或导入所谓合格题。调用者必须明确提供 blueprint 的规范标识和双学科五题型配额，总题数严格为 28；每学科每题型至少一道。实际考试配额须由规范原文确认后配置。

下例仅演示请求结构，不是官方配额：

```json
{
  "specification": "example-only-replace-with-reviewed-spec-version",
  "quotas": {
    "biochemistry": {"single_choice": 4, "fill_blank": 4, "term": 2, "short_answer": 2, "essay": 2},
    "cell_biology": {"single_choice": 4, "fill_blank": 4, "term": 2, "short_answer": 2, "essay": 2}
  }
}
```

分值来自每道题的 `max_points` 和逐项 rubric；当前不推测官方考试总分。填空目前支持一个 `____` 空位、多个明确允许的同义答案；多空题应拆分或另行扩展结构，不能伪装成单空题。

## 发布审核与证据

每题须有：学科、题型、题干、标准答案、教材书名/作者/版本/有效 ISBN/章节/页码或图节定位/短摘录，以及总分和可加总的逐项评分标准。单选必须有四个不同选项和一个答案键；主观题不接受伪装的选择题选项；填空答案必须在显式允许列表中。

专家需提交五维审核，每项包含通过/不通过及理由：

| 字段 | 审核内容 |
| --- | --- |
| `scientific_correctness` | 对照教材核验科学事实、概念边界及答案 |
| `exam_style` | 对照明确的真题/命题规范检查风格，不将种子题宣称为真题 |
| `uniqueness` | 单选唯一正确项；填空允许答案边界；主观题评分点无歧义 |
| `type_fit` | 题干、认知深度、答案长度与题型及分值匹配 |
| `duplication` | 识别改写、同义重复、考点机械重复，补充字符检测的盲区 |

另须填写 `evidence_verification`，记录实际核对的教材定位及证据为何支持答案。系统不会联网下载教材，也不会把“ISBN 校验通过”当作科学结论。审核者必须持有独立的 `QUIZ_REVIEW_API_KEY`；普通答题 Cookie/API key 无法发布或评分。此单用户系统以共享操作员密钥授权，`reviewer` 是操作记录标签，不是独立人员身份认证。

自动检测对 NFKC 规范化、去标点和空白、折叠大小写后的题干计算 SequenceMatcher 相似度，阈值 `>=0.85` 即禁止发布；记录比较数量、相似题数、匹配比例、最大相似度和阈值。比较对象为已发布新题及全部旧题（排除该题自身的旧接口导出副本）；发布时再次检测，事务锁避免两个发布操作同时绕过检查。字符相似度不保证语义去重，五维专家审核仍必需。测试包含发布前后重复条件变化的复核，未声称完成多会话压力测试。

题目内容不可原地修改，纠错需建新草稿并重新审核；审核记录只能追加。任何维度失败都会阻止发布；已发布题复审失败自动撤下，影响未来组卷而不修改既有试卷快照。撤回不自动撤销历史成绩。

## 接口与判分

- 操作员：`POST /v2/questions` 建草稿；`GET /v2/questions/{id}` 查内容及审核历史；`POST .../audit`；`POST .../publish`；`POST .../withdraw`；`GET /v2/quality/report` 查看库存和失败审核数。请求头为 `x-review-api-key`，密钥未配置时操作员接口返回 503。
- `POST /v2/questions/{id}/export-legacy` 将已发布单选题幂等导出到旧题表并建立审核关联；填空及主观题不可导出。
- 学习者沿用现有登录 Cookie、`x-api-key` 或 Bearer 认证；`POST /v2/papers` 传 blueprint 组卷，`GET /v2/papers/{id}` 读取本人试卷。组卷响应不泄露标准答案、教材摘录或 rubric。
- 库存不足返回 HTTP 409，`detail.code=insufficient_reviewed_bank`，按学科和题型报告 required/available。整卷生成事务回滚，不创建半张试卷。只用已发布题，并排除本人旧版/新版试卷中相同或相似的已见题干；不会拿旧种子题或重复旧题补足。
- 同人同日重复请求返回已有卷，若 blueprint 不同则 409，不覆盖试卷。不同用户不能读、提交或查阅对方成绩。试卷逐题保存不可变内容快照，撤题不影响已有试卷。
- `POST /v2/papers/{id}/submit` 的 `answers` 是 question ID 到文本答案的映射，必须恰好包含该卷 28 个 ID，不能为空。重复提交 409。无效请求的全部写入回滚。
- 单选自动判分；单空填空对 NFKC 后去首尾空格的显式允许答案匹配，保留科学含义上的大小写（例如 `mM` 不等于 `mm`）。主观题保持 `points=null`；存在未评分题时 `score=null,status=pending_review`，不会误报整卷分数。
- 操作员 `POST /v2/papers/{paper_id}/answers/{question_id}/grade` 提交 `criterion_points` 数组、feedback、reviewer；每项不得超过快照 rubric 对应分数，只能评分未批主观题，不能覆盖已完成成绩。全部批完才返回整卷最终分数。
- `GET /v2/papers/{id}/result` 返回本人提交后的答案、教材证据、rubric、反馈及评分状态。错题/失分题按批改日期安排 1/3/7 天复习；`GET /v2/review/due` 查询，`POST /v2/review/{attempt_id}/{question_id}/{stage}/complete` 只完成本人的到期复习。复习不把旧题重新填入新 28 题试卷。

旧 `/quiz/today`、`/quiz/submit`、`/history`、`/stats`、`/review/due` 的 60 题响应结构、旧成绩和历史试卷保留。新增旧接口组卷同样只选审核导出的单选题；原 72 道未审核种子题不会自动获得资格，库存不足返回 409。这是有意实施的质量门禁，而不是用未审核题保持表面成功。旧版跨天复习重用策略仍保留；严格不重复规则适用于新 28 题模式。

## 迁移与验证

迁移执行命令（仅在明确选择的开发库或以后独立批准的发布步骤运行）：

```sh
cd backend
python migrate.py
```

脚本采用事务和 advisory lock，登记迁移文件 SHA-256；重复运行跳过已应用文件，已应用文件被修改则报错。兼容无迁移登记表的旧库升级。001/002 原文件保持不变，003 只新增对象，不修改旧表、成绩、答案或试卷。任何一步失败回滚当次全部迁移。不提供删除数据的 down migration。

`python migrate.py --seed-legacy` 仅显式补入缺失的旧演示题；已有 external_id 题目不更新、不认证。默认迁移不播种。

```sh
cd backend
python -m pip install -r requirements-dev.txt
# 用明确选定的开发 PostgreSQL 连接；不得指向 production。
TEST_DATABASE_URL=postgresql://agent@127.0.0.1:5432/quiz python -m pytest -ra -q -p no:cacheprovider
```

未设置 TEST_DATABASE_URL 时数据库测试明确 skip，不构成迁移/接口验收。CI 使用独立 PostgreSQL 17 测试服务，运行同一套测试；工作流没有部署步骤或 production 凭据。

测试 schema 在外层强制回滚事务中创建，search_path 仅指向该 schema。迁移和 API 测试不改动已有 public 业务数据。验收详细结果见 [acceptance.md](acceptance.md)。

## Railway 与 ChatGPT 真实调用边界

可核实：`railway.json` 指定 `backend/Dockerfile`，容器只启动 `uvicorn app.main:app`，不会自动运行迁移；静态首页写“每日 60 题”，`app.js` 使用相对路径 fetch `/quiz/today`、`/quiz/submit` 等同源接口。仓库没有 ChatGPT Action/OpenAPI 导出、GPT 应用标识、外部调用域名、28 题客户端或 Railway 访问日志。

因此目前只证明“该仓库配置的 Railway 应用包含旧 60 题前后端”，不能证明当前线上版本、ChatGPT 28 题应用实际调用它，或两者共享数据库。FastAPI 自动生成的 `/openapi.json` 仅描述服务能力，不是已接入 ChatGPT 的证据。

后续核实需提供 ChatGPT Action 的 schema/server URL/operationId（不要提供密钥）、Railway 服务域名及脱敏访问日志/请求 ID，对照具体请求路径与时间。接入 V2 须显式配置新 `/v2/papers` 等 operation；不得假定旧 `/quiz/today` 会返回 28 题。此次没有连接 Railway 管理面、调用 production、修改 Action 或部署应用。
