"""被值守仓库的只读文件工具。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings, repository_path


class RepositoryReader:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _repository(self, repository: str):
        try:
            return self.settings.repositories[repository]
        except KeyError as error:
            raise ValueError(f"未配置的仓库：{repository}") from error

    def read(self, repository: str, path: str, start_line: int = 1, end_line: int | None = None) -> dict[str, Any]:
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise ValueError("行号范围无效")
        file_path = repository_path(self._repository(repository), path)
        if file_path.stat().st_size > 1_000_000:
            raise ValueError("文件超过 1 MiB，请指定更小、更明确的读取对象")
        lines = file_path.read_text(encoding="utf-8").splitlines()
        stop = min(end_line or len(lines), len(lines), start_line + 399)
        selected = lines[start_line - 1:stop]
        return {"repository": repository, "path": path, "start_line": start_line, "end_line": stop, "total_lines": len(lines), "content": "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start_line))}

    def search(self, repository: str, query: str, glob: str = "*.ts", max_results: int = 30) -> dict[str, Any]:
        if not query or len(query) > 200 or max_results < 1 or max_results > 100:
            raise ValueError("检索参数无效")
        repo = self._repository(repository)
        matches = []
        # 仅扫描白名单文本文件；构建产物和隐藏目录不是维护依据。
        for path in repo.root.rglob(glob):
            if not path.is_file() or path.is_symlink() or any(part.startswith(".") or part in {"node_modules", "lib", "dist"} for part in path.relative_to(repo.root).parts[:-1]):
                continue
            try:
                if path.stat().st_size > 500_000:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for number, line in enumerate(lines, 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": path.relative_to(repo.root).as_posix(), "line": number, "text": line.strip()[:500]})
                    if len(matches) >= max_results:
                        return {"repository": repository, "query": query, "matches": matches, "truncated": True}
        return {"repository": repository, "query": query, "matches": matches, "truncated": False}
