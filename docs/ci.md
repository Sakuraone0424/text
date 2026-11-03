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
- **Postgres 镜像**：服务容器在步骤开始前就启动，`actions/cache` 够不到它，没法缓存；改用 `public.ecr.aws` 镜像，拉取更快、不受 Docker Hub 限流。
- **取消过期运行**：同一个 PR 又推了新提交，旧的运行自动取消。
- **不重复跑**：见下。

## 合并后不再重跑

合并进 `feat/60q-v2-spec` 会触发一次 push 运行，Railway 的 Wait for CI 要等它。`reuse` 作业在这时判断：

- **快进合并**（可选脚本，提交 SHA 与 PR 上通过的 SHA 相同）：直接复用；
- **GitHub 的合并提交**：第二个父提交是 PR 头，第一个父提交正好是那次通过的运行所测试的基线 → 合并没有引入运行没见过的内容 → 复用；
- 其它情况（squash、基线又变了、手工提交）：完整重跑。

复用时 `tests` 分片被跳过，`test` 汇总作业几秒内变绿，Railway 随即部署。

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
