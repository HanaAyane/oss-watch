# P4 验收记录：测试自包含、CI 与文档重组（2026-11-17）

分支 `chore/engineering`（基于 `feat/daemon-review`）。

## 做了什么

1. **测试自包含**：`fixtures/target-repo/` 收进 4 个目标文件副本（`src/client/native-selector.tsx`、`package.json`、`CHANGELOG.md`、`README.md`，逐字节复制自验收时的真实仓库）。`tests/test_workflow.py` 与 `tests/mcp_smoke.py` 默认用副本；显式 `OSS_WATCH_TEST_REAL_REPO=1` 才用 `config/watch.json` 指向的真实仓库（仍只读）。
2. **CI**：`.github/workflows/ci.yml`，windows-latest 单平台，Python 3.11，`pip install -e .` 后跑 unittest（-v）+ compileall + mcp_smoke。Pi SDK 探针需本机 Pi，不进 CI。
3. **`status` 暴露 `poll` 段**：每仓库 `attempted_at`/`cursor`/`ok`/`error` 可直接排障核对（operations.md §3.3 依赖它）；`test_poller.py` 增加断言。
4. **文档重组**：README 保留「是什么 + 安装 + 演示 + 部署概要 + 验证」；组件细节 → `docs/architecture.md`，25 条 FAQ → `docs/faq.md`，状态/发送/Pi-MCP 参考 → `docs/reference.md`；新增 `docs/operations.md`（部署前置检查、两条 schtasks、日常节奏、备份恢复、锁/轮询/通知/超时/发送排障、日志产物位置）。拆分为原文搬运加导航，README 增加文档导航表。
5. **未做（需用户确认/预留）**：ruff + mypy 属新开发依赖，按方案 D4 待确认后再加；事实库快照与补丁 dry-run 校验为预留项，本阶段不做。

## 验证证据

- 全量：`python -m unittest discover -s tests` → **76 项 OK（11.5s）**；`compileall`、`mcp_smoke`（12 工具）通过。
- **clone 即可测试**：`git clone --local --no-hardlinks` 到全新临时目录，同步本次未提交的 P4 改动后，在**剥离全部 `OSS_WATCH_*` 环境变量**的干净环境中运行：unittest OK、mcp_smoke returncode 0、compileall returncode 0。克隆用后即清理。（曾尝试临时改名真实仓库目录做更强验证，被权限策略拦截，未执行；克隆验证已覆盖「不依赖真实仓库配置」这一目标。）
- 文档交叉链接脚本检查：README 与 docs/*.md 全部相对链接可解析，无坏链接。
- CI 工作流本身将在 PR 推送后由 GitHub Actions 首次实跑验证（本记录写就时远端尚未运行）。