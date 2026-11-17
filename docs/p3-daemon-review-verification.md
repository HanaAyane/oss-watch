# P3 验收记录：常驻循环、通知与审阅视图（2026-11-17）

分支 `feat/daemon-review`（基于 `feat/poller`）。改动仅限 oss-watch：新增 `notify.py`、`review.py`，扩展 `inbox.py` loop 模式与 CLI `watch --loop/--no-notify`、`review` 子命令；默认模式行为不变。

## 自动验证

- `python -m unittest discover -s tests`：**76 项通过（12.0s）**。新增 `tests/test_daemon_review.py` 7 项：loop 拍板后自动接续第二条队列事件（线程 + sleep 替身扮演人）、判断失败即停返回 `needs-resume` 不重试、待拍板通知按事件去重只发一次、toast 载荷走 `-EncodedCommand` 编码回归、toast 失败降级响铃、review 渲染待拍板含拍板命令且零写入（对比事实日志前后一致）、历史回看与未知事件报错。
- `python -m compileall -q src demo.py tests`、`git diff --check`：通过。
- `python tests/mcp_smoke.py`：三类事件真实 stdio 通过，12 个工具。

## 本机真实验证（不调用模型，不改待拍板）

在 P1 真实 Pi 轮次的状态目录 `var/p1-acceptance` 上执行（该轮 `engine=pi`、`decision=needs_info`，仍待拍板）：

1. `python demo.py review --event demo-issue-menu-overflow-20261113`：完整渲染判断、置信度、7 条理由、8 条待验证项、17 条源码证据与产物路径，标注「模拟事件」；`status` 确认 `pending_approval` 未变、队列为空。
2. loop 冒烟：`inbox.run(loop=True, notify=True)` 配 sleep 替身两次心跳后 Ctrl+C。观察到通知发出、Ctrl+C 干净退出、`pending_approval` 原样保留。
3. **发现并修复真实 bug**：Windows PowerShell 5.1 会把 `-Command` 之后的剩余参数拼进脚本正文执行（`$args` 恒为空），含空格/中文的通知文案被当作命令解析报错，toast 永远失败、全部降级响铃。修复：文案以 Base64 JSON 嵌入脚本、整体走 `-EncodedCommand`。修复后实测 `notify(...)` 返回 `toast`，桌面出现真实通知；此前同文案实测 returncode=1（`The term 'oss-watch' is not recognized`）。

## 未覆盖

- 「GitHub 发 Issue → toast → review → approve → daemon 自动接续」的端到端验收需要先注册任务计划（README「部署为常驻值守」给出 `schtasks` 命令，由用户手动执行）并等待一轮真实模型判断，本轮未执行。
- 无桌面会话（如任务计划「不管用户是否登录」模式）下 toast 的降级路径只由单测替身覆盖；建议 daemon 任务保持「只在用户登录时运行」。

默认 `var/` 全程未触碰；验收产物均在被忽略的 `var/p1-acceptance/`。