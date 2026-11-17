# oss-watch 落地改进方案

> 目标：把当前「演示闭环」变成**每天真实值守自己开源仓库**的工具。
> 撰写日期：2026-11-16。基于对 `src/oss_watch/` 全部 15 个模块、48 项测试与 README 的通读；
> 本机实测确认了文中各「现状」条目（48 项测试通过、`pi --help` 确认 `-p`/`--mode json`、`gh` 2.94.0 可用）。

---

## 0. 一句话方案

**不推翻现有架构**。事件溯源、只读围栏、审批门、冻结-核验发送这四块是项目的核心价值，全部保留；
落地缺的是四件事：**修安全/性能缺口 → 事件能自动进来（轮询）→ 轮次能无人值守跑（headless Pi）→ 待办主动找人、拍板顺手（通知 + review 视图）**。
按 P0–P4 五个阶段推进，每阶段独立可验收、可回退，P0–P3 完成后即达到「真实值守」标准。

### 「真正投入使用」的定义（验收基线）

连续两周满足以下全部条件，视为落地成功：

1. `HanaAyane/dsh-reasoning-effort` 的新 Issue / PR / Release 在无人投递的情况下自动进入收件箱；
2. 每个事件自动完成一轮 Pi 判断并停在待拍板，人只负责看草稿、拍板；
3. 待拍板产生后 10 分钟内人能收到通知（不必盯着终端）；
4. 全程零误发：模拟事件、被篡改草稿、未批准内容一律未出现在真实讨论区；
5. 任意时刻断电/杀进程，重启后状态完整、可接续，无重复评论。

---

## 1. 现状盘点

### 1.1 必须保留的设计（不要动）

| 设计 | 位置 | 保留理由 |
| --- | --- | --- |
| 追加式事实日志 + 纯投影 | `state.py` | 崩溃安全、可审计、可完整重建，是无人值守的信任基础 |
| 多层只读围栏 | `config.py` / `repository.py` / MCP 工具面 / `.pi/one-round.ts` | 「永不写目标仓库」是项目对外的核心承诺 |
| 审批门 + 草稿冻结 + SHA-256 核验 | `approval.py` / `delivery.py` | 误发防线；headless 化后这条线更重要 |
| claim-before-send、不自动重发 | `delivery.py` | 宁可人工核对也不重复评论，语义正确 |
| 证据逐条核对原文 | `pi_judgment.py` submit | 抑制模型编造，落地后仍是最有效的质量闸 |
| 规则 demo 与 Pi 判断分离 | `workflow.py` | demo 保持冻结的回归品，不冒充智能判断 |

### 1.2 已核实的缺口（落地的直接障碍）

| # | 缺口 | 现状证据 | 影响 |
| --- | --- | --- | --- |
| G1 | 隐藏文件可被读取/检索 | 实测 `read(".env")` 返回内容；搜索过滤 `parts[:-1]` 漏掉文件名本身（`repository.py:38`） | 目标仓库若有点文件密钥，会先暴露给模型，可能进入外发草稿 |
| G2 | 搜索未剪枝 | 目标仓库 node_modules 含 42,486 个 .ts；实测一次未命中搜索 5.7 秒 | 每轮判断多次搜索，无人值守时是分钟级浪费 |
| G3 | 事件只能人工投递 | 无轮询器（README FAQ 13 明确「还没做」） | 人不投递就没有值守，违背落地目标 |
| G4 | Pi 只有交互 TUI 模式 | `pi_judgment.py:141` 固定交互参数；本机 `pi --help` 确认支持 `-p`、`--mode json`、`--session-dir` | 无法后台运行；每轮必须有人开着终端 |
| G5 | watch 派一轮即退出、无通知 | `inbox.py:66`；无 toast/日志（src 中无 logging 引用） | 待拍板积压无人知晓；拍板后要手动重启 watch |
| G6 | 测试依赖外部真实仓库 | `test_workflow.py:28` 从 `E:\code\插件\...` 复制源码 | 换机器 clone 后 48 项测试跑不起来，回归不可复现 |
| G7 | 无运行日志 | CLI 只向 stdout 打 JSON；watch 崩溃原因无处可查 | 无人值守出问题无法排障 |
| G8 | 零散缺陷 | `github.py:85` no-op replace；`delivery.py:45` 旧记录缺 comment 键会裸 KeyError；锁文件无 PID/时间戳；队列按 event_id 字符串排序 | 小，但都在无人值守路径上 |

---

## 2. 总体架构（目标态）

```
                       ┌─────────────────────────────────────────────┐
  Windows 任务计划      │  本机（无公网监听，无常驻中台）                │
  每 1 分钟触发         │                                             │
      │                │   poll ──按各仓库间隔挑到期的──gh 只读拉取     │
      ▼                │        ──▶ 转事件 ──▶ enqueue                │
  oss-watch poll --once│                                  │          │
                       │            SQLite 事实日志（不变，新增少量事实类型）
                       │                                  ▲          │
                       │   daemon（watch --loop）──────────┘          │
                       │      │ 取队首 → headless Pi（-p + 超时看门狗） │
                       │      │        └── MCP 工具面（不变）          │
                       │      ▼                                       │
                       │   pending_approval ──▶ 通知（toast + 日志）    │
                       │      │                                       │
                       │   人：status / review 浏览 / approve / reject  │
                       │      │                                       │
                       │   CommentDelivery（不变，默认关）              │
                       └─────────────────────────────────────────────┘
```

要点：

- **poll 与 daemon 是两个独立进程**，都以事实库为唯一交接面，谁挂了都不丢数据；
- **审批永远是人**：daemon 只是把「派轮 → 停下 → 叫人 → 人拍板 → 继续下一轮」串起来，不新增任何自动发送能力；
- **交互模式保留**：`pi <event>`（现在的行为）作为调试/接管入口不删，headless 是新增的默认值守路径；
- 现有 `.pi/one-round.ts`、工具排除、环境开关约束在 headless 下原样生效。

### 2.1 用户交互模型：落地后的一天

落地后人只做三类事，日常 95% 的交互是「看草稿、拍板」，不盯终端、不操作模型：

**① 一次性配置**

- `config/watch.json`：白名单仓库 + 各自轮询频率；`.env`：读取/发送开关；
- 注册两条任务计划（poll 心跳 + daemon），命令写在 README，手动执行一次。

**② 日常主循环（被动触发）**

```text
09:02  Windows toast：「dsh-reasoning-effort 新 Issue #42 判断完成，待拍板」
       （poll 发现 → daemon 派 headless Pi → 停在审批门 → 通知，全程无人工）
09:05  打开终端：python demo.py review
       → 人类可读摘要：事件、结论、理由、证据、待验证项
       → --open 用默认编辑器打开 comment.md / patch.md
       → 末尾给出填好 event_id 的 approve / reject 命令，复制即用
09:08  看完草稿，粘贴执行：
       python demo.py approve gh-HanaAyane-dsh-reasoning-effort-issue-42-opened --note "同意回复"
       （真实发送需先开 OSS_WATCH_SEND_COMMENTS=1；默认只留本地记录）
09:08  daemon 自动接续队列下一条；没有下一条就安静等待
```

**③ 例外处理（低频）**

- 判断中断/超时：`pi --resume-round` 接续；想亲自接管一轮时用保留的交互模式（`--interactive`）；
- 锁异常：`doctor` 诊断，人工确认后移锁；
- 处理存量旧 Issue / 立即拉取：手动 `enqueue` / `poll --force`；
- 排障：`status`（完整 JSON，给脚本用）与 `logs/oss-watch.log`。

设计原则：**review 只读，拍板必须显式带 event_id**。「一次只有一条待拍板」的既有不变量让无参数
`review` 永远指向唯一一轮，但批准仍要求人粘贴/输入 ID，不提供无参数「一键批准」，杜绝顺手误批。
落地后不提供与模型自由对话的入口——追问和接管走交互模式，日常只有「看证据 → 做决定」。

---

## 3. 分阶段计划

> 每阶段一个分支 + 一个 PR，合并前跑全量测试；工作量按「专注小时」粗估，供排期参考。
> 依赖关系：P0 → P1 → P2 顺序推进；P3 可与 P2 并行；P4 独立穿插。

### P0 安全与正确性修复（约 0.5–1 天）

**做什么**

1. **G1 隐藏文件围栏**（`repository.py` + `config.py`）
   - `search` 的过滤从 `parts[:-1]` 改为 `parts`（文件名也参与隐藏/排除判断）；
   - `read` 增加同样规则：路径任一部件以 `.` 开头或在排除目录名单内则拒绝；
   - 排除名单补充常见产物目录（`node_modules`、`lib`、`dist`、`build`、`coverage`）。
2. **G2 搜索剪枝**（`repository.py`）
   - `rglob` 改为 `os.walk`，在下降前 `dirnames[:] = [...]` 剪掉排除目录与隐藏目录；
   - 保持现有返回结构、`max_results`、大小上限不变。
3. **G8 零散缺陷**
   - 删掉 `github.py:85` 的 no-op replace（search API 的 `html_url` 本就是网页地址）；
   - `delivery.py` 对 `artifacts` 缺 `comment` 键的旧记录走「草稿校验失败，未发出」分支，不抛裸 KeyError；
   - `inbox.py` 队列排序改为按入队事实顺序（投影里 queue 已按事实序插入，去掉 `sorted(... + ".json")` 兼容排序即可，README 同步改一句）。

**验收**

- 新增回归：点文件 read/search 被拒；含 node_modules 副本的临时仓库中搜索不进入排除目录（可用目录内放哨兵文件断言未命中）；
- 实测目标仓库一次未命中 `*.ts` 搜索 < 0.5 秒（当前 5.7 秒）；
- 全量测试通过，行为无其他变化。

**风险**：极低。全部改动局部于两个文件，现有测试结构直接覆盖。

---

### P1 无人值守轮次：headless Pi + 看门狗 + 日志（约 1–2 天，本方案技术核心）

**做什么**

1. **G4 headless 启动路径**（`pi_judgment.py`）
   - `launch` 增加 headless 分支：在现有参数上加 `-p`、`--mode json`、`--session-dir <runs/<event_id>/session>`；
   - 其余约束原样保留：`--approve --no-extensions -e builtin:mcp -e .pi/one-round.ts --no-skills --no-prompt-templates --no-context-files --tools mcp__oss_watch__* --exclude-tools mcp__oss_watch__record_approval @prompt.md`；
   - Pi stdout/stderr 重定向到 `runs/<event_id>/pi-output.jsonl` / `pi-stderr.log`（JSON 模式逐事件输出，天然是审计日志）；
   - `settings` / CLI 增加 `--interactive` 保留旧行为；`watch` 默认走 headless。
   - **成功判据不变**：进程退出后以事实库里的 `pending_approval` 为准，不解析模型自述。
2. **超时看门狗**
   - `subprocess.run(..., timeout=N)`，N 进配置（默认 900 秒，`watch.json` 新增 `judgment.timeout_seconds`）；
   - 超时杀进程树（Windows 下 `taskkill /T /F` 或 `psutil`——**不引新依赖，用 taskkill**），事实库中 `active_judgment` 保留，走既有 `--resume-round` 接续；
   - 新增事实类型 `judgment_timed_out`（记 attempt 次数与原因），投影为活动轮次上的元数据，不影响既有状态机。
3. **G7 运行日志**（新增 `logging_setup.py`，标准库 logging）
   - CLI/daemon/poll 统一写 `<状态目录>/logs/oss-watch.log`（RotatingFileHandler，5 MB × 3）；
   - 关键节点各一条：入队、派轮、Pi 退出码、超时、待拍板、拍板、发送结果；**不打印事件正文与任何环境变量值**（沿用现有保密约定）；
   - stdout 的 JSON 输出协议不变，脚本兼容。
4. **锁可诊断**（`state.py`）
   - 锁文件内容写入 `{pid, started_at, owner}`；`locked()` 冲突报错时带上这些信息；
   - 新增 `doctor` 子命令：列出各锁、PID 是否存活（`ctypes OpenProcess` 或 `tasklist` 查询），给出「可安全移走 / 仍在使用」建议；**不自动删锁**。

**验收**

- 新增单测：headless 参数拼装（沿用 `test_pi_judgment.py` 的 mock 手法）、超时后事实与接续、日志不含事件正文；
- 真实验收：关掉终端窗口，用 headless 跑一条模拟 Issue，确认草稿齐全、停在待拍板、`pi-output.jsonl` 可复盘；
- 手动杀 Pi 进程 → `status` 显示活动轮次 → `pi --resume-round`（headless）接续成功。

**风险与对策**

- *headless 下 Pi/MCP 行为差异*：`--mode json` 与 TUI 的工具调用路径可能有细节差异 → 先用 `tests/pi_smoke.mjs` 思路加一个 headless 探针（不调模型），再跑真实一轮；交互模式保留为逃生门。
- *看门狗误杀长判断*：900 秒对单事件足够宽；超时可配置，且超时不丢事实。

---

### P2 事件自动进来：内置轮询器（约 1–2 天）

**做什么**（新增 `poller.py` + CLI `poll` 子命令）

1. **按仓库配置轮询频率**（`config/watch.json`，各仓库独立）
   - 每个仓库可选 `poll` 对象：`{"interval_seconds": 86400}`；不写则用全局默认（建议 1800 秒）；`{"enabled": false}` 表示该仓库不参与自动轮询（仍可手动 enqueue）；
   - 示例：需要盯紧的仓库 300 秒（分钟级），低频仓库 86400 秒（1 天一次）；
   - 配置校验范围 60 秒–30 天，防止误配置刷 API；改频率只改配置文件，不用动任务计划。
2. **调度：一个定时器 + 到期检查**（不为每个仓库建一个定时任务）
   - 任务计划只需一条：每 1 分钟跑 `poll --once`；每次运行从事实投影读各仓库上次轮询时间，只拉「到期」的仓库（now ≥ last + interval_seconds），未到期直接跳过、不联网；
   - 空转一次只读本地状态（毫秒级），代价可忽略；日级仓库一天只真正拉一次，分钟级仓库每分钟拉；
   - 失败退避：拉取失败不推进 cursor，按 min(interval, 15 分钟) 后重试——日级仓库失败后不必干等一天，也不会每个 tick 都打 API；
   - 手动辅助：`--repo <别名>` 只处理指定仓库；`--force` 忽略间隔立即拉取（补拉/验收用）；`--loop --tick 60` 可选自循环，替代任务计划。
3. **拉取**：对到期仓库，用 `gh api` 只读拉取
   - `repos/{owner}/{repo}/issues?state=all&sort=updated&direction=desc&since=<cursor>`（含 PR，按 `pull_request` 字段分流）；
   - `repos/{owner}/{repo}/releases?per_page=10`（按 `published_at` 过滤）。
4. **cursor 与轮询时间都是事实**：新增 `poll_cursor_updated` 事实（含仓库、尝试时间、成功与否、水位），投影出当前 cursor 与上次轮询时间；崩溃后从上一水位重拉，靠幂等去重兜底。
5. **事件转换**
   - `event_id` 确定性生成：`gh-{owner}-{repo}-issue-{number}-opened` / `...-release-{tag}`（字符集符合 `safe_event_id`）；
   - 只生成 `issues.opened` / `pull_request.opened` / `release.published` 三类现有事件；`fixture: false`；
   - **首期只处理「新建」**：`created_at >= cursor` 的才入队，`updated` 变化不入队（避免编辑刷事件）；
   - 跳过 bot 作者（`[bot]` 后缀）与白名单外的仓库；正文超长截断到现有校验允许的范围内并标注。
6. **入队**：走现有 `EventInbox.enqueue`，同 ID 同正文幂等、同 ID 异正文拒绝——现有语义正好是轮询器需要的。
7. **开关**：`OSS_WATCH_GITHUB=1` 才允许 poll（复用现有只读开关语义）。
8. **代理**：gh 子进程继承环境变量；`.env.example` 注明本机需 `HTTPS_PROXY=http://127.0.0.1:7897`（按你的全局约定）。

**验收**

- 单测：mock gh 响应 → 转换、cursor 推进、重拉幂等、bot 过滤、编辑不重复入队；**到期判断**（两个不同 interval 的仓库在同一 tick 只有一个被拉取；未到期仓库零 gh 调用）；失败退避与 `--force`；
- 真实验收：对自己仓库发一条测试 Issue，`poll --once --force` 后 `status` 出现队列事件；再跑一次不带 `--force` 的 poll，确认未到期不拉取、到期重拉也不重复；
- 任务计划注册**每 1 分钟一条** `poll --once`（注册命令写进 README，由你手动执行）；各仓库频率全部由 `watch.json` 控制。

**风险与对策**

- *GitHub API 限额*：只拉到期仓库，日级仓库一天 2–3 个请求；分钟级仓库按 60 秒下限约 1500 请求/天，仍低于 5000/小时限额；失败退避防打爆；
- *历史存量*：首次 cursor 初始化为「启用时刻」，不回溯历史 Issue（要处理旧 Issue 手动 enqueue，语义清晰）；
- *每分钟空转的代价*：一次空转只读本地事实库，毫秒级、不联网；换来的是改频率不用改任务计划。

---

### P3 常驻、通知与审阅视图：daemon + toast + review（约 1–1.5 天，可与 P2 并行）

**做什么**

1. **G5a `watch --loop`**（`inbox.py`）
   - 显式 opt-in：默认行为完全不变（派一轮退出）；
   - loop 模式：派轮 → 停在待拍板 → 发通知 → **等待拍板事实出现**（轮询状态，间隔同 `--interval`）→ 归档 → 取下一轮；队列空则继续等待；
   - 审批门语义不变：loop 只是等，不代拍板；活动轮次异常仍退出并提示 `--resume-round`；
   - `.watch.lock` 租约照旧防双开。
2. **G5b 通知**（新增 `notify.py`，零依赖）
   - Windows toast：PowerShell `Windows.UI.Notifications`（subprocess 调用，失败静默降级）；
   - 兜底：日志 + stdout 明示 + `\a` 响铃；
   - 通知内容只含 event_id、仓库、结论一句话与「打开草稿」的路径，**不含评论正文**；
   - 触发点：进入待拍板、判断超时、发送结果 unknown。
3. **`review` 审阅视图**（CLI 新增子命令，人读入口；`status` 保留原始 JSON 供脚本）
   - 默认渲染当前待拍板：事件摘要、结论/理由/证据/待验证项（来自 `conclusion.json`）、产物路径，末尾给出填好 event_id 的 `approve` / `reject` 命令，复制即用；
   - `--open` 用默认编辑器打开 comment.md / patch.md；`--event <id>` 回看历史轮次；
   - **只读，不内嵌拍板动作**：批准仍走独立 `approve <event_id>`，不提供无参数「一键批准」，避免顺手误批。
4. **部署方式（Windows 任务计划，两条任务）**
   - `oss-watch-poll`：每 1 分钟 `python demo.py poll --once`（只是心跳；各仓库真实频率由 `watch.json` 的 `poll.interval_seconds` 决定，见 P2）；
   - `oss-watch-daemon`：登录时启动 `python demo.py watch --loop`（无窗口 `pythonw`，日志落盘）；
   - README 给出 `schtasks` 注册/卸载命令，由你手动执行；不引入 NSSM/服务包装等新依赖。

**验收**

- 单测：loop 在拍板后自动接续第二条队列事件（沿用 `test_complete_loop.py` 的线程手法）；默认模式行为回归不变；`review` 渲染与 `status` 数据一致、不触发任何写操作；
- 真实验收：任务计划跑起来后，从「GitHub 发 Issue」到「收到 toast → review 看草稿 → 粘贴 approve」全程只需一次终端拍板；approve 后 daemon 自动处理下一条。

**风险与对策**

- *toast 在无桌面会话时失败*：静默降级到日志，不阻塞主流程；
- *daemon 长期运行累积状态*：事实库轻量（见 P4），日志轮转已限容。

---

### P4 工程化与长期健康（穿插进行，约 1–2 天）

1. **G6 测试自包含**：把 `native-selector.tsx`、`package.json`、`CHANGELOG.md`、`README.md` 四个文件的最小副本收进 `fixtures/target-repo/`，`test_workflow.py` 优先用副本、真实仓库仅在显式环境变量下使用；「clone 即可全量测试」成立。
2. **lint / 类型检查**：ruff + mypy（**新开发依赖，需你确认后再加**；只进 dev 依赖，不进运行时）。
3. **CI**：GitHub Actions 跑 unittest + compileall + mcp_smoke（P4.1 完成后才可行）；windows-latest 单平台即可。
4. **文档重组**：README 保留「是什么 + 快速开始 + 演示」，把组件细节/FAQ 拆到 `docs/`；新增 `docs/operations.md`（部署、备份、排障、doctor）。
5. **事实库快照**（预留，暂不做）：事件量上千后加 `snapshot_taken` 事实 + 增量投影；接口保持 `project()` 纯函数语义。
6. **补丁 dry-run 校验**（可选增强）：对 Pi 产出的 unified diff 做「上下文唯一命中」校验（推广 `workflow.py` 的 `replace_once` 思路），提升草稿可用性。

---

## 4. 明确不做的事（保护项目边界）

- **不做公网 Webhook 接收端**：轮询已满足时效（分钟级），不暴露本机、少一套验签/防重放面；
- **不自动应用补丁 / 开修复 PR / 合并**：落地不改变「被值守仓库永远只读」承诺；
- **不自动审批、不自动重发**：headless 与 daemon 只减少「派轮和等待」的人工，拍板永远是人；
- **不引入中台、向量库、消息队列**：SQLite 事实日志 + 两个本机进程足够本场景；
- **不做通用问答机器人**：只处理三类事件，范围不变。

## 5. 新增决策点（需要你拍板的）

| # | 决策 | 建议默认 |
| --- | --- | --- |
| D1 | headless 成为 watch 默认（交互保留为 `--interactive`） | 是 |
| D2 | 轮询频率与首期范围 | **频率按仓库独立配置**（已定）：全局默认 1800 秒，每仓库可覆写（如日级 86400、分钟级 300）；只「新建」事件；首期仅 dsh-reasoning-effort |
| D3 | 通知渠道与审阅入口 | Windows toast + 日志兜底；审阅用 CLI `review`（--open 打开草稿），零依赖；Web 审阅面板首期不做 |
| D4 | ruff/mypy 开发依赖 | 加（P4 再定） |
| D5 | 判断超时 | 900 秒，可配置 |

## 6. 里程碑与工作量汇总

| 阶段 | 内容 | 预估 | 完成标志 |
| --- | --- | --- | --- |
| P0 | 围栏 + 剪枝 + 零散修复 | 0.5–1 天 | 安全回归通过；搜索 < 0.5s |
| P1 | headless + 看门狗 + 日志 + doctor | 1–2 天 | 关终端跑通一轮真实判断 |
| P2 | 内置轮询器 | 1–2 天 | 真实 Issue 自动入队且幂等 |
| P3 | daemon + 通知 + review 视图 | 1–1.5 天 | 发 Issue → 收 toast → review 拍板，人只动手一次 |
| P4 | 测试自包含 / CI / 文档 | 1–2 天 | clone 即可测试，CI 绿 |

合计约 5–8 个专注日。P0–P3 完成即满足第 0 节的落地验收基线；P4 决定项目能否长期健康维护。

## 7. 每阶段统一验收纪律

- 一个阶段一个分支一个 PR，合并前：全量 unittest + compileall + mcp_smoke（涉及 Pi 启动改动时加 pi_smoke）；
- 真实外发验收永远用你自己的测试 Issue，且默认开关关闭状态下先验证「不发」，再显式开启验证「只发一条」；
- 每阶段结束更新 `docs/complete-loop-verification.md` 风格的验收记录，写实际跑过的命令与输出。
