"""供本机 Pi 等 MCP 客户端调用的 stdio 服务。安装 mcp 后运行。"""
from __future__ import annotations

import json
from typing import Any

from .services import Services, create_services


def result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def register_tools(services: Services):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("开源值守", instructions="被值守仓库始终只读；补丁永远只保留本地草稿。MCP 不提供发送工具；人工审批与可选讨论区评论发送只走 CLI。")

    @mcp.tool()
    def read_repository_file(repository: str, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """读取已配置仓库的文本文件；路径限制在仓库内，最多返回 400 行。"""
        return result(services.repositories.read(repository, path, start_line, end_line))

    @mcp.tool()
    def search_repository(repository: str, query: str, glob: str = "*.ts", max_results: int = 30) -> str:
        """在指定仓库做不区分大小写的定向文本检索，忽略依赖和构建产物。"""
        return result(services.repositories.search(repository, query, glob, max_results))

    @mcp.tool()
    def read_github_release(repository: str, tag: str | None = None) -> str:
        """读取已配置仓库的 GitHub Release；需显式开启本机 gh 只读访问。"""
        return result(services.github.release(repository, tag))

    @mcp.tool()
    def read_github_issue(repository: str, number: int) -> str:
        """读取指定 Issue；本轮模拟事件返回本地快照，其他查询须显式开启在线读取。"""
        return result(services.github.issue(repository, number))

    @mcp.tool()
    def read_github_pull_request(repository: str, number: int) -> str:
        """读取指定 PR；本轮模拟事件返回本地快照，其他查询须显式开启在线读取。"""
        return result(services.github.pull_request(repository, number))

    @mcp.tool()
    def search_github_history(repository: str, query: str) -> str:
        """用 GitHub 搜索已配置仓库的历史 Issue / PR，仅查询、不写入。"""
        return result(services.github.search_history(repository, query))

    @mcp.tool()
    def search_history_and_docs(repository: str, query: str) -> str:
        """检索随仓库附带的公开历史快照与目标仓库根目录 Markdown 文档。"""
        return result(services.history.search(repository, query))

    @mcp.tool()
    def write_comment_draft(event_id: str, repository: str, body: str, target_number: int | None = None) -> str:
        """写本地评论草稿；目标编号可留空，永远只保存、不发送。"""
        return result(services.approval.draft_comment(event_id, repository, target_number, body))

    @mcp.tool()
    def write_patch_draft(event_id: str, repository: str, path: str, patch: str) -> str:
        """把补丁或改动说明写到本轮待拍板目录，绝不应用到目标仓库。"""
        return result(services.approval.draft_patch(event_id, repository, path, patch))

    @mcp.tool()
    def get_pending_approval() -> str:
        """查看当前待拍板事件、产物路径和最近审批记录。"""
        return result(services.approval.inspect())

    @mcp.tool()
    def submit_judgment(event_id: str, decision: str, reasons: list[str], evidence: list[dict[str, Any]],
                        validation_required: list[str], confidence: str) -> str:
        """提交 Pi 自己的结论，核对源码证据后停在 pending_approval；须先写两份草稿，绝不审批或发送。"""
        from .pi_judgment import PiJudgment
        return result(PiJudgment(services.settings).submit(event_id, decision, reasons, evidence, validation_required, confidence))

    @mcp.tool()
    def record_approval(event_id: str, decision: str, note: str = "") -> str:
        """仅记录本地 approve/reject，永远不触发发送；人工应使用 CLI 拍板。"""
        return result(services.state.review(event_id, decision, note))

    return mcp


def main() -> None:
    register_tools(create_services()).run(transport="stdio")


if __name__ == "__main__":
    main()
