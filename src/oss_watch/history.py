"""历史快照与仓库文档的定向检索，不使用向量库。"""
from __future__ import annotations

import json
from typing import Any

from .config import Settings, repository_path


class HistoryReader:
    def __init__(self, settings: Settings):
        self.settings = settings

    def search(self, repository: str, query: str) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("检索词不能为空")
        terms = [term.casefold() for term in query.split()]
        results: list[dict[str, Any]] = []
        path = self.settings.fixtures_root / "history-snapshot.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            same_repo = data.get("github") == self.settings.repositories[repository].github
            for kind, items in (("issue", data.get("issues", []) if same_repo else []), ("pull_request", data.get("pull_requests", []) if same_repo else [])):
                for item in items:
                    text = " ".join(str(value) for value in item.values())
                    if all(term in text.casefold() for term in terms):
                        results.append({"kind": kind, **item})
        for file_path in sorted(self.settings.repositories[repository].root.glob("*.md")):
            file_path = repository_path(self.settings.repositories[repository], file_path.name)
            text = file_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for number, line in enumerate(lines, 1):
                if all(term in line.casefold() for term in terms):
                    results.append({"kind": "document", "path": file_path.name, "line": number, "excerpt": line[:500]})
        return {"query": query, "results": results, "sources": ["fixtures/history-snapshot.json", "仓库根目录 Markdown 文档"]}
