# 闭环验收记录（2026-11-13）

本次从 `origin/main` 的 `25ebf25` 建立 `feat/complete-loop`。只修改 oss-watch，被值守仓库不做文件变更、提交、推送或修复 PR。

## 自动验证

- `python -m unittest discover -s tests -v`：36 项通过。测试使用临时目标副本和模拟 GitHub 发送响应，每轮比较副本文件内容，未向真实讨论区发评论。
- `python -m compileall -q src demo.py tests`、`git diff --check`：通过。
- `python tests/mcp_smoke.py`：发版、Issue、PR 三类事件分别在隔离状态目录通过真实 stdio；检查 12 个服务工具、源码读取、事件快照、草稿、提交与仅本地审批。
- `node tests/pi_smoke.mjs <本机Pi包目录>`：真实 Pi SDK 加载项目配置和退出扩展，11 个模型可见工具，人工审批工具隐藏，三项只读调用及退出请求通过；这个探针不调用模型。

发送测试确认本地 `approve` 已落盘后才检查凭据和 POST，且只能发往原事件的 Issue Comments API。覆盖默认关闭、拒绝、无凭据、模拟事件、版本适配、正文被改、请求超时和重复尝试。没有真实发送回执，因此这些结果不代表 GitHub 线上发送验收。

## 本机交互 Pi

在可见、可接管的 PowerShell 窗口运行 `python demo.py watch`，使用独立 `OSS_WATCH_VAR=...\var\complete-loop-issue-20261113`，随后另一个进程投递 `fixtures/issue-menu-overflow.json`。

观察到收件文件自动叫醒一轮真正的交互 Pi。Pi 调用了 Issue 快照读取、源码读取、定向搜索、历史文档搜索和两种草稿工具，最终 `submit_judgment` 成功，结论为 `needs_more_info`，在 17:43:50（本机时区）保存 `pending_approval`。收件事件移入 `inbox-archive/`，Pi 与等待器退出，两个进程租约文件均已释放。没有代拍板，该轮继续等用户决定。

模型也尝试读取其他历史 Issue 和搜索 GitHub；这些调用因在线读取关闭被拒绝，随后仍用本地快照完成判断。结论和评论属于模型静态建议，设备复现、定位假设与历史 Issue 的当前线上状态均未独立验证；不能将快照中的 open 状态当成当前状态。

保留的本地文件（均由 `.gitignore` 忽略，不进入提交）：

- `var/complete-loop-issue-20261113/state.json`
- `var/complete-loop-issue-20261113/runs/demo-issue-menu-overflow-20261113/conclusion.json`
- 同目录 `comment.md`、`patch.md`、`prompt.md`

原 `var/state.json` 与开工前复制到临时目录的备份内容摘要一致，原 `demo-dsh-v0.3.0-rc.1` 待拍板未变。被值守 `dsh-reasoning-effort` 在开工和验收时 `git status --porcelain` 均为空。

## 再次运行

使用 README 的两个终端步骤。本机再次投递模拟 PR 后，`watch --once` 返回 `blocked`、保留队列，未覆盖 Issue 的待拍板记录。人工 `approve` / `reject` 后重新启动 `watch` 才会处理下一轮。原默认状态目录也继续保留发版待拍板，应使用相同独立目录查看或接续本次 Issue，不能混用状态目录。
