"""隔离 MCP stdio 冒烟：真实调用读取、草稿、模型提交与仅本地审批。"""
import asyncio
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from oss_watch.state import StateStore

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(state_root: Path, fixture: str) -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(root / "src"), os.environ.get("PYTHONPATH", "")]))
    env["OSS_WATCH_CONFIG"] = str(root / "config" / "watch.json")
    env["OSS_WATCH_GITHUB"] = "0"
    event = json.loads((root / "fixtures" / fixture).read_text(encoding="utf-8"))
    event_id = event["event_id"]
    parameters = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), "--serve", str(state_root), fixture], cwd=str(root), env=env)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {"read_repository_file", "search_repository", "read_github_release", "read_github_issue", "read_github_pull_request", "search_github_history", "search_history_and_docs", "write_comment_draft", "write_patch_draft", "get_pending_approval", "record_approval", "submit_judgment"}
            assert expected == names, f"工具列表不符：{expected ^ names}"
            response = await session.call_tool("read_repository_file", {"repository": "dsh-reasoning-effort", "path": "package.json", "start_line": 1, "end_line": 5})
            payload = json.loads(response.content[0].text)
            assert payload["content"].startswith('1: {'), payload
            response = await session.call_tool("search_repository", {"repository": "dsh-reasoning-effort", "query": "aria-label", "glob": "*.tsx"})
            assert json.loads(response.content[0].text)["matches"], "搜索没有命中真实源码"
            response = await session.call_tool("get_pending_approval", {})
            assert json.loads(response.content[0].text)["active_judgment"]["event"]["event_id"] == event_id
            if event["type"] != "release.published":
                key = "issue" if event["type"] == "issues.opened" else "pull_request"
                response = await session.call_tool("read_github_" + key, {"repository": event["repository"], "number": event[key]["number"]})
                assert not response.isError, response
                snapshot = json.loads(response.content[0].text)
                assert snapshot["source"] == "local-event" and snapshot["fixture"] is True
            arguments = {"event_id": event_id, "repository": "dsh-reasoning-effort"}
            for name, extra in (("write_comment_draft", {"body": "# 隔离冒烟评论草稿"}),
                                ("write_patch_draft", {"path": "patch.md", "patch": "# 隔离冒烟补丁说明，未应用"})):
                response = await session.call_tool(name, {**arguments, **extra})
                assert not response.isError, response
                artifact = Path(json.loads(response.content[0].text)["artifact"])
                assert artifact.is_relative_to(state_root) and artifact.is_file()
            response = await session.call_tool("submit_judgment", {
                "event_id": arguments["event_id"], "decision": "协议冒烟，非模型判断", "reasons": ["只验证工具协议"],
                "evidence": [{"path": "package.json", "line": 1, "text": "{", "finding": "读取清单原文"}],
                "validation_required": ["真实模型判断需要另外验收"], "confidence": "不评价兼容性",
            })
            assert not response.isError, response
            payload = json.loads(response.content[0].text)
            assert payload["status"] == "pending_approval" and payload["delivery"] == "not-sent"
            response = await session.call_tool("record_approval", {"event_id": arguments["event_id"], "decision": "approve", "note": "隔离测试，仅本地"})
            assert not response.isError, response
            assert json.loads(response.content[0].text)["delivery"] == "not-sent"
            assert StateStore(state_root / "oss-watch.sqlite").load()["pending_approval"] is None
            print(json.dumps({"mcp": "隔离 stdio 握手、全部工具清单、真实读取、草稿、判断提交、仅本地审批均通过", "event_type": event["type"], "tool_count": len(names)}, ensure_ascii=False, indent=2))


def serve(state_root: Path, fixture: str) -> None:
    from oss_watch.config import load_settings
    from oss_watch.mcp_server import register_tools
    from oss_watch.pi_judgment import PiJudgment
    from oss_watch.services import create_services

    settings = replace(load_settings(), state_path=state_root / "oss-watch.sqlite", runs_root=state_root / "runs")
    services = create_services(settings)
    event = services.workflow.load_event(str(settings.fixtures_root / fixture))
    PiJudgment(settings).prepare(event)
    register_tools(services).run(transport="stdio")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]), sys.argv[3])
    else:
        for fixture in ("release-v0.3.0-rc.1.json", "issue-menu-overflow.json", "pr-menu-position.json"):
            with tempfile.TemporaryDirectory(prefix="oss-watch-mcp-smoke-") as temporary:
                asyncio.run(main(Path(temporary), fixture))
