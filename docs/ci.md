# CI 与合并流程

## 结构（`.github/workflows/tests.yml`）

| 作业 | 做什么 |
|---|---|
| `reuse` | 只在推送到 `feat/**`/`main` 时有意义：判断这个提交的内容是否已经作为 PR 通过了测试（见下）。 |
| `tests`（6 个分片并行） | 每个分片是一台独立的机器，各带一个 Postgres 服务，按**记录的单条测试耗时**把整个测试套件均分成 6 份（`pytest-split`，`backend/.test_durations`），测试覆盖不变：同一套测试，每条恰好在一个分片里跑一次，包含 Chromium / Firefox / WebKit iPhone 15 / HTTPS 恢复测试。 |
| `test` | 汇总作业，名字保持 `test`：所有分片通过（或该提交已通过）才通过。Railway 的 Wait for CI 与分支规则看的是它。 |

加速手段：

- **并行**：6 台机器同时跑，单次约 25–29 分钟降到约一个分片的时长；分片按耗时均衡，不是按文件数。
- **缓存**：pip 依赖（`setup-python` 的 pip 缓存）、Playwright 浏览器（`actions/cache`，键含 Playwright 版本）。
- **数据库提速**：测试库是一次性的，关掉 `fsync / synchronous_commit / full_page_writes`（只在 CI 的临时库）。
- **Postgres**：不再用服务容器（它在步骤开始前拉镜像，拉不下来整个分片就失败，没法重试）；改成一个步骤里 `docker run`，镜像拉取最多重试 3 次，仍不行换 Docker Hub。
- **取消过期运行**：同一个 PR 又推了新提交，旧的运行自动取消。
- **不重复跑**：见下。

## 已经测过的代码不再重跑

两种情况会让同一份代码被再跑一遍，Railway 的 Wait for CI 要等到它们全部结束：

1. 合并进 `feat/60q-v2-spec` 触发的 push 运行；
2. `feat/60q-v2-spec` → `main` 那个长期开着的 PR：每次往 feat 推送，它的 pull_request 运行也会重新开始。

`reuse` 作业对这两种运行都做同一个判断。被检查的提交是：push 的提交，或 pull_request 的头提交。

- **快进**（push 的提交就是某个已通过的 PR 运行的头，SHA 相同）：复用；
- **GitHub 的合并提交**：第二个父提交是某个已通过的 PR 运行的头，**并且**第一个父提交正好是那次运行当时测试所用的基线（PR 运行把「头」和「基线」写在自己的运行名里：`tests pr#N head=<sha> base=<sha>`）→ 合并没有引入运行没见过的内容 → 复用；
- 其它情况（squash、基线在测试后又变了、手工提交、普通的 PR 头）：完整运行。

复用时 `tests` 分片被跳过，`test` 汇总作业几秒内变绿，Railway 随即部署。判断依据不是 GitHub 的 `pull_requests` 字段（分支被重置后它会变空），而是运行名。

## 合并方式：GitHub 普通合并（不需要电脑、不需要脚本）

日常合并就是在 GitHub 上点普通合并（merge commit）。流程里没有任何需要本机执行的步骤，手机/iPad 上也能完成：

1. PR 的 `test` 检查变绿；
2. 普通合并；
3. 合并产生的 push 运行里，`reuse` 作业核对「第二个父提交 = PR 头、第一个父提交 = 那次通过的运行所测的基线」，成立就跳过分片，`test` 汇总几秒变绿，Railway 随即部署；不成立（基线在测试后又变了、squash 等）就完整重跑一次。

所以不依赖提交 SHA 一致，依赖的是「内容一致」的证明。

### 可选：快进合并脚本（不是必需步骤）

`scripts/ff-merge.sh <分支>` 能做真正的快进合并（合并后的 SHA 与 PR 上通过的完全相同），但需要一台能运行 git 的电脑；没有也完全不影响上面的流程。GitHub 自己的合并按钮/接口没有快进模式（“Rebase and merge” 也会改写提交）。

## 更新耗时记录

`backend/.test_durations` 记录每条测试的耗时，用来均衡分片。测试增减很多之后重新生成：

    cd backend && python -m pytest -q --store-durations --durations-path .test_durations

新增的、还没有记录的测试按平均耗时安排，不会漏跑。


## 不因为网络卡住而白等

以前分片偶尔在安装依赖或拉镜像时卡住，一直等到 25 分钟的作业上限才被取消，日志里没有任何说明。现在：

- 每个要联网的步骤都有自己的短时限并自动重试（pip 安装、拉 Postgres 镜像、下载浏览器、`install-deps`、apt）；
- 浏览器缓存恢复超过 5 分钟就放弃，改为直接下载；
- 测试步骤的时限（19 分钟）低于作业上限，所以超时后仍会保存截图和数据库日志；
- `faulthandler_timeout=300`：某条测试卡住 5 分钟时，pytest 会打印每个线程停在哪里，卡住的是哪条测试一目了然。

## 依赖版本固定

`backend/requirements.txt` 和 `requirements-dev.txt` 里每个包都写了确切版本（`==`），重新构建镜像时装到的就是测试过的版本。升级方法：改这里的一行，让 CI 跑，合并。`tests/test_requirements_pinned.py` 保证不会再出现没有版本号的行。

## 一个 PR 只做一类事

一个 PR 里混了很多不相干的改动时，出了问题很难知道是哪一项引起的，CI 一条失败就拖住全部。约定：

- 一个 PR 只做一类改动（一个缺陷，或一个功能，或一处重构），能在几句话里说清；
- 多项反馈拆成多个 PR 依次合并；确实必须一起上线的（例如同一个迁移）才放在一起，并在描述里写明原因；
- 每个 PR 自带它的测试；合并后在 `feat/60q-v2-spec` 上出问题，可以只回退那一个合并提交。

## 前端检查（`backend/package.json`）

- 每个页面脚本都要能通过语法解析；`eslint`（`backend/eslint.config.mjs`）发现错误（未定义变量、重复键等）会让测试失败，警告不会。
- 与页面无关的判断（今日两张卷子的状态、手动判分的选项等）放在 `backend/app/static/logic.js`，由 `node --test tests/js/logic.test.js` 单元测试。
- `tests/test_api_contract.py`：页面测试里的假数据必须带有服务端真实返回里页面要读的字段。
- CI 在跑测试前执行 `npm ci`（按 `package-lock.json` 安装精确版本）；本地想跑 lint 先在 `backend/` 里 `npm ci`。
