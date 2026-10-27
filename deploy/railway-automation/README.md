# Railway隔离部署准备（未执行）

本方案取代此前Tailnet/VPS建议：只评估已有xiao-ustc-quiz项目内新的非生产环境。普通HTTPS + 应用登录，iPhone/iPad Safari可操作；不依赖Tailscale，不要求关闭Loon。没有真正部署或iOS设备实测，不能把配置包当作上线。

## 授权前已完成

- 独立Dockerfile（Python3.12 + 固定官方Harness SDK 0.2.1rc2及其内置运行时 + PostgreSQL17客户端），API/短任务Worker配置分离。原railway.json与production配置未改。
- 本地镜像构建、无网络容器内API导入、SDK版本与pg_dump17检查成功。生产镜像不复制Git、虚拟环境、备份或私有正文。
- 用户侧标准HTTPS认证；/static/model.html用于一次性模型/预算/备份设置。每日训练不依赖ChatGPT保持打开。ChatGPT原生应用尚未注册/验证，现有API适配契约保留。

## 必须单独确认的范围

1. 通过可信Secret提供Railway访问权限，先只读核对项目ID、环境、已有套餐和预计增量费用。不能凭项目名称宣称已验证部署。没有安装/登录Railway CLI或可用Railway绑定，本轮未读取production变量。
2. 明确批准在这个项目中新建隔离环境（建议automation-staging）、Python API服务、定时Worker、空白PostgreSQL及必要的私有备份卷。费用未知，未自动购买资源。确认资源清单/费用后才创建。
3. 明确批准将现有私有快照传输并恢复至指定的新空库，核对目标环境/服务ID，绝不使用production凭据。单独批准新HTTPS入口/域名。禁止覆盖当前原库或生产库。
4. 首次DeepSeek API调用、教材摘录/作答发送分别授权；仅配置密钥不代表同意外发。预算必须是明确有限金额，未知模型价格保持停机。

## 获授权后的操作次序

- 保存目标项目/环境/服务ID，不仅依靠名称。先核验新库为空（系统schema之外无用户表），current_database符合审批名称，与原库区分；未明确批准时不要创建恢复演练数据库。
- 当前备份包含custom dump、manifest、RESTORE.txt。先验证ZIP SHA/CRC、dump SHA及完整解码；使用PostgreSQL17 `pg_restore --single-transaction --exit-on-error --no-owner --no-acl`，连接参数通过进程环境/受控Secret，不写进命令行、Git或包。不使用--clean、--create，不删除/重建数据库。
- 独立连接恢复库：UTC下逐表对to_jsonb(row)::text做SHA256、排序再汇总，与manifest的行数/摘要逐一对比。验证序列连续性、001—017迁移及SHA、六来源原字节/索引/页标、213342既有工作项、38有效候选/45版本/7修订链。执行真实查询后才能称恢复演练通过。
- 配置`DATABASE_URL`（仅新库）、`QUIZ_DB_SCHEMA=source_staging_real_20261026`。新镜像默认QUIZ_AUTO_ONLY=1，训练仅选自动ready，旧人工发布题不作为回退；应用不会自动迁移或创建数据库。旧包若缺017，需要在明确授权目标中调用apply_migrations并记录增量，不重导六文件。
- API服务设置`QUIZ_PUBLIC_ORIGIN`为获批准的准确HTTPS origin（适配Railway反向代理，不盲信任任意转发头），并配置独立`APP_PASSWORD`、`SESSION_SECRET`、可选专用learner API key、Secure cookie。**不设置QUIZ_REVIEW_API_KEY**，使旧人工写接口默认关闭；历史审核事件继续不可变归档。不要把密钥配置进前端。
- Worker与API使用不同最小权限数据库角色。AI子进程不继承任何数据库URL、Railway令牌或后台审核密钥；不向Harness提供Shell、SQL或任意网络工具。源码/教材只读，只有受控Python业务写入。
- Worker用worker.railway.json，每5分钟短运行一次。API内现有deadline/20:00北京时间循环保持可用；Worker在长推理之前也执行到期/备卷tick。少量常驻API+数据库，推理Worker不常驻。实际资源消耗/费用须在授权环境测量，不能从本地成功推断Railway额度充足。

## 一次性模型配置

- 在Railway Secret设置`DEEPSEEK_API_KEY`，不在聊天、SQL、备份、日志或Git输入。
- 明确授权后才设置`DEEPSEEK_API_AUTHORIZED=1`；教材/作答最小片段外发另行同意后才设置`DEEPSEEK_PRIVATE_DATA_AUTHORIZED=1`。AI_AUTOMATION_ENABLED默认0。
- Safari登录模型页面，刷新实际官方目录；设置人民币单任务/首次试验/每日/月度预算，单任务输出Token上限、重试与并发，取消停止开关。首次合成兼容性探测不发送教材。它也是真实付费API使用，须先授权。
- 当前目录若无价格，填写依据官方价格确认的正数单价上界与官方URL，再对目标模型/档位运行小探测。系统明确将其称为用户声明的预算上界，不伪装成API返回价格或真实账单；未知价格不自动调用。真实费用字段在无可靠计价信息时为null，保留保守预留预算。
- /model default保存deepseek-flash/high并暂停，未发现/未测模型不会被凭空添加。只把实际成功的档位列为可用。自动发现默认开，自动升级默认关；即便开启升级，也要求真实兼容性、可信质量回归及预算门禁。
- 完成小规模真实API/成本/隐私测试后，才启用`AI_AUTOMATION_ENABLED=1`及`QUIZ_DAILY_PREP_ENABLED=1`。先保留AI_QUEUE_AUTOPLAN=0、AI_FULL_CORPUS_ENABLED=0，只运行明确排队的小规模38题样本；试验通过后在授权范围内启用AI_QUEUE_AUTOPLAN=1自动处理38题。全库处理须确认数据范围后才设置AI_FULL_CORPUS_ENABLED=1，其按单元发送的片段长期累计可能覆盖六份资料全文；不将其伪称只发送永远固定的少量原文，不额外上传会话日志。不要把静态旧分析当成科学审核。ready不足时记录缺口、做有界补充，不降门禁。

## 私有备份与手机验收

配置仅指向新库的`SOURCE_STAGING_DATABASE_URL`、`CANDIDATE_REVIEW_SCHEMA`、`CANDIDATE_REVIEW_DATABASE_NAME`、`CANDIDATE_BACKUP_ROOT`、`CANDIDATE_BACKUP_POINTER`、`CANDIDATE_PG_BIN`（17客户端目录）。这些配置在Secret/受控环境，不放进备份。登录后的模型页面可生成最新私有包，认证/Range下载并显示SHA。Safari下载→分享→存储到文件；不能声称用户已经实际保存。

获批准持久备份卷和保留成本后，可设置QUIZ_PRIVATE_BACKUP_ENABLED=1，默认每日北京时间04:00后的首个API后台tick生成一次新私有快照（QUIZ_PRIVATE_BACKUP_HOUR可改）。PG会话锁与快照日期防止并发/重启重复，旧包全部保留，不自动清理历史。备份卷只挂载至单实例API服务，并确认postgres用户有写权限；Railway不提供跨服务共享卷，短生命周期模型Worker不会写入或冒充这份持久备份。备份不依赖DeepSeek是否可用。后台进程未运行或配置失败时不会产生成功快照。默认关闭，不因创建服务就暗中占用备份存储；本轮未启用。

备份卷本身不是异地备份。长期策略需用户批准保留周期、Railway原生备份能力及费用，或另行授权私有备份存储。没有授权不得自动上传第三方。Codex本地环境可能被回收，必须在回收前完成实际交接。

最后从真实iPhone/iPad实测HTTPS证书、登录、Loon正常启用时的连接、28题滚动/照片/自动暂存、开始60分钟、刷新/切换设备、超时与未答0分、待批、评分/FSRS/历史、退出鉴权和备份保存。没有真实设备与公网地址，不宣称这些已验收。未确认当前模型视觉能力前，手写自动转录保持不可用/待定，不猜测识别后扣分。

## 人民币计费与激活范围

先追加018迁移（001—017不改），旧USD账本只读归档，不做汇率转换。使用`python -m app.cny_prices --refresh-official-cny-prices`读取官方中文公开价格；不需要DeepSeek Key，不发送私有内容。新设置预算0、stopped=true；用户同意后才设置CNY限额并启用相应服务端授权。未知/过期/冲突价格一律阻断；每次尝试快照当前价，模型/思考档位仍冻结。API的Decimal金额使用字符串，Railway账单独立。

真实数据库已应用018，但没有真实API/新Railway部署或异库恢复。最新备份、测试类别及局限见[当前验收](../../docs/cny-live-readiness-20261027.md)，具体资源估算、私有传输范围、授权和手机步骤见[集中交接](../../docs/live-activation-authorization.md)。不要把本地容器成功当成云端上线。
