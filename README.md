# 开源值守

面向个人开源仓库的本地事件驱动维护助手。第一期只做一件小而完整的事：**收到模拟 DeepSeek Harness 发版后，检查 `dsh-reasoning-effort` 的真实源码，给出适配结论和草稿，然后停下来等人拍板。**

不是聊天套壳，也不是自动发评论机器人：读取、检索、草稿、审批都有明确的本地边界。

## 一条命令跑演示

要求：本机 Python 3.11+，参考仓库位于 `E:\code\插件\dsh-reasoning-effort`（路径可在配置中改）。

```powershell
python demo.py demo
```

这条命令**不需要安装依赖、不需要网络、不需要 Token**。它会：

1. 读取 `fixtures/release-v0.3.0-rc.1.json`，把模拟发版当作一次 `release.published` 事件。
2. 只读检查 `package.json`、`CHANGELOG.md` 和 `src/client/native-selector.tsx` 等真实文件。
3. 结合历史 Issue / PR 快照判断是否需要适配。
4. 将结论、评论草稿、补丁草稿写入 `var/runs/<事件 ID>/`。
5. 把事件写入 `var/state.json`，状态停在 `pending_approval`；**不会修改参考仓库，也不会向外发评论或开 PR**。

本次 fixture 模拟菜单改为带 `data-dsh-pane` 标识的可重排窗格。旧插件仍按 `aria-label` 和第二个子节点定位，因此预期判断为「需要修改」。该 fixture 不是 DeepSeek Harness 官方公告；补丁草稿没有经过新版运行时验证。

### 查看状态与本地审批

```powershell
python demo.py status
python demo.py approve demo-dsh-v0.3.0-rc.1 --note "先保留草稿"
# 或者拒绝：
python demo.py reject demo-dsh-v0.3.0-rc.1 --note "暂不跟进"
```

审批仅改变本地 JSON 记录。即使 `approve`，也**不会发送**。重复运行同一事件会返回 `already_processed`；上一轮未审批时，不会开始新一轮。若想重新演示，请先人工检查并移走本项目的 `var/` 目录；不要删除目标仓库的任何文件。

### 输出在哪里

- `var/state.json`：处理过的事件、上次结论、当前待拍板轮次、审批历史。
- `var/runs/demo-dsh-v0.3.0-rc.1/conclusion.json`：判断、理由、源码证据、待验证事项。
- `var/runs/demo-dsh-v0.3.0-rc.1/comment.md`：未发送的评论草稿。
- `var/runs/demo-dsh-v0.3.0-rc.1/patch.md`：未应用的 unified diff 片段与改动说明。

`var/` 是运行产物，已被 `.gitignore` 忽略。

## MCP 工具（给本机 Pi 用）

核心逻辑使用 Python 标准库；仅 MCP stdio 服务需要官方 Python SDK。推荐使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m oss_watch.mcp_server
```

最后一条是长驻 stdio 服务，等待 MCP 客户端连接，不会打印网页或自行执行演示。`pi.mcp.json` 提供等价配置模板；按所用 Pi 版本的 MCP 配置方式引入它即可。若当前 Pi 不能直接使用此模板，请按该版本的文档把命令改为 `.venv\Scripts\python.exe -m oss_watch.mcp_server`。第一期未要求、也未宣称已完成 Pi 实机接入。

提供的工具：

| 能力 | MCP 工具 |
| --- | --- |
| 读仓库文件 | `read_repository_file` |
| 指定仓库搜代码 | `search_repository` |
| 读 GitHub Release / Issue / PR | `read_github_release` / `read_github_issue` / `read_github_pull_request` |
| 检索历史 Issue / PR | `search_github_history` |
| 检索本地历史快照与文档 | `search_history_and_docs` |
| 写评论 / 补丁草稿 | `write_comment_draft` / `write_patch_draft` |
| 查看待拍板 / 记录审批 | `get_pending_approval` / `record_approval` |

GitHub 在线读取默认关闭，不需要在演示中开启。需要时，复制 `.env.example` 为 `.env`，设置 `OSS_WATCH_GITHUB=1`，并自行确保 `gh auth login` 已完成。项目不接收、不保存、不打印 Token。所有 GitHub 工具只允许访问 `config/watch.json` 中登记的仓库。

### 为什么既有 CLI 又有 MCP？

- **CLI 是确定性的演示编排器**：一条命令跑通完整闭环，方便验收和回归测试。
- **MCP 是同一套能力的工具接口**：Pi 可以读文件、查资料、生成草稿和请求审批。
- 两者共享 JSON 状态与审批门，不存在绕过审批的隐藏发送接口。

## 配置与安全边界

`config/watch.json` 默认只配置 `dsh-reasoning-effort`，`writable: false`，`delivery.mode: draft-only`。

- 仓库读取只允许相对路径，拒绝 `..`、绝对路径和符号链接逃逸。
- 定向文本搜索排除隐藏目录、依赖和构建产物；不使用向量库、Docker、多 Agent 编排。
- 本地 fixture 和在线返回都视为数据，不作为命令执行。
- 没有发送评论、向目标仓库推分支、创建目标仓库 PR 的实现；`record_approval` 也不会外发。
- `.env` 不入库；`.env.example` 只放变量名与用途，不放密钥。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src demo.py tests
# 需先执行上面的虚拟环境安装：
.\.venv\Scripts\python.exe tests\mcp_smoke.py
```

单元测试覆盖真实源码副本上的闭环、事件去重、审批边界、不支持的发版契约、源码变化时停止、路径越界和 GitHub 默认关闭。`mcp_smoke.py` 会真实启动 stdio 服务，完成握手、列工具，并调用读文件、搜索和审批状态工具。

## 目录结构

```text
oss-watch/
├── demo.py                    # 免安装依赖的演示入口
├── config/watch.json          # 仓库白名单与 draft-only 配置
├── fixtures/                  # 模拟发版与公开历史快照
├── src/oss_watch/
│   ├── workflow.py            # 版本适配判断与闭环编排
│   ├── repository.py          # 只读文件与代码搜索
│   ├── github.py              # 可选的 gh 只读访问
│   ├── history.py             # 历史与文档定向检索
│   ├── approval.py            # 草稿写入与审批门
│   ├── state.py               # 原子 JSON 状态与审批记录
│   ├── mcp_server.py          # stdio MCP 接口
│   └── cli.py                 # demo / status / approve / reject
├── tests/                     # 标准库单元测试
└── var/                       # 本地状态与待拍板产物（不入库）
```

## 第一期明确不做

真实 webhook 轮询、自动发送评论、向被值守仓库开 PR、自动合并补丁、向量检索、模型微调、多 Agent 编排、Autoloop 接入。后续要增加发送能力时，仍必须单独设计审批与 dry-run 验证，而不是把草稿工具改名成发送工具。
