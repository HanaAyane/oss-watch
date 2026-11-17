# 运维手册：部署、备份与排障

面向把 oss-watch 当日常值守用的人。组件原理见 [README](../README.md)，各阶段验收证据见 `docs/*-verification.md`。所有命令在项目根的虚拟环境中执行；`<状态目录>` 指 `OSS_WATCH_VAR` 指定的目录，未设置时为 `var/`。

## 1. 部署

### 1.1 前置检查

```powershell
Set-Location E:\code\oss-watch
.\.venv\Scripts\python.exe -m pip install -e .
gh auth status --hostname github.com
pi --version
```

- `config/watch.json`：每个白名单仓库配好本地 `root`（必须存在、只读）、GitHub `owner/repo`、`poll.interval_seconds`（60–2592000 秒，默认 1800）与 `poll.enabled`。低频仓库设 `86400`（一天一次）即可，心跳任务不用改。
- 判断超时：`judgment.timeout_seconds`（默认 900，可配 30–7200）。
- 用户级环境变量 `OSS_WATCH_GITHUB=1`（轮询与真实 Issue/PR 的在线读取需要）。**发送开关 `OSS_WATCH_SEND_COMMENTS` 不要设成用户级**，只在准备发真实评论的那个终端临时开启。

```powershell
[Environment]::SetEnvironmentVariable('OSS_WATCH_GITHUB', '1', 'User')
```

### 1.2 两条任务计划

poll 心跳每分钟醒一次，只拉取「到期」的仓库；daemon 登录时启动常驻循环。注册前先手动跑一轮确认无误：

```powershell
python demo.py poll --once
```

```powershell
schtasks /Create /TN "oss-watch-poll" /SC MINUTE /MO 1 /TR "'E:\code\oss-watch\.venv\Scripts\python.exe' 'E:\code\oss-watch\demo.py' poll --once" /F
```

```powershell
schtasks /Create /TN "oss-watch-daemon" /SC ONLOGON /TR "'E:\code\oss-watch\.venv\Scripts\pythonw.exe' 'E:\code\oss-watch\demo.py' watch --loop" /F
```

注意：

- 两条任务都保持「只在用户登录时运行」：toast 需要桌面会话；无桌面时通知自动降级为日志（不影响判断）。
- 任务的工作目录默认不在项目根，`demo.py` 内部按文件位置定位项目，无需额外设置；但 `OSS_WATCH_VAR` 若要指向非默认目录，需设成用户级环境变量。
- 不想用任务计划时，前台跑 `python demo.py poll --loop` 与 `python demo.py watch --loop` 等价，`Ctrl+C` 停止。
- 崩溃自动重启没做；重启前后用 `doctor` 检查锁（见 §3.1）。

### 1.3 日常节奏

1. 收到 toast（或日志里的提醒）→ `python demo.py review --open` 看结论、证据与两份草稿；
2. 复制 review 末尾的 `approve` / `reject` 命令执行（默认只留本地记录）；
3. daemon 自动接续队列里的下一条，无需重启。

判断失败/超时时 daemon 停下返回 `needs-resume`：先 `python demo.py pi --resume-round` 接续，成功后再启动 `watch --loop`（或等下次登录任务拉起）。

### 1.4 卸载

```powershell
schtasks /Delete /TN "oss-watch-poll" /F
```

```powershell
schtasks /Delete /TN "oss-watch-daemon" /F
```

事实库、草稿与日志都保留在状态目录，随时可重新部署接续。

## 2. 备份与恢复

**数据库和 `runs/` 产物要一起备份**；只复制主库会丢草稿。先停掉该状态目录的所有进程（两条任务计划可先 `/Change /DISABLE`，再结束 python/pythonw 进程），再整目录复制：

```powershell
$stateDir = if ($env:OSS_WATCH_VAR) { $env:OSS_WATCH_VAR } else { Join-Path $PWD 'var' }
$backup = Join-Path $env:TEMP ('oss-watch-var-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
Copy-Item -LiteralPath $stateDir -Destination $backup -Recurse
```

- WAL 模式下若有 `oss-watch.sqlite-wal` / `oss-watch.sqlite-shm`，一并保留，不要手动删除。
- 恢复 = 把整个目录放回原路径（或设 `OSS_WATCH_VAR` 指向它），进程重启后从事实日志重建全部状态：队列、待拍板、未完成轮次、审批历史都在。
- 事实日志只追加（数据库触发器禁止 UPDATE/DELETE），断电最多丢最后一个未提交事务；发送尝试先落盘再发请求，结果不明时记录 `unknown`，**不会自动重发**——恢复后人工核对讨论区再继续。

## 3. 排障

### 3.1 锁冲突：state.lock / state.pi.lock / state.watch.lock

三把锁分别是短暂状态写锁、Pi 进程租约、等待器租约，锁文件里记有持有者 PID 与时间。先诊断：

```powershell
python demo.py doctor
```

输出会说明每个锁的持有进程是否存活并给建议，**只诊断不删锁**。确认持有进程确已退出（任务管理器核对 PID）后，人工移走对应锁文件（移到备份目录而非删除），保留数据库与产物，再按状态接续。持有进程还活着时不要动锁——那是并发保护在正常工作。

### 3.2 事件入队了却没处理

```powershell
python demo.py status
```

按顺序核对：`database` 是否是你以为的状态目录 → `queued_events` 里有没有该事件 → `pending_approval` / `active_judgment` 是否阻塞（一次只允许一轮）→ daemon 是否在跑（任务计划程序里看 `oss-watch-daemon` 上次运行结果）。默认模式的 `watch` 派一轮就退出，属正常行为；要常驻用 `--loop`。

### 3.3 轮询没拉到新事件

- `OSS_WATCH_GITHUB=1` 是否对任务计划生效（用户级变量，注册任务后需重新登录或重启任务）；
- 该仓库 `poll.enabled` 是否为 `true`、是否真的到期：`status` 的 `poll` 段有每仓库 `attempted_at` / `cursor` / `ok` / `error`；
- 首次启用**不回溯存量**，只拉启用之后的新建事件；补拉历史用 `python demo.py poll --repo <别名> --force`（force 忽略间隔与 enabled，但仍不回拨水位）；
- 失败会退避 `min(interval, 900)` 秒且不推进水位，修复网络/凭据后自动恢复；
- bot 创建的事件与 draft release 会被跳过，属预期。

### 3.4 没收到通知

- toast 需要桌面会话；任务计划设成「不管用户是否登录」时收不到 toast，日志会有 `通知降级(bell)`；
- Windows 专注助手/勿扰会拦 toast；
- 通知按事件去重，同一待拍板只提醒一次；`--no-notify` 会显式关闭；
- 兜底渠道是响铃 + stdout + 日志，事件本身不会丢——`review` 随时能看到待拍板。

### 3.5 判断超时 / 模型中断

看门狗按 `judgment.timeout_seconds` 终止进程树并落 `judgment_timed_out` 事实，产物保留：

```powershell
python demo.py pi --resume-round
```

它读取状态中保存的原事件与提示词，不依赖旧会话。**未成功提交（没有 `pending_approval`）时不能 approve**。反复超时先查模型配置与网络，再考虑调大超时。

### 3.6 发送结果 unknown / sending

不会自动重试。人工打开该 Issue/PR 讨论区核对评论是否已发出：已发出就当成功留档；未发出也**不要重复 approve**（会被拒绝），需要发送时人工在讨论区补评或等下一轮事件。详见 README「批准后可选发评论」。

## 4. 日志与产物在哪

| 路径 | 内容 |
| --- | --- |
| `<状态目录>/oss-watch.sqlite`（+ `-wal`/`-shm`） | 唯一状态来源：事实日志与推导视图 |
| `<状态目录>/runs/<event_id>/` | `prompt.md`、`conclusion.json`、`comment.md`、`patch.md`、`pi-output.jsonl`、`pi-stderr.log`、`session/` |
| `<状态目录>/logs/oss-watch.log`（5MB×3 轮转） | 流程节点与事件 ID，**不记事件正文与环境变量值** |

排障时 `pi-output.jsonl` 可看模型每一步工具调用；`Get-Content -Wait` 可实时跟踪。日志与产物都在被忽略的 `var/` 下，不会进入版本库。

## 5. 验证安装

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m compileall -q src demo.py tests
.\.venv\Scripts\python.exe tests/mcp_smoke.py
```

测试与冒烟默认用 `fixtures/target-repo/` 的随仓库副本，**clone 即可全量运行**，不依赖真实被值守仓库、不联网、不弹通知、不调用模型。要针对真实仓库跑，显式设 `OSS_WATCH_TEST_REAL_REPO=1`（仍只读）。CI 在 GitHub Actions 的 windows-latest 上跑同一套命令。