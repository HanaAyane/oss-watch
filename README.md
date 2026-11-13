# 开源值守

面向个人开源仓库的本地事件驱动维护助手。**智能判断交给本机 Pi**：模拟发版叫醒一轮模型，Pi 通过 MCP 只读检查真实 `dsh-reasoning-effort`，独立判断、写草稿，然后退出，等人拍板。

`python demo.py demo` 保留为固定规则对照和回归，**不是大模型判断**。本期不接入 Agent Autoloop，也不新增固定适配规则。

## 主演示：在终端启动交互 Pi

要求：Python 3.11+、本机 Pi 1.5.2 和已可用的模型配置；目标仓库默认位于 `E:\code\插件\dsh-reasoning-effort`。模型登录由用户在本机管理，本项目不保存登录配置。首次安装：

```powershell
Set-Location E:\code\oss-watch
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

若本项目 `var/` 已有上一期 `demo-dsh-v0.3.0-rc.1` 待拍板，先检查并**备份移走本项目的整个 var 目录**。不要操作被值守仓库的文件。例如在仓库根目录执行：

```powershell
python demo.py status
$backup = Join-Path $env:TEMP ('oss-watch-var-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Move-Item -LiteralPath (Join-Path $PWD 'var') -Destination $backup
```

开始一轮演示：

```powershell
python demo.py pi
# 等价地显式指定同一份模拟发版：
# python demo.py pi fixtures/release-v0.3.0-rc.1.json
```

这是当前终端内可交互、可观察工具调用的 Pi TUI，**没有使用 `pi -p`**，没有后台子 agent。启动器会：

1. 校验事件和去重，确认没有待拍板或未完成的轮次，将事件和提示词写入本项目 `var/`，状态为 `judging`。
2. 以仓库根目录为 cwd 启动 `pi --approve`，信任项目本地配置，并将完整模拟事件作为初始消息传入。
3. Pi 通过 MCP 读取真实源码、搜索历史，自行给出中文结论；评论和补丁只写本地草稿。证据不足时明确写出原因，补丁文件可保存无法可靠产出补丁的说明。
4. Pi 调用 `submit_judgment`，服务器核对源码引用、检查两份草稿后保存结论，转为 `pending_approval`。这里不使用固定规则生成结论或补丁。
5. 项目扩展在 `agent_settled`（包括 Pi 自身重试结束后的边界）请求退出，关闭 MCP 子进程；终端回到 CLI 并显示产物路径。等拍板期间没有 Pi 进程空转。

fixture 中的 `v0.3.0-rc.1` 和菜单结构变化是**模拟数据，并非 DeepSeek Harness 官方公告**。判断属于静态检查，实际新版兼容性仍需人工运行时验证。CLI 不预设模型必须得出哪个结论。

### 查看产物与人工拍板

```powershell
python demo.py status
Get-Content -Encoding UTF8 var/runs/demo-dsh-v0.3.0-rc.1/conclusion.json
Get-Content -Encoding UTF8 var/runs/demo-dsh-v0.3.0-rc.1/comment.md
Get-Content -Encoding UTF8 var/runs/demo-dsh-v0.3.0-rc.1/patch.md
python demo.py approve demo-dsh-v0.3.0-rc.1 --note "先保留草稿"
# 或拒绝：
# python demo.py reject demo-dsh-v0.3.0-rc.1 --note "暂不跟进"
```

`approve` / `reject` 仅更改本项目 `var/state.json` 的审批记录，**即使批准也不发送**。再次运行同一事件返回 `already_processed`；上一轮未审批时，新事件不能开始。Pi 提交后的草稿不可变更。

若模型报错、用户中断或一轮未调用 `submit_judgment`，CLI 会报告未完成，保留 `active_judgment` 和已有草稿，阻止新事件及规则对照启动。修复本机模型问题后，显式接续同一事件：

```powershell
python demo.py pi --resume-round
```

接续使用原事件和已有草稿，启动新的交互上下文，不依赖上一段 Pi 聊天记录。可先用 `python demo.py pi --prepare-only` 只准备事件与提示词，再用 `--resume-round` 启动。不同正文的同 ID 事件不能接续。短暂状态写锁与 Pi 进程租约防止并发写入/启动；异常断电留下锁时，确认相关进程已退出后才移走 `var/state.lock` 或 `var/state.pi.lock`。

### 项目 MCP 配置与工具自动执行

实际配置是 **`.pi/mcp.json`**；根目录 `pi.mcp.json` 是等价模板。stdio 使用 `.venv\Scripts\python.exe -m oss_watch.mcp_server`，cwd 为仓库根，环境显式设置 `OSS_WATCH_GITHUB=0`。无需修改 `~/.pi/agent/mcp.json`。

Pi 只加载受信任项目的本地配置，因此必须使用 **`pi --approve`** 或在交互 Pi 中保存项目信任。`--approve` 是信任项目文件，**不是业务审批**。Pi 1.5.2 默认执行工具时不逐条询问，没有需要另配的 `autoApprove` 开关，参见 [Pi 安全文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md) 和 [MCP 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/mcp.md)。

启动器通过以下选项限制本轮工具；关闭其他扩展、技能、提示词和上下文文件发现，仅显式加载内置 MCP 与本项目的一轮退出扩展：

```text
--approve --no-extensions -e builtin:mcp -e .pi/one-round.ts
--no-skills --no-prompt-templates --no-context-files
--tools mcp__oss_watch__* --exclude-tools mcp__oss_watch__record_approval
```

项目配置也将 `record_approval` 设为 hidden，扩展额外拒绝该工具和其他命名空间的工具调用。只读和草稿工具自动执行，人工拍板继续使用 CLI；没有发送工具。Pi 仍会读取用户模型设置，并可能连接已启用的全局 MCP，但本轮不向模型开放这些服务的工具；不改写用户全局配置。

## 固定规则对照

```powershell
python demo.py demo
```

这条路径无需模型、依赖或网络；使用同一 fixture 检查既定适配契约，按固定规则生成结论及补丁。它共享事件去重与审批状态，所以不能与 Pi 用同一份 `var/` 重复演示。比较两条路径前分别备份移走本项目的 `var/`，保留各自产物。

## MCP 能力与边界

保留原有 11 个工具，增加 `submit_judgment`；共 12 个服务端工具，本轮 Pi 可见 11 个（人工审批记录工具隐藏）。

| 能力 | 工具 |
| --- | --- |
| 只读源码、定向搜索 | `read_repository_file` / `search_repository` |
| 可选 GitHub 只读查询 | `read_github_release` / `read_github_issue` / `read_github_pull_request` / `search_github_history` |
| 本地历史和文档检索 | `search_history_and_docs` |
| 只写本项目草稿 | `write_comment_draft` / `write_patch_draft` |
| 提交模型结论，停在审批门 | `submit_judgment` |
| 当前轮次及审批状态 | `get_pending_approval` |
| 本地审批记录，本轮 Pi 隐藏 | `record_approval` |

`config/watch.json` 限定仓库白名单、`writable: false` 和 `delivery.mode: draft-only`。读取拒绝路径越界；没有修改目标仓库、发送评论、推送目标分支或创建目标 PR 的实现。GitHub 在线读取默认关闭，主演示强制关闭；独立使用在线只读工具需显式设置 `OSS_WATCH_GITHUB=1` 并自行配置本机 `gh`。密钥与登录资料不进入项目。

运行产物均在忽略的 `var/`：`state.json` 保存去重、活动轮次和审批历史；`runs/<事件 ID>/` 保存 `prompt.md`、模型的 `conclusion.json`、`comment.md` 和 `patch.md`。补丁从不应用到目标仓库。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src demo.py tests
.\.venv\Scripts\python.exe tests/mcp_smoke.py

# 不调用模型的 Pi 实机探针（Windows npm 全局安装示例）：
$piPackage = Join-Path (Split-Path (Get-Command pi).Source) 'node_modules/@earendil-works/pi-coding-agent'
node tests/pi_smoke.mjs $piPackage
```

Python 测试使用临时源码副本验证规则回归、模型判断提交、源码引用校验、草稿冻结、事件去重、未审批阻塞、显式接续、锁和本地审批。MCP 冒烟真实启动 stdio，使用临时状态目录，检查全部工具并调用读文件、搜索、审批状态、两种草稿、判断提交与本地审批；不会改动现有业务状态。

Pi 探针使用本机 Pi SDK、临时 agent 配置和仅本次有效的项目信任，加载真实 `.pi/mcp.json` 及退出扩展，列出工具并通过 Pi 调用三个只读工具，也验证一轮结束请求退出。它不发送模型请求，不读取用户登录资料、不改变用户全局配置、不写入业务状态。Pi 1.5.2 的 `mcp list` 子命令不能附加 `--approve`；保存项目信任后可另外运行 `pi mcp list --json` 检查配置。

**连接探针与单元测试不等于模型判断验收。** 完整智能演示仍需用户在本机终端执行 `python demo.py pi`，观察真实模型读取、草稿和 `pending_approval`，再人工核对结论。

本期不包含常驻轮询、真实 webhook、Autoloop 中台、多 Agent 编排或任何自动外发。
