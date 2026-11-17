# 参考：状态与接续、评论发送、Pi/MCP 入口

> 本文从 README 拆出，收录三块参考细节。日常操作见 [README](../README.md)，部署与备份见 [operations.md](operations.md)。

## 收件箱、状态与接续参考

`enqueue` 校验本地事件 JSON 后，把原事件作为入队事实追加到 `<状态目录>/oss-watch.sqlite`。自行编写投递器也应调用 `enqueue`，不再写 `inbox/`。成功完成一轮或识别为已处理后，追加归档事实，从当前队列移除，原事件与历史仍保留在库中。每次 `watch` 最多派一个新模型轮次；`--once` 只是让队列为空时立即退出，不会绕过审批或禁用模型：

```powershell
python demo.py watch --once
```

扫描间隔可以用 `--interval` 调整，单位是秒，默认 `1.0`，允许 `0.1` 到 `60`。已有待拍板或未完成判断时，默认模式的 `watch` 返回 `blocked` 并退出，不自动接续（`--loop` 见 [architecture.md](architecture.md)）。拍板后需重新启动，未完成时使用：

```powershell
python demo.py pi --resume-round
```

不传文件时，它读取状态保存的原事件；也可以显式提供相同的事件文件。接续使用原提示词和已有草稿。Pi 提交后，工具禁止改写该轮草稿；发送前再次核对评论内容摘要。状态写锁、Pi 租约和等待器租约拒绝并发启动；异常遗留锁的处理见 [faq.md](faq.md) 第 20 条。

**事实日志就是按时间追加发生过的事**：事件入队、开始判断、提交结论摘要与草稿路径、队列归档、人工批准/拒绝，以及可选发送的结果都会留下新记录。当前队列、`active_judgment`、`pending_approval`、`processed_events`、`reviews`、`last_conclusion` 和 `poll` 每次都从这些记录推导，数据库是唯一状态来源；评论与补丁全文仍在 `runs/<event_id>/` 文件中，库里只记路径和评论 SHA-256 等核验信息。SQLite 使用 Python 标准库 `sqlite3` 和 WAL，无需额外服务或新依赖。

| 命令 | 如何使用同一个事实库 |
| --- | --- |
| `enqueue` | 校验正文并追加入队事实；同 ID 同正文去重，正文冲突拒绝 |
| `watch` | 扫描当前队列，认领一轮并记录开始判断；成功后追加归档事实；`--loop` 等拍板事实出现后自动接续 |
| `poll` | 按各仓库 `poll.interval_seconds` 拉取线上新事件并入队；水位与结果为 `poll_updated` 事实 |
| `pi` / `demo` | 直接处理事件；提交判断时事务写入结论摘要、产物路径与待拍板记录 |
| `review` / `status` / MCP `get_pending_approval` | 从事实重建当前视图，不调用模型，也不替人拍板；review 给人读，status 给脚本 |
| `approve` / `reject` | 事务追加人工决定，解除待拍板；默认只留本地记录 |
| `doctor` | 只读诊断三把锁的持有进程是否存活，不删锁 |

**旧数据会一次迁移**：某状态目录的数据库不存在或事实表为空时，首次打开会校验并导入旧 `state.json`、`inbox/*.json` 和 `inbox-archive/*.json`，保留待拍板、未完成判断、已处理记录、队列及审批历史。同 ID 正文冲突、无效事件或链接逃逸会使整个导入回滚；修正备份来源后可重试空库。导入成功后旧文件原样保留，程序将它们视为只读备份，不再读取或改写；此后不要编辑旧 JSON 来改变运行状态。缺少可核验发送目标的旧轮次仍只能本地拍板。

未设置 `OSS_WATCH_VAR` 时，库在 `var/oss-watch.sqlite`，产物在 `var/runs/`；指定目录时二者都位于该目录内。**备份要同时复制数据库和 `runs/` 产物**，步骤与恢复见 [operations.md](operations.md)。

持久化与进程常驻是两回事：日志留在磁盘上，默认 `watch` 仍只派一轮并退出，新事件不会自动启动已退出的等待器；需要常驻时显式用 `watch --loop`。

本项目还没做公网 Webhook 接收端或 Autoloop 中台。在线读取默认关闭，不启动公网监听、不要求暴露本机。

## 批准后可选发评论

`approve` / `reject` **先在 SQLite 事务中追加本地拍板事实**。未开启发送或无凭据时，结果为 `delivery: not-sent`，并注明“未发出”。拒绝、模拟事件、版本适配都不会发送；补丁始终为 `patch_delivery: local-only`。

只有准备发送**真实 Issue/PR** 的讨论区评论时，在拍板的本机终端显式设置开关，并使用本机已配置的 GitHub 凭据：

```powershell
# 可先在本机检查登录状态；本项目不写入或打印 Token
gh auth status --hostname github.com
$eventId = (python demo.py status | ConvertFrom-Json).pending_approval.event_id
# 确认该 ID 对应真实 Issue/PR，且已审阅它的草稿，再继续
$env:OSS_WATCH_SEND_COMMENTS = '1'
python demo.py approve $eventId --note "批准这份评论"
Remove-Item Env:OSS_WATCH_SEND_COMMENTS
```

发送只使用配置白名单的 `owner/repo` 和原事件编号，调用 GitHub Issue Comments API（PR 也使用讨论区评论接口），不相信模型提供的 URL，不发送代码评审、不创建 PR、不合并。`delivery.mode: draft-only` 约束代理只产草稿；`OSS_WATCH_SEND_COMMENTS` 是人工 CLI 拍板后的独立开关，默认 `0`。MCP 没有发送工具，模型不能打开开关或拍板。

成功后记录 `delivery: sent` 和评论链接；请求前先落盘发送尝试。如果网络超时、进程中断或回执异常，可能为 `unknown` 或留下 `sending`，**不会自动重发**，同时会发一条通知提醒人工核对。请人工核对讨论区，保留原记录。重复 `approve` 会被拒绝；当前没有延后发送或自动重试命令。开关应在首次审批前开启。

GitHub **在线读取**由独立的 `OSS_WATCH_GITHUB=1` 控制，默认 `0`。发版主演示及所有模拟事件强制关闭在线读取；真实 Issue/PR 轮次只有显式开启后才允许查询 GitHub，需自行配置 `gh`。项目 MCP 继承启动器的读取开关，发送开关始终强制为 `0`。模拟工具读取无需网络；`poll` 轮询器复用这条只读通路，同样受此开关控制。

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

每个工具的完整边界见 [architecture.md](architecture.md) 的工具表。新事件格式为 `event_id`、白名单别名 `repository`、`type`、布尔 `fixture` 和正文对象：发版用 `release`（`tag`、`body`），`issues.opened` 用 `issue`，`pull_request.opened` 用 `pull_request`（正整数 `number`、`title`、`body`）；参见 `fixtures/`。所有正文都是不受信任的数据，不构成工具授权。历史快照按配置的 GitHub 仓库过滤，不串用其他项目的历史。