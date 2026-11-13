# 写在前面
本仓库实现了一个简单的Agent开发loop，一个以事件驱动的一轮值守loop
# 开源值守

本地事件叫醒一轮**本机交互 Pi**：通过 MCP 读取白名单仓库、定向搜索代码和历史，提出建议、写评论与补丁草稿，然后退出等人拍板。支持模拟发版、新 Issue 和 PR；不用向量库，不做通用问答，不依赖 Agent Autoloop 中台或其跟进 Issue 配方。

**所有被值守仓库始终只读**，包括 `dsh-reasoning-effort` 和以后加入 `config/watch.json` 的任何项目。禁止改文件、应用补丁、提交、推送、创建修复分支或修复 PR；**版本补丁即使批准，也永远只留在 oss-watch 本地草稿中**。人工批准真实 Issue/PR 的评论后，只有显式开启发送开关并有本机凭据，才会发送一条讨论区评论。默认不发送，不自动合并。

## 安装

要求 Python 3.11+、本机 Pi（已在 1.5.2 验证）和可用的模型配置。默认只读目标为 `E:\code\插件\dsh-reasoning-effort`，可在 `config/watch.json` 配置其他白名单仓库；`writable: true` 会被拒绝。模型与 GitHub 登录由用户在本机管理，不要将密钥、Token 或登录资料放入仓库。

```powershell
Set-Location E:\code\oss-watch
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

已有虚拟环境且已安装依赖时，不必重复安装。下面的命令都在项目虚拟环境中执行；Pi 仍使用本机已有的模型配置。这次模拟演示**不需要 GitHub Token，也不必开启在线读取或评论发送**。

## 一次完整演示

这次只处理一条模拟 Issue：`fixtures/issue-menu-overflow.json`，事件 ID 为 `demo-issue-menu-overflow-20261113`。目标是走完「投递 → 本机 watch 拉起交互 Pi → 只读判断并写本地草稿 → 查看 → 人工拍板」，不是验证反馈中的问题已经复现。

### 1. 准备两个终端

打开两个 PowerShell，分别称为终端 A 和终端 B。**两个终端都执行下面整段命令**，保持同一工作目录、同一虚拟环境和同一个新的状态目录：

```powershell
Set-Location E:\code\oss-watch
. .\.venv\Scripts\Activate.ps1
$env:OSS_WATCH_VAR = 'E:\code\oss-watch\var\readme-demo'
$env:OSS_WATCH_GITHUB = '0'
$env:OSS_WATCH_SEND_COMMENTS = '0'
```

如果 PowerShell 不允许激活脚本，可把后文每条 `python demo.py ...` 换成 `.\.venv\Scripts\python.exe demo.py ...`，其余设置照常执行。

**不要直接用默认 `var/` 演示**：那里可能已有待拍板记录，导致 `watch` 返回 `blocked`；也可能已有历史审批，`status` 会把旧的 `reviews` 和新的待办一起列出，容易看错。保留原目录和记录即可，不要清空。如果 `var/readme-demo/` 也用过，请在两个终端都换成同一个未使用的项目内子目录，后文读文件的路径也要一起换。

环境变量只影响当前终端和它启动的子进程，不会自动传给另一个已打开的终端。CLI 与它派出的 MCP 会继承同一个 `OSS_WATCH_VAR`。状态目录按需创建，必须位于 oss-watch 项目内，不能与任何被值守仓库重叠。

### 2. 终端 A：启动本地等待器

```powershell
python demo.py watch
```

屏幕会先显示：

```text
本地值守已启动；等待事件文件，派一轮交互 Pi 后退出。Ctrl+C 停止等待。
```

此时只是一个本机前台进程在扫描 `E:\code\oss-watch\var\readme-demo\inbox\`，不是常驻中台。没有事件时不会启动模型、不会访问 GitHub；先让它保持等待，再去终端 B 投递。

### 3. 终端 B：投递模拟 Issue

```powershell
python demo.py enqueue fixtures/issue-menu-overflow.json
```

终端 B 返回 `status: queued`、事件 ID 和收件文件路径。命令校验事件后，通过临时文件原子写入：

```text
var/readme-demo/inbox/demo-issue-menu-overflow-20261113.json
```

**`enqueue` 不会自动启动或唤醒一个已经退出的 `watch`**，它只投递文件。这次终端 A 已在扫描，所以会发现文件、取队首并拉起一轮可交互 Pi。

### 4. 观察终端 A：Pi 读取、判断、写草稿

终端 A 会进入本机 Pi TUI，可以观察工具调用并与 Pi 交互。它通过项目 MCP 读取模拟 Issue `100001` 的**本地事件快照**，定向搜索白名单仓库的真实源码和本地历史，再提出中文建议。这里没有 `pi -p`、一次性后台子 agent 或自动审批。

**被值守仓库始终只读**。本轮只能把建议写进 oss-watch 的本地目录，不会修改 `E:\code\插件\dsh-reasoning-effort`，不会应用补丁、提交、推送或建立修复 PR。

正常完成时，Pi 会用草稿工具写出 `comment.md`（拟回复）和 `patch.md`（拟补丁，或无法可靠产出补丁的原因），再调用 `submit_judgment`，写出 `conclusion.json` 并停在 `pending_approval`。终端会显示结论摘要、产物路径及待拍板状态，具体建议由模型判断，不保证每次措辞相同。

一轮结束后 Pi 和 `watch` 都会退出，终端 A 回到 PowerShell；等待人拍板期间不会空转烧 Token。**不用在 Pi 里找审批按钮**：`record_approval` 对本轮模型隐藏，人工拍板要用后面的 CLI 命令。如果没有完成提交，先按「常见问题」中的接续方式处理，不要当作已成功。

### 5. 终端 B：用 status 找到这次待办

```powershell
python demo.py status
```

`status` 是总览，**不是草稿文件本身，也不只是本次结论**。首先核对 `pending_approval.event_id` 是否为 `demo-issue-menu-overflow-20261113`，再看它的 `conclusion` 和 `artifacts`。正常提交后，`active_judgment` 应为空，`pending_approval` 才是现在等你决定的事件。

`last_conclusion` 是最近一次已提交的结论，`reviews` 是最近的人工审批历史。如果看到其他事件的 `delivery_reason: 已拒绝，未发出`，那只属于它所在的那条历史审批；按同一条记录里的 `event_id` 区分，不能把它当成本次建议或本次已被拒绝。全新状态目录在第一次拍板前应没有这类审批历史。

### 6. 亲眼打开结论和两份草稿

```powershell
Get-Content -Encoding UTF8 var/readme-demo/runs/demo-issue-menu-overflow-20261113/conclusion.json
Get-Content -Encoding UTF8 var/readme-demo/runs/demo-issue-menu-overflow-20261113/comment.md
Get-Content -Encoding UTF8 var/readme-demo/runs/demo-issue-menu-overflow-20261113/patch.md
```

`conclusion.json` 是结论本体，包含判断、理由、源码证据、置信度和待验证项；`comment.md` 是准备给讨论区看的回复；`patch.md` 是本地补丁建议或不应改动的说明。路径以本轮 `status` 返回的 `artifacts` 为准；如果换过状态目录，也要换上面命令里的目录。

三份文件在人工拍板前就已写好。请检查是否把模拟反馈误写成真实线上事实、是否把静态分析误写成已复现，以及还缺哪些浏览器、视口或 DSH 版本信息。

### 7. 带上 event_id，批准或拒绝

看完后，在终端 B **二选一**执行：

```powershell
python demo.py approve demo-issue-menu-overflow-20261113 --note "先保留本地建议"
# 或拒绝，不要在批准后再对同一事件执行拒绝
# python demo.py reject demo-issue-menu-overflow-20261113 --note "信息不足，暂不跟进"
```

`event_id` 是必填参数，要与 `pending_approval.event_id` 一致，不能只运行 `python demo.py approve`。`approve` **不会再写一遍草稿**；这次只更新 `var/readme-demo/state.json` 中的人工决定及发送结果。默认 `delivery: not-sent`，补丁为 `patch_delivery: local-only`。拒绝会记录 `delivery_reason: 已拒绝，未发出`，原草稿仍保留。

这是 `fixture: true` 的模拟事件，**即使误开评论发送开关，也不会向真实讨论区发评论**。补丁即使批准也永远只留在 oss-watch 本地，不会应用到被值守仓库，不会自动开修复 PR。

### 8. 再看一次结果

```powershell
python demo.py status
```

现在 `pending_approval` 应为空，`processed_events` 中这条事件应为 `approved` 或 `rejected`，`reviews` 中会新增带相同事件 ID 和备注的审批记录。结论、评论和补丁文件仍在原处；批准不会自动启动下一轮 `watch`。

### 9. 可选：继续下一条，并观察排队

另外两份 fixture 也能走同样流程，但一次只演示一条，不要把所有命令一次性投递：

| 模拟事件文件 | event_id |
| --- | --- |
| `fixtures/release-v0.3.0-rc.1.json` | `demo-dsh-v0.3.0-rc.1` |
| `fixtures/pr-menu-position.json` | `demo-pr-menu-position-20261113` |

第一条拍板后，在终端 A 再运行 `python demo.py watch`，终端 B 投递下一条，例如：

```powershell
python demo.py enqueue fixtures/pr-menu-position.json
```

等 PR 这一轮停在 `pending_approval` 后，**先不拍板**，在终端 B 投递剩下的发版事件：

```powershell
python demo.py enqueue fixtures/release-v0.3.0-rc.1.json
python demo.py watch --once
python demo.py status
```

这时 `watch` 会返回 `blocked` 并退出。`pending_approval.event_id` 仍是 `demo-pr-menu-position-20261113`，发版文件只出现在 `queued_files`，不会覆盖 PR 草稿。先阅读 PR 的产物并用它的事件 ID 拍板，再在终端 A 重新运行 `watch`，才会处理排队的发版事件。模拟 PR `100002` 和发版 `v0.3.0-rc.1` 同样不是线上事件或官方公告。

**另外两个入口怎么选？** `python demo.py pi` 会跳过收件箱，直接在当前终端拉起一轮 Pi，默认处理模拟发版；也能显式传入 Issue/PR 文件。`python demo.py demo` 是免模型的固定规则对照与回归，只接受发版事件，不是上面的智能演示。它们共享去重和审批状态，比较时请使用不同的状态目录。

## 常见问题

### 1. 这套判断是固定程序，还是大模型在判断？

`watch` 拉起的 Pi 和直接运行的 `pi` 都由大模型读证据、写建议。程序负责事件校验、只读边界、状态、去重和审批门。只有 `demo` 子命令使用固定规则，只接受发版事件，不能把它的输出当作模型判断。

### 2. status 就是草稿吗？返回的就是结论吗？

它是总览，包含活动轮次、待拍板记录、最近结论、已处理事件、最近 20 条审批和收件文件。它会带出结论，未完成轮次还可能显示 `active_drafts`，但要完整审阅本轮产物，请按 `pending_approval.artifacts` 打开 `conclusion.json`、`comment.md` 和 `patch.md`。

### 3. 执行 approve 之后才会开始写草稿吗？

不是。Pi 必须先写评论和补丁草稿，再提交判断，最后才轮到人拍板。`approve` 记录人工决定，符合外发条件时才尝试发评论，不重新生成或改写草稿。

### 4. approve 为什么报错缺少 event_id？

因为事件 ID 是必填参数。先看 `status` 的 `pending_approval.event_id`，再执行 `python demo.py approve demo-issue-menu-overflow-20261113 --note "先保留本地建议"`。`reject` 同样需要 ID；没有待拍板记录或 ID 不匹配也会报错。

### 5. 我还没拍板，为什么就看到「已拒绝，未发出」？这是这次的建议吗？

这句话是某条审批记录的 `delivery_reason`，不是模型建议。`reviews` 会保留历史，先看这句话所在记录的 `event_id`，再与当前 `pending_approval.event_id` 对照。其他事件被拒绝，不代表这次也被拒绝；如果用的是全新目录却看到旧审批，先核对两个终端的 `OSS_WATCH_VAR`。

### 6. 项目是不是还没有做持久化？「watch 不持久化」是什么意思？

已经有本地持久化：`state.json` 保存状态和审批历史，`runs/` 保存提示词、结论和草稿，收件与归档也在磁盘上。「watch 不持久化」容易误导，更准确的说法是它不会作为服务一直运行：派一轮并完成提交后就退出，数据仍保留。

### 7. 投递器把事件放进 inbox，会自动唤醒 watch 吗？

不会启动一个尚未运行的 `watch`。已经在等待的 `watch` 会扫描到文件并派一轮 Pi；没有等待器时，文件只留在收件箱，等你下一次运行 `watch`。

### 8. 「持久化」和「watch 正在跑」是一回事吗？

不是。持久化指数据落盘，退出终端后仍在；正在跑指此刻有进程扫描收件箱。磁盘上有待办，并不代表有进程在处理它。

### 9. 如果想更自动，是不是必须让 watch 一直开着？

如果希望等待期间文件一到就处理，需要有 `watch` 正在跑；也可以投递后自己启动它。当前每次最多派一个新模型轮次，待拍板或未完成时会退出，不会一直循环替你处理和审批。自动重启、系统服务托管还没做，实际判断仍需要可交互的本机终端。

### 10. watch 是哪个程序实现的？

是本项目的 Python CLI：`demo.py` 进入 `src/oss_watch/cli.py`，再调用 `src/oss_watch/inbox.py` 的 `EventInbox.run()`。默认每秒扫描一次本地文件，再由 `pi_judgment.py` 启动本机 Pi，不是 GitHub 或 Pi 自带的常驻服务。

### 11. 什么东西替代了中台？为什么不引入中台？

当前用本地收件箱、Python 等待器、JSON 状态和人工 CLI 拍板串起这条小闭环，模型判断交给本机 Pi。范围是本机维护建议，用这些就能保留事件、草稿和决定，少一套服务需要部署维护。Agent Autoloop 中台及跟进 Issue 配方没有接入，也没有在本项目中实现。

### 12. 公网 Webhook 是什么？本项目有没有做？

它是一个可从公网访问的接收地址，让 GitHub 在事件发生时主动发送 HTTP 请求。本项目的公网接收端还没做，不启动公网监听，也不要求暴露本机。将来若自行实现，需要验证请求、转换事件格式，再投递到本地收件箱。

### 13. 最简单的投递器是不是定时拉 Issue/PR？一天一次可以吗，需要常驻服务吗？

可以另写脚本定时拉取，转换成这里支持的事件，再调用 `enqueue`。能接受延迟就可以一天一次，用任务计划定时执行，不必常驻。**内置定时轮询器还没做**；脚本还要自己记录拉取进度、生成稳定的事件 ID 并处理重复。投递完成仍不会启动 `watch`，这一步需要另外安排。

### 14. 本地收件箱在哪？

默认是 `E:\code\oss-watch\var\inbox\`。本演示是 `E:\code\oss-watch\var\readme-demo\inbox\`，即 `$env:OSS_WATCH_VAR` 下面的 `inbox/`；`status` 的 `inbox` 字段会显示实际路径。

### 15. 批准之后会不会改被值守仓库，或自动开修复 PR、自动发评论？

不会改被值守仓库，也不会开修复 PR，补丁永远只在 oss-watch 本地。评论默认也不发；只有真实 Issue/PR、首次人工 `approve`、显式开启 `OSS_WATCH_SEND_COMMENTS=1` 且本机凭据和草稿校验都通过，才尝试发送讨论区评论。拒绝、模拟事件和版本适配一律不发。

### 16. 为什么演示要新建 OSS_WATCH_VAR？

为了保留日常数据，也避免旧待办阻塞和历史审批混淆。请在两个终端用同一个未使用的项目内子目录，而不是各设一个目录。不要为了演示删掉默认 `var/`。

### 17. 只有一个终端能不能演示？

可以。先设置工作目录、虚拟环境和新状态目录，先 `enqueue`，再 `watch`；队列已有文件时会直接处理，退出后继续 `status` 和拍板。两个终端只是方便亲眼看到「正在等待 → 文件到达 → 拉起 Pi」。

### 18. 同一事件再 enqueue 一次会怎样？

队列里同 ID、同正文的事件不会重复写入。已经处理过的同一事件再次入队，待审批门放行后，`watch` 会识别为已处理并归档，不再启动模型或覆盖草稿。同 ID 正文不同会在投递或处理校验时被拒绝，不能用改正文的方式覆盖旧记录。

### 19. 模型中断，或者没有 submit_judgment，怎么接续？

原事件和已有草稿会保留，`active_judgment` 会阻止新轮次。修复模型问题后，在同一状态目录运行 `python demo.py pi --resume-round`，它读取保存的原事件，不会把 Issue/PR 误接成默认发版。接续新开交互上下文，使用原提示词和已有草稿，不依赖旧聊天记录，也不会覆盖已有草稿。未提交成功时不能直接 `approve`。

### 20. 碰到 state.lock、state.pi.lock、state.watch.lock 怎么办？

它们分别是短暂状态写锁、Pi 进程租约和等待器租约，用来拒绝并发操作。先检查是否有相关 CLI、Pi 或 `watch` 正在运行；正常运行时不要动锁。若异常退出留下锁，确认对应进程已经退出，再人工移走当前状态目录中对应的锁文件，保留 `state.json` 和产物，然后按状态接续或重新启动等待器。

### 21. 打开发送开关后，模拟事件或发版适配会不会发出去？

不会。`fixture: true` 的模拟事件和版本适配都禁止外发，不受发送开关影响。这个开关也不会让补丁被应用或创建修复 PR。

### 22. 在线读取开关和发送开关是不是一回事？

不是。`OSS_WATCH_GITHUB=1` 允许真实 Issue/PR 轮次的 GitHub 只读查询；`OSS_WATCH_SEND_COMMENTS=1` 只控制人工批准后的评论发送，默认都为 `0`。模拟事件和发版交互轮次强制关闭在线读取；Pi 派出的 MCP 强制关闭发送，模型不能自己批准或外发。

### 23. 已有待拍板时再跑 watch，为什么是 blocked？

因为一次只允许一条待拍板或未完成判断，避免新事件覆盖你正在看的内容。`blocked` 会带上 `blocking_event_id`，队列仍保留。待拍板就先按 ID 批准或拒绝；未完成就先 `pi --resume-round`。处理完后要重新运行 `watch`。

### 24. 批准时没开发送开关，以后再 approve 能补发吗？

不能。首次审批已经清除了待拍板记录，重复 `approve` 会被拒绝。当前还没有延后发送或自动重试命令；若确实准备发送真实评论，应在第一次审批前开启发送开关。出现 `unknown` 或遗留 `sending` 时先人工核对讨论区，不要盲目重发。

### 25. 为什么文件已经入队却没有处理？

先用同一状态目录运行 `status`：确认 `inbox` 和 `queued_files`，再看是否有阻塞轮次、等待器是否已经退出。队列按文件名排序，不按投递时间；坏 JSON 或无效队首文件会让等待器报错退出并保留文件，需要人工修正再启动。不要直接逐段写入正在被扫描的 `.json`。

## 收件箱、状态与接续参考

`enqueue` 用临时文件原子投递到 `<状态目录>/inbox/<event_id>.json`。自行编写投递器时，也应先写 `.tmp`，写完再原子改名为 `.json`。文件按文件名排序；成功完成一轮或识别为已处理后，移到 `inbox-archive/`。每次 `watch` 最多派一个新模型轮次；`--once` 只是让队列为空时立即退出，不会绕过审批或禁用模型：

```powershell
python demo.py watch --once
```

扫描间隔可以用 `--interval` 调整，单位是秒，默认 `1.0`，允许 `0.1` 到 `60`。已有待拍板或未完成判断时，`watch` 返回 `blocked` 并退出，不自动接续。拍板后需重新启动，未完成时使用：

```powershell
python demo.py pi --resume-round
```

不传文件时，它读取状态保存的原事件；也可以显式提供相同的事件文件。接续使用原提示词和已有草稿。Pi 提交后，工具禁止改写该轮草稿；发送前再次核对评论内容摘要。状态写锁、Pi 租约和等待器租约拒绝并发启动；异常遗留锁的处理见常见问题。

`state.json` 保留每个新轮次的原事件、草稿路径、冻结的评论摘要及审批/发送结果。旧版状态无需清空，缺少发送目标的旧轮次只能本地拍板。未设置 `OSS_WATCH_VAR` 时，状态在 `var/state.json`，产物在 `var/runs/`。需要备份时，只复制本项目的 `var/`，不必删除或移走原目录：

```powershell
$backup = Join-Path $env:TEMP ('oss-watch-var-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Copy-Item -LiteralPath .\var -Destination $backup -Recurse
```

本项目还没做公网 Webhook 接收端、内置定时轮询器或 Autoloop 中台。在线读取默认关闭，不启动公网监听、不要求暴露本机。

## 批准后可选发评论

`approve` / `reject` **先更新本地 JSON**。未开启发送或无凭据时，结果为 `delivery: not-sent`，并注明“未发出”。拒绝、模拟事件、版本适配都不会发送；补丁始终为 `patch_delivery: local-only`。

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

MCP 冒烟真实启动 stdio，在隔离状态目录调用工具并走到本地审批；Pi SDK 探针验证项目配置、工具限制和退出扩展，不调用模型、不改全局配置。它们与单元测试**不等于真实模型判断验收**。完整交互验收请在本机终端按「一次完整演示」执行，观察 Pi 的真实读取、草稿和 `pending_approval`，再人工核对结论。运行产物、草稿与状态都在忽略的 `var/` 中。
