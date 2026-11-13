"""通过本机 gh 做有限、只读的 GitHub 查询。从不输出 Token。"""
from __future__ import annotations

import json
import subprocess
from typing import Any

from .config import Repository, Settings
from .state import StateStore


class GitHubReader:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _repository(self, repository: str) -> Repository:
        try:
            return self.settings.repositories[repository]
        except KeyError as error:
            raise ValueError(f"未配置的仓库：{repository}") from error

    def _call_path(self, path: str) -> Any:
        if not self.settings.github_enabled:
            raise PermissionError("GitHub 只读工具默认关闭；需要时在 .env 设置 OSS_WATCH_GITHUB=1，并自行完成 gh auth login。")
        result = subprocess.run(
            ["gh", "api", path],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        if result.returncode != 0:
            # gh 的错误可能含请求信息，不原样转发；只返回便于诊断的摘要。
            message = result.stderr.strip().splitlines()
            summary = next((line for line in message if "gh:" in line or "HTTP" in line), "gh api 请求失败")
            raise RuntimeError(f"GitHub 查询失败：{summary[:300]}")
        return json.loads(result.stdout)

    def _call(self, repository: str, endpoint: str) -> Any:
        repo = self._repository(repository)
        return self._call_path(f"repos/{repo.github}/{endpoint}")

    @staticmethod
    def _issue(item: dict[str, Any]) -> dict[str, Any]:
        return {"number": item["number"], "title": item["title"], "state": item["state"], "url": item["html_url"], "body": item.get("body") or ""}

    def release(self, repository: str, tag: str | None = None) -> dict[str, Any]:
        endpoint = "releases/latest" if tag is None else f"releases/tags/{tag}"
        data = self._call(repository, endpoint)
        return {"tag": data["tag_name"], "name": data.get("name") or "", "url": data["html_url"], "published_at": data.get("published_at"), "body": data.get("body") or "", "fixture": False}

    def issue(self, repository: str, number: int) -> dict[str, Any]:
        fixture = self._fixture(repository, number, "issue")
        if fixture is not None:
            return fixture
        return self._issue(self._call(repository, f"issues/{number}"))

    def pull_request(self, repository: str, number: int) -> dict[str, Any]:
        fixture = self._fixture(repository, number, "pull_request")
        if fixture is not None:
            return fixture
        return self._issue(self._call(repository, f"pulls/{number}"))

    def _fixture(self, repository: str, number: int, key: str) -> dict | None:
        self._repository(repository)
        if type(number) is not int or number < 1:
            raise ValueError("编号必须为正整数")
        state = StateStore(self.settings.state_path, self.settings).load()
        active = state.get("active_judgment") or {}
        event = active.get("event", {})
        expected = "issues.opened" if key == "issue" else "pull_request.opened"
        if event.get("fixture") is True and event.get("repository") == repository and event.get("type") == expected:
            item = event[key]
            if item["number"] == number:
                return {**item, "fixture": True, "source": "local-event", "event_id": event["event_id"]}
        return None

    def search_history(self, repository: str, query: str) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("检索词不能为空")
        repo = self._repository(repository)
        # 搜索词是 URL 查询参数，而不是 shell 命令；gh 不会把它当作代码执行。
        from urllib.parse import quote
        encoded = quote(f"repo:{repo.github} {query}")
        data = self._call_path(f"search/issues?q={encoded}&per_page=10")
        items = [self._issue(item) for item in data.get("items", [])]
        for item in items:
            item["url"] = item["url"].replace("api.github.com/repos", "github.com").replace("/issues/", "/issues/")
        return {"query": query, "total_count": data.get("total_count", 0), "items": items}
