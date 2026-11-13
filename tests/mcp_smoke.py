"""独立 MCP stdio 冒烟测试：真实启动服务并调用只读工具。"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(root / "src"), os.environ.get("PYTHONPATH", "")]))
    env["OSS_WATCH_CONFIG"] = str(root / "config" / "watch.json")
    env["OSS_WATCH_GITHUB"] = "0"
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "oss_watch.mcp_server"], cwd=str(root), env=env)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {"read_repository_file", "search_repository", "read_github_release", "read_github_issue", "read_github_pull_request", "write_comment_draft", "write_patch_draft", "get_pending_approval"}
            assert expected <= names, f"缺少工具：{expected - names}"
            response = await session.call_tool("read_repository_file", {"repository": "dsh-reasoning-effort", "path": "package.json", "start_line": 1, "end_line": 5})
            payload = json.loads(response.content[0].text)
            assert payload["content"].startswith('1: {'), payload
            response = await session.call_tool("search_repository", {"repository": "dsh-reasoning-effort", "query": "aria-label", "glob": "*.tsx"})
            assert json.loads(response.content[0].text)["matches"], "搜索没有命中真实源码"
            response = await session.call_tool("get_pending_approval", {})
            assert "pending_approval" in json.loads(response.content[0].text)
            print(json.dumps({"mcp": "stdio 握手、列工具、读文件、搜索、审批状态均通过", "tool_count": len(names)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
