# 开源值守

本地事件叫醒一轮**本机交互 Pi**：通过 MCP 读取白名单仓库、定向搜索代码和历史，提出建议、写评论与补丁草稿，然后退出等人拍板。支持模拟发版、新 Issue 和 PR；不用向量库，不做通用问答，不依赖 Agent Autoloop 中台或其跟进 Issue 配方。

**所有被值守仓库始终只读**，包括 `dsh-reasoning-effort` 和以后加入 `config/watch.json` 的任何项目。禁止改文件、应用补丁、提交、推送、创建修复分支或修复 PR；**版本补丁即使批准，也永远只留在 oss-watch 本地草稿中**。人工批准真实 Issue/PR 的评论后，只有显式开启发送开关并有本机凭据，才会发送一条讨论区评论。默认不发送，不自动合并。

## 安装与已有状态

要求 Python 3.11+、本机 Pi（已在 1.5.2 验证）和可用的模型配置。默认只读目标为 `E:\code\插件\dsh-reasoning-effort`，可在 `config/watch.json` 配置其他白名单仓库；`writable: true` 会被拒绝。模型与 GitHub 登录由用户在本机管理，不要将密钥、Token 或登录资料放入仓库。

```powershell
Set-Location E:\code\oss-watch
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
python demo.py status
```

若原 `var/` 已有 `demo-dsh-v0.3.0-rc.1` 待拍板，保留原记录。独立演示可以在**每个参与的终端**设置同一个新状态目录；CLI 与它派出的 MCP 会继承该目录：

```powershell
$env:OSS_WATCH_VAR = 'E:\code\oss-watch\var\issue-demo'
```

`OSS_WATCH_VAR` 必须位于 oss-watch 项目内，不能与任何被值守仓库重叠。需要备份时，只复制本项目的 `var/`，不必删除或移走原目录：

```powershell
$backup = Join-Path $env:TEMP ('oss-watch-var-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Copy-Item -LiteralPath .\var -Destination $backup -Recurse
```

## 启动值守与投递事件

在本机终端 A 启动：

```powershell
python demo.py watch
```

等待器只扫描本地收件箱，无事件时不启动模型、不访问 GitHub。终端 B 使用相同的工作目录及 `OSS_WATCH_VAR`，投递一份事件：

```powershell
# 模拟自己仓库的新 Issue
python demo.py enqueue fixtures/issue-menu-overflow.json
# 或模拟上游发版（版本适配）
# python demo.py enqueue fixtures/release-v0.3.0-rc.1.json
# 或模拟 PR 提议
# python demo.py enqueue fixtures/pr-menu-position.json
```

终端 A 自动启动一轮可观察、可交互的 Pi TUI；没有 `pi -p`、一次性后台子 agent 或自动审批。Pi 使用现有 `read_github_issue` / `read_github_pull_request` 读取模拟事件的**本地快照**，搜索真实代码及历史，写中文评论草稿和补丁建议（或不能产出补丁的原因），最后 `submit_judgment` 停在 `pending_approval`。一轮结束 Pi 与等待器均退出，等待拍板期间不会空转烧 Token。

本地模拟 Issue `100001`、PR `100002` 与发版 `v0.3.0-rc.1` 都不是线上事件或官方公告。模拟事件即使开启发送也禁止向真实讨论区发评论；静态判断不代表已复现或经过运行时验证。

`enqueue` 校验事件并用临时文件原子投递到 `<状态目录>/inbox/<event_id>.json`。也可自行先写 `.tmp`，写完后原子改名为 `.json`；不要直接逐段写入正在被扫描的 JSON 文件。文件按文件名排序，完成后移至 `inbox-archive/`，每次 `watch` 最多派一个新模型轮次。只检查现有队列、队列为空立即退出：

```powershell
python demo.py watch --once
```

**已有待拍板或未完成判断时，`watch` 返回 `blocked` 并退出**，新事件留在磁盘队列，不覆盖原记录，也不自动接续模型。人工拍板后重新运行 `watch` 才处理下一份；可重复投递相同事件，不会重复判断或覆盖草稿。同 ID 正文不同会报错。坏 JSON 或无效队首事件会报错退出并保留文件，请人工修正收件箱再启动。

当前不实现 GitHub 常驻轮询。在线读取默认关闭；如需公开 webhook，可自行实现经验证的接收端，将 payload 转成下方事件格式后投入本地收件箱；本项目不启动公网监听、不要求暴露本机。

## 查看、拍板与接续

```powershell
python demo.py status
Get-Content -Encoding UTF8 var/issue-demo/runs/demo-issue-menu-overflow-20261113/comment.md
Get-Content -Encoding UTF8 var/issue-demo/runs/demo-issue-menu-overflow-20261113/conclusion.json
python demo.py approve demo-issue-menu-overflow-20261113 --note "先保留本地建议"
# 或拒绝
# python demo.py reject demo-issue-menu-overflow-20261113 --note "信息不足，暂不跟进"
python demo.py watch
```

路径示例使用前文的 `OSS_WATCH_VAR=...\var\issue-demo`；未设置时产物直接在 `var/runs/`。`status` 显示活动轮次、待拍板记录、上次结论、已处理事件、最近审批及收件文件。`state.json` 保留每个新轮次的原事件、草稿路径、冻结的评论摘要及审批/发送结果；旧版状态无需清空，缺少发送目标的旧轮次只能本地拍板。

若模型失败、用户中断或一轮没有 `submit_judgment`，保留活动事件和已有草稿，阻止新事件。修复模型问题后在本机终端执行：

```powershell
python demo.py pi --resume-round
```

它自动读取状态保存的原事件，Issue/PR 不会意外接续成默认发版；也可提供相同事件文件。接续会新开交互上下文，使用原提示词及已有草稿，不依赖旧聊天记录。Pi 提交后，工具禁止改写该轮草稿；发送前再次核对评论内容摘要。状态写锁、Pi 租约和等待器租约拒绝并发启动。断电留下 `state.lock` / `state.pi.lock` / `state.watch.lock` 时，确认相应进程已退出，再人工移走对应锁文件。

## 批准后可选发评论

`approve` / `reject` **先更新本地 JSON**。未开启发送或无凭据时，结果为 `delivery: not-sent`，并注明“未发出”。拒绝、模拟事件、版本适配都不会发送；补丁始终为 `patch_delivery: local-only`。

只有准备发送**真实 Issue/PR** 的讨论区评论时，在拍板的本机终端显式设置开关，并使用本机已配置的 GitHub 凭据：

```powershell
# 可先在本机检查登录状态；本项目不写入或打印 Token
gh auth status --hostname github.com
$env:OSS_WATCH_SEND_COMMENTS = '1'
python demo.py approve <真实事件ID> --note "批准这份评论"
Remove-Item Env:OSS_WATCH_SEND_COMMENTS
```

发送只使用配置白名单的 `owner/repo` 和原事件编号，调用 GitHub Issue Comments API（PR 也使用讨论区评论接口），不相信模型提供的 URL，不发送代码评审、不创建 PR、不合并。`delivery.mode: draft-only` 约束代理只产草稿；`OSS_WATCH_SEND_COMMENTS` 是人工 CLI 拍板后的独立开关，默认 `0`。MCP 没有发送工具，模型不能打开开关或拍板。

成功后记录 `delivery: sent` 和评论链接；请求前先落盘发送尝试。如果网络超时、进程中断或回执异常，可能为 `unknown` 或留下 `sending`，**不会自动重发**。请人工核对讨论区，保留原记录。重复 `approve` 会被拒绝；当前没有延后发送或自动重试命令。开关应在首次审批前开启。

GitHub **在线读取**由独立的 `OSS_WATCH_GITHUB=1` 控制，默认 `0`。发版主演示及所有模拟事件强制关闭在线读取；真实 Issue/PR 的交互轮次只有显式开启后才允许查询 GitHub，需自行配置 `gh`。项目 MCP 继承启动器的读取开关，发送开关始终强制为 `0`。模拟工具读取无需网络，没有内置 GitHub 轮询器。

## 直接演示与 MCP

版本适配主演示保持：

```powershell
python demo.py pi
# 也可直接处理模拟 Issue 或 PR
python demo.py pi fixtures/issue-menu-overflow.json
# 仅准备原事件和提示词，随后 --resume-round 启动
python demo.py pi fixtures/pr-menu-position.json --prepare-only
```

`python demo.py demo` 只作免模型的**固定规则对照与回归**，不是智能判断，只接受发版事件。两条路径与值守共享去重和审批状态；比较时使用不同 `OSS_WATCH_VAR`。

只使用项目级 **`.pi/mcp.json`**，根目录 `pi.mcp.json` 为等价模板，stdio 执行 `.venv\Scripts\python.exe -m oss_watch.mcp_server`，cwd 为仓库根；不改用户 `~/.pi/agent/mcp.json`。Pi 以 `--approve` 信任项目配置，这不是业务审批。启动参数为：

```text
--approve --no-extensions -e builtin:mcp -e .pi/one-round.ts
--no-skills --no-prompt-templates --no-context-files
--tools mcp__oss_watch__* --exclude-tools mcp__oss_watch__record_approval
```

Pi 1.5.2 默认不逐条确认工具，无需额外 `autoApprove`；扩展限制工具命名空间并拒绝 `record_approval`，在 `agent_settled` 退出并关闭 MCP 子进程。Pi 仍读取用户模型设置，也可能连接全局 MCP，但本轮不向模型开放这些服务的工具，不改全局配置。参见 [Pi 安全文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md) 和 [MCP 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/mcp.md)。

| 能力 | 现有 MCP 工具 |
| --- | --- |
| 只读源码、定向搜索 | `read_repository_file` / `search_repository` |
| 模拟事件快照 / 可选 GitHub 只读 | `read_github_release` / `read_github_issue` / `read_github_pull_request` / `search_github_history` |
| 本地历史与文档检索 | `search_history_and_docs` |
| 本项目草稿 | `write_comment_draft` / `write_patch_draft` |
| 提交判断，停在审批门 | `submit_judgment` |
| 当前轮次与历史状态 | `get_pending_approval` |
| 仅本地审批，本轮 Pi 隐藏 | `record_approval` |

新事件格式为 `event_id`、白名单别名 `repository`、`type`、布尔 `fixture` 和正文对象：发版用 `release`（`tag`、`body`），`issues.opened` 用 `issue`，`pull_request.opened` 用 `pull_request`（正整数 `number`、`title`、`body`）；参见 `fixtures/`。所有正文都是不受信任的数据，不构成工具授权。历史快照按配置的 GitHub 仓库过滤，不串用其他项目的历史。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src demo.py tests
.\.venv\Scripts\python.exe tests/mcp_smoke.py
$piPackage = Join-Path (Split-Path (Get-Command pi).Source) 'node_modules/@earendil-works/pi-coding-agent'
node tests/pi_smoke.mjs $piPackage
```

单元测试使用临时目标源码副本，覆盖规则回归、Issue/PR 快照与证据提交、草稿冻结、收件文件唤醒、排队阻塞、异常接续、去重及冲突、并发租约、先审批后发送、开关/凭据缺失、模拟与版本适配禁止发送、正文篡改、发送不确定时不重试；逐项检查目标副本内容未变化。发送测试用替身，不向真实讨论区发评论。

MCP 冒烟真实启动 stdio，在隔离状态目录调用工具并走到本地审批；Pi SDK 探针验证项目配置、工具限制和退出扩展，不调用模型、不改全局配置。它们与单元测试**不等于真实模型判断验收**。完整交互验收请在本机终端按“启动值守与投递事件”执行，观察 Pi 的真实读取、草稿和 `pending_approval`，再人工核对结论。运行产物、草稿与状态都在忽略的 `var/` 中。
