# 一次性私有 QA 六源导入：部署、上传、读回和关闭入口

**执行范围：只允许 Railway 项目 `ustc-exam-ocr-qa` 的 `qa` 环境。**
禁止操作 `amiable-vibrancy/production`、提交原始教材到公开仓库、
向外部公开 PostgreSQL 端口或将真实题目作为已审核题目发布。

## 当前状态

- QA PostgreSQL 已正常运行，1/1 副本，挂载持久化存储。
- 公网 PostgreSQL TCP 代理已移除。
- `six-source-qa-intake` 创建为 **仅暂存** 的 Railway 服务；未连接
  GitHub 源、未运行。
- 本次工具连接 GitHub 代码源时受到服务端安全检查阻止。**不能把它描述为部署成功。**
- 六份原始文件在私有 ZIP 中，ZIP 与六原件的字节一致性已在原始工作环境重新检查。
- GitHub 提供 `backend/app/qa_import_upload.py`（基于 FastAPI 的一次性有密钥上传和原子导入接口）
  及 `backend/tests/test_qa_import_upload.py`。使用 CI 已通过的精确提交，不使用未审核的生产分支。

## 完成一次性 QA 服务部署

1. 在 Railway 打开项目 `ustc-exam-ocr-qa` → `qa`。
   对暂存服务 `six-source-qa-intake` 连接已授权 GitHub 仓库
   `Sakuraone0424/text`，开发分支 `feat/60q-v2-spec`，
   最好锁定经过 CI 的具体 commit。**绝不在 production 中部署。**
2. 构建方式 Dockerfile，路径 `backend/Dockerfile`，
   根目录保留仓库根 `/`。启动命令：
   ```sh
   sh -c 'uvicorn app.qa_import_upload:app --host 0.0.0.0 --port ${PORT:-8080}'
   ```
   健康检查 `/health`。本命令只启动隔离的上传服务，不运行生产答题 API。
3. 在该 QA 上传服务设置变量：
   - `SOURCE_STAGING_DATABASE_URL` = `${{Postgres.DATABASE_URL}}`
     （Railway 对**同一 QA 项目**数据库服务的内部引用，非生产凭据）
   - `QA_IMPORT_SCHEMA` = `source_staging_ocr_20261030`
   - `QA_IMPORT_ENABLED` = `1`
   - `QA_UPLOAD_TOKEN` = 密码学安全随机生成的至少40字符私密令牌
     （在 Railway 中设置；不要写进 GitHub、URL、日志、README）
   - `RAILWAY_ENVIRONMENT_NAME` 应由 Railway 证明为 `qa`；上传接口会
     明确拒绝其他环境。
4. 确认服务健康后，才为**上传服务**开通临时 HTTPS 域名
   （不是 PostgreSQL TCP 代理）。浏览器打开域名可显示上传表单。
   请求必须在 `Authorization` Bearer 头带上传密钥。

## 原件压缩包

只接受用户私有的 `ustc-locked-six-ocr-private-qa-bundle-2026-10-29.zip`。
ZIP 中正好六份被锁定的原件、`PRIVATE_QA_SOURCE_LOCK.json` 与
`README_PRIVATE_QA.txt`。接口必须验证：
文件数、精确内部路径、Zip CRC、大小上限、无符号链接，
以及六个原始文件的实际 SHA-256 与提交的锁定清单完全一致。

ZIP 总 SHA-256（可独立核对）：
`5082558ac6e52c5864a6a3cf334d8f545c9c66c9a7d23251a34ee0b466d0f395`

提供上传密钥与 ZIP 后，接口调用现有
`collection_seed.import_isolated(... initialize_schema=True)`：
在全新独立 PostgreSQL schema 中原子提交六份资料，随后通过另一条
只读数据库连接检查六份身份，才返回成功。

导入的成功响应仅证明六文件与数据库完整性，不代表教材答案科学正确、
31张图片内容全部验收、题目通过双轮评审或120题储备合格。

## 导入后的必要动作

1. 认证访问 `GET /status`，确认显示6/6文件及六份完整性检查通过。
2. 在 QA 内进一步核对四份教材OCR页标1915个、各文件文本单元、
   两份DOCX媒体对象31项、章节映射和风险清单；
   跟离线审计逐项对账，发现分歧不得忽略。
3. 将风险登记、知识图谱和题型模型的未核验事项纳入真实科学审核流程；
   正式120题储备必须为实收两轮评审通过。
4. **关闭 `QA_IMPORT_ENABLED`，撤销 HTTPS 上传域名，并停止/删除一次性上传服务**
   （保留 QA PostgreSQL 及其卷）。
5. 直到七阶段完整验收，保持 PR Draft、不触碰生产系统与历史成绩。

## 失败处理

- 哈希不匹配、ZIP结构有异、缺件：直接拒绝；不进入数据库。
- Schema 已存在：拒绝自动覆盖；必须先检查既有数据库与审计记录，
  不要盲目换 schema 反复导入。
- 数据库失败或读回失败：不返回成功；保存私有错误诊断，
  不公开教材正文、题目、密钥或连接地址。
- 如果 GitHub 源连接或网络被平台权限/安全策略阻断，
  **不要尝试绕过安全检查**；由授权者通过 Railway UI 完成必要连接。
