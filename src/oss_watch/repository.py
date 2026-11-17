"""被值守仓库的只读文件工具。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import Settings, repository_path

# 构建产物与依赖目录既不是维护依据，也可能包含密钥或海量文件；读取与检索一律排除。
EXCLUDED_DIRECTORIES = {"node_modules", "lib", "dist", "build", "coverage"}


def _is_excluded(part: str) -> bool:
    return part.startswith(".") or part in EXCLUDED_DIRECTORIES


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
        repo = self._repository(repository)
        file_path = repository_path(repo, path)
        # 隐藏文件（如 .env）可能含密钥；与检索使用同一排除规则。
        if any(_is_excluded(part) for part in file_path.relative_to(repo.root).parts):
            raise ValueError("隐藏文件与构建产物不是维护依据，拒绝读取")
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
        folded = query.casefold()
        # 下降前剪掉排除目录，避免 rglob 全量枚举 node_modules 等海量文件。
        for directory, dirnames, filenames in os.walk(repo.root):
            dirnames[:] = sorted(name for name in dirnames if not _is_excluded(name))
            base = Path(directory)
            for name in sorted(filenames):
                if _is_excluded(name) or not base.joinpath(name).match(glob):
                    continue
                path = base / name
                if path.is_symlink():
                    continue
                try:
                    if path.stat().st_size > 500_000:
                        continue
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeError):
                    continue
                for number, line in enumerate(lines, 1):
                    if folded in line.casefold():
                        matches.append({"path": path.relative_to(repo.root).as_posix(), "line": number, "text": line.strip()[:500]})
                        if len(matches) >= max_results:
                            return {"repository": repository, "query": query, "matches": matches, "truncated": True}
        return {"repository": repository, "query": query, "matches": matches, "truncated": False}
