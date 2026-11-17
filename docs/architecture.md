# 各组件构成

> 本文从 README 拆出，按当前代码说明每个组件由什么组成、怎样交接。快速开始与演示见 [README](../README.md)，部署与排障见 [operations.md](operations.md)。

整条链路是「poll 轮询 / enqueue 投递 → 本地收件箱 → watch 等待器（可 `--loop` 常驻）→ 本机 headless Pi → MCP 读证据、写草稿 → 本地状态 → toast 通知 → 人工审批（review / approve / reject）→ 可选评论发送」。被值守仓库只提供源码和文档，GitHub 只读查询只补充线上信息；两者都不是补丁的写入目标。下面按当前代码说明每个组件由什么组成、怎样交接。

## 1. 本地收件箱：保存尚未处理的事件

收件箱由 `enqueue` 命令、`src/oss_watch/inbox.py` 和状态目录下的 `oss-watch.sqlite` 组成。`enqueue` 通过 `events.py` 校验事件后，在一个 SQLite 事务中把原事件作为入队事实追加到数据库，不再写入 `inbox/<event_id>.json`。队列按入队先后排列；一轮成功提交待拍板或识别为已处理后，追加归档事实并从当前队列移除，原事件与历史仍保留在库中。

每份事件都是 JSON 对象，用 `event_id` 标识事件，用 `repository` 指定 `config/watch.json` 中的白名单别名，`type` 决定正文对象；模拟事件显式带上布尔值 `fixture: true`。支持的正文如下，完整例子见 `fixtures/`：

| `type` | 正文对象与关键字段 |
| --- | --- |
| `release.published` | `release`，包含非空 `tag` 和字符串 `body`。 |
| `issues.opened` | `issue`，包含正整数 `number`、非空 `title` 和字符串 `body`。 |
| `pull_request.opened` | `pull_request`，包含正整数 `number`、非空 `title` 和字符串 `body`。 |

事件正文是不受信任的数据，不能授权工具操作。同 ID、同正文的排队投递不会重复写入，同 ID、不同正文会被拒绝。**`enqueue` 只负责写入数据库，不会启动或唤醒已经退出的 `watch`**；交接依靠正在扫描队列的等待器，或人之后重新运行它。

## 2. watch 等待器：从 SQLite 队列派出一轮判断

`python demo.py watch` 从 `demo.py` 进入 `src/oss_watch/cli.py`，由 `inbox.py` 的 `EventInbox.run()` 扫描 SQLite 中的当前队列，再交给 `pi_judgment.py` 准备原事件和提示词、启动 Pi。默认每秒扫描一次；空闲等待时不启动模型，也不访问 GitHub。`--once` 让空队列立即返回，不绕过审批门。

**Pi 默认以 headless（`-p --mode json`）运行**：输出写入 `runs/<event_id>/pi-output.jsonl` 与 `pi-stderr.log`，会话存 `runs/<event_id>/session/`，超过 `judgment.timeout_seconds`（默认 900 秒，`watch.json` 可配 30–7200）会按进程树终止并留下超时事实，之后用 `pi --resume-round` 接续。想观察或接管一轮时加 `--interactive` 进入交互 TUI。判断是否完成只看事实库中的待拍板记录，不解析模型输出。

默认每次运行最多派出一个新事件的模型轮次，完成后归档事件并退出；遇到已处理事件只归档，不再启动模型。若已有 `pending_approval` 或未完成的 `active_judgment`，等待器返回 `blocked` 并退出，后续事件留在队列。待拍板时先审批，未完成时先用 `pi --resume-round` 接续，再重新运行 `watch`。

加 `--loop` 则成为**本机常驻循环**：派一轮 → 停在待拍板并发通知 → 按 `--interval` 等待拍板事实出现 → 自动归档并接续下一条，队列空时继续等待。loop 只等待、**不代拍板**；判断失败或超时仍停下返回 `needs-resume`，等人接续后重新启动，不空转重试烧 Token。`--no-notify` 关闭通知（日志仍记录）。`Ctrl+C` 停止，`.watch.lock` 租约防止双开。它是本机前台/任务计划进程，不是公网中台，没有公网 Webhook 或自动重启服务。

## 3. 本机 Pi：读证据并提出本轮建议

Pi 是本机已有的程序，由 `watch` 或 `python demo.py pi` 拉起。启动器 `src/oss_watch/pi_judgment.py` 保存本轮事件和 `prompt.md`；项目级 `.pi/mcp.json` 连接 oss-watch MCP，`.pi/one-round.ts` 限制可调用工具，并在 `agent_settled` 时退出、关闭 MCP 子进程。**默认 headless（`pi -p --mode json --session-dir`）**，输出与会话落盘到 `runs/<event_id>/`，超时由看门狗按进程树终止；加 `--interactive` 才进入可观察、可接管的交互 TUI。一轮结束后 Pi 退出，不持续等待新任务。

Pi 负责搜索源码与历史、说明证据和不确定性、写评论与补丁草稿，再提交判断。启动参数只开放 `mcp__oss_watch__*`，显式排除 `record_approval`，扩展还会拦截越界工具调用。Pi 可以把轮次交到待拍板状态，**不能替人审批、打开发送开关或发送评论**。启动时的 `--approve` 只是信任项目配置，不是对本轮建议的业务批准。

## 4. MCP 工具：把有限能力提供给 Pi

MCP 由 `src/oss_watch/mcp_server.py` 注册工具，通过 `services.py` 组装只读仓库、GitHub、历史检索、状态与草稿服务。项目配置从仓库根目录启动 stdio 服务：`.venv\Scripts\python.exe -m oss_watch.mcp_server`。它与 CLI 使用同一套配置和 `OSS_WATCH_VAR`，共用同一个状态目录中的 SQLite 库交接状态，不需要额外部署中台。

服务共注册 **12 个工具**，其中 `record_approval` 对本轮 Pi 隐藏；下表列出全部工具及关键边界。

| 工具 | 作用 | 关键限制 |
| --- | --- | --- |
| `read_repository_file` | 分段读取已配置仓库的 UTF-8 文本，返回带行号的内容。 | 路径必须留在白名单仓库内，拒绝绝对路径和链接逃逸；隐藏文件（如 `.env`）及 `node_modules`、`lib`、`dist`、`build`、`coverage` 内的文件拒绝读取；每次最多 400 行，大文件会被拒绝。 |
| `search_repository` | 在指定仓库内做不区分大小写的定向文本检索，返回路径、行号和片段。 | 默认 `glob="*.ts"`、最多 30 条，可指定匹配范围，结果上限 100 条；隐藏文件/目录、依赖及 `lib`、`dist`、`build`、`coverage` 等构建产物在扫描前剪枝，不参与检索。 |
| `read_github_release` | 按标签读取已配置 GitHub 仓库的 Release，不传标签则读取 latest 接口。 | 当前实现始终需要 `OSS_WATCH_GITHUB=1` 和本机可用的 `gh`；不提供模拟发版快照回退，模拟发版正文已在本轮事件中。 |
| `read_github_issue` | 读取指定编号的 Issue；本轮匹配的模拟 Issue 返回本地事件快照。 | 本地快照必须匹配当前活动事件的仓库、类型和编号；其他查询需要 `OSS_WATCH_GITHUB=1` 和本机 `gh`。 |
| `read_github_pull_request` | 读取指定编号的 PR；本轮匹配的模拟 PR 返回本地事件快照。 | 与 Issue 相同，只对本轮匹配的模拟事件免联网；其他查询需要显式开启在线读取并使用本机 `gh`。 |
| `search_github_history` | 用 GitHub 搜索已配置仓库的历史 Issue/PR，最多返回 10 条结果。 | 需要在线读取开关和本机 `gh`；仅查询，不写评论或改动 GitHub 内容。 |
| `search_history_and_docs` | 检索 `fixtures/history-snapshot.json` 附带的公开历史快照，以及目标仓库根目录 Markdown。 | 本地关键词检索，不用向量库；历史按配置的 GitHub 仓库匹配，快照状态不能当作线上当前状态。 |
| `write_comment_draft` | 把本轮拟回复保存为 `runs/<event_id>/comment.md`。 | 仓库和目标编号必须与本轮事件一致，编号可留空由程序确定；永远只保存、不发送，已提交的 Pi 草稿不可改写。 |
| `write_patch_draft` | 把补丁或改动说明写入本轮目录，通常命名为 `patch.md`。 | 只接受单层 `.md` 文件名，不覆盖保留产物；无法可靠给出补丁时应写原因，绝不向目标仓库应用补丁。 |
| `get_pending_approval` | 查看活动轮次、已有草稿、当前待拍板事件、产物路径和最近审批记录。 | 只读状态总览，最近审批最多返回 20 条；历史记录要按 `event_id` 区分，查看不代表批准。 |
| `submit_judgment` | 提交 Pi 的判断、理由、源码证据、待验证项和置信度，写出 `conclusion.json`。 | 只能提交当前活动事件，须先写评论和补丁两份草稿；逐条核对源码路径、行号及原文后停在 `pending_approval`，不审批、不发送。 |
| `record_approval` | 在本地状态中记录指定事件的 `approve` / `reject` 和备注。 | 只接受当前待拍板事件，永不触发发送；**对本轮 Pi 隐藏并禁止调用，人工审批只走 CLI**。 |

**MCP 没有发送工具，模型不能打开开关或替你拍板。** 项目 MCP 继承启动器的读取开关，发送开关强制为 `0`。启动器只允许显式开启读取的真实 Issue/PR 轮次联网，模拟事件和发版轮次强制关闭在线读取；因此模拟演示依赖本地事件、源码与历史即可完成。

## 5. 本地状态与草稿：让进程退出后仍有据可查

这一层由 `src/oss_watch/state.py` 和状态目录下的 `oss-watch.sqlite` 组成。默认状态目录为 `var/`，也可用 `OSS_WATCH_VAR` 指定项目内子目录；CLI、Pi 启动器和 MCP 必须指向同一个目录。库中的追加式事实日志保存原事件、活动轮次、待拍板、去重记录和审批/发送结果，当前视图每次从日志推导；`runs/<event_id>/` 保存 `prompt.md`、`conclusion.json`、`comment.md` 与通常命名为 `patch.md` 的补丁草稿，实际路径以状态中的 `artifacts` 为准。

`conclusion.json` 说明判断、理由、源码证据、置信度和待验证项；两份 Markdown 分别供人审阅拟回复和拟改动。它们都在人工拍板前写好，提交后冻结 Pi 草稿，并保存评论摘要供发送前核对。状态以短事务追加事实，写锁、Pi 租约和等待器租约拒绝并发操作。**数据库持久化不依赖 `watch` 或 Pi 一直运行**；下次 CLI 从库中重建当前状态，未完成轮次也据此接续。旧的 `state.json`、`inbox/` 和 `inbox-archive/` 会在首次打开空库时一次性导入，之后只作只读备份。

## 6. 审批门：由人按事件 ID 作决定

审批由 CLI 的 `approve` / `reject`、`src/oss_watch/approval.py` 和 `state.py` 协作完成。人先用 **`python demo.py review`** 阅读待拍板轮次（`src/oss_watch/review.py` 渲染结论、理由、源码证据、待验证项和产物路径，末尾给出填好 event_id 的拍板命令，复制即用；`--open` 用默认编辑器打开两份草稿，`--event <id>` 回看历史），或用 `status` 看原始 JSON。review **只读、不内嵌拍板动作**；再执行 `python demo.py approve <event_id>` 或 `python demo.py reject <event_id>`，两条命令都必须带上当前待拍板的事件 ID，缺失、不匹配或重复审批会被拒绝。

审批先在 SQLite 事务中追加本地拍板事实，记录决定和备注、清除待拍板，再交给评论发送组件检查外发条件。它不重新生成草稿，不应用补丁，也不启动下一轮模型。拒绝会保留原产物；批准后的补丁仍为 `patch_delivery: local-only`。MCP 的 `record_approval` 只做本地记录，不连接这个发送步骤，而且不向本轮 Pi 开放。

## 7. 被值守仓库：只读证据来源

白名单由 `config/watch.json` 的 `repositories` 定义，每个别名对应本地 `root`、GitHub `owner/repo` 和只读约束。`src/oss_watch/config.py` 校验配置与路径，`repository.py` 提供文件读取和检索；事件只能引用已配置的别名，配置 `writable: true` 会被拒绝。本地状态与草稿目录也不得和任何被值守仓库重叠。

这一层把源码、版本记录和文档交给 Pi 判断，始终不接收写入。包括 `dsh-reasoning-effort` 在内的所有目标都禁止修改文件、应用补丁、提交、推送、创建修复分支或修复 PR。**补丁无论是否批准，都只留在 oss-watch 本地**；可选的讨论区评论发送不改变这个边界。

## 8. 可选 GitHub 读取与评论发送：两条独立通路

只读通路由 `src/oss_watch/github.py` 和本机 `gh api` 组成，受 `OSS_WATCH_GITHUB=1` 控制，默认关闭。它向 MCP 返回 Release、Issue、PR 或历史搜索信息，不改变线上内容；匹配本轮的模拟 Issue/PR 则直接返回本地快照。判断启动器还会按事件类型收紧读取开关，具体限制见上面的工具表。

线上事件进入本地队列靠 **`poll` 轮询器**（`src/oss_watch/poller.py`）：按各仓库在 `watch.json` 配置的 `poll.interval_seconds`（60 秒–30 天，默认 1800）与 `poll.enabled`，只读拉取新建 Issue/PR 与 Release，生成确定性事件 ID 走既有 `enqueue` 幂等入队。水位与轮询结果落为事实，失败退避且不推进水位；首次启用不回溯存量，跳过 bot。默认 `--once` 供任务计划每分钟心跳调用（到期才真正拉取），`--loop` 常驻按 `--tick` 心跳，`--repo` / `--force` 供定向补拉。需要 `OSS_WATCH_GITHUB=1` 与本机 `gh` 登录。

发送通路由人工 CLI 审批和 `src/oss_watch/delivery.py` 组成，受独立的 `OSS_WATCH_SEND_COMMENTS=1` 控制，默认也关闭。只有首次人工批准真实 Issue/PR，且本机凭据、配置仓库、原事件目标与冻结的评论草稿都核验通过后，才尝试向该 Issue/PR 的讨论区发送一条评论。**拒绝、模拟事件和发版适配都不会发出**，也没有补丁发送、代码评审、创建修复 PR 或合并能力。

发送前先持久化尝试记录，成功后记录评论链接；超时或回执不明时可能留下 `unknown` / `sending`，不会自动重试。单独打开读取开关不会允许发送，发送开关也不会解除目标仓库只读约束；模型通过 MCP 无法进入人工发送通路。