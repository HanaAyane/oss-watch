"""配置、路径边界和本地环境变量。不会打印任何环境变量值。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_local_env() -> None:
    """读取项目 .env；已经存在的进程环境变量优先。"""
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


@dataclass(frozen=True)
class Repository:
    name: str
    root: Path
    github: str
    writable: bool


@dataclass(frozen=True)
class Settings:
    project_root: Path
    state_path: Path
    runs_root: Path
    fixtures_root: Path
    repositories: dict[str, Repository]
    github_enabled: bool
    delivery_mode: str
    send_comments: bool = False


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"配置项 {field} 必须是非空字符串")
    return value.strip()


def load_settings() -> Settings:
    load_local_env()
    config_path = Path(os.environ.get("OSS_WATCH_CONFIG", PROJECT_ROOT / "config" / "watch.json"))
    config_path = config_path.expanduser().resolve()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("仅支持 schema_version = 1 的配置")
    repositories: dict[str, Repository] = {}
    for name, item in data.get("repositories", {}).items():
        if not isinstance(item, dict):
            raise ValueError(f"仓库 {name} 的配置必须是对象")
        github = _text(item.get("github"), f"{name}.github")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", github):
            raise ValueError("github 必须写成 owner/repo")
        root_value = os.environ.get("OSS_WATCH_TARGET_ROOT") if name == "dsh-reasoning-effort" else None
        root = Path(root_value or _text(item.get("root"), f"{name}.root")).expanduser()
        if not root.is_absolute():
            root = config_path.parent / root
        root = root.resolve()
        if not root.is_dir():
            raise ValueError(f"只读仓库目录不存在：{root}")
        if item.get("writable") is True:
            raise ValueError("被值守仓库必须只读，禁止 writable: true")
        repositories[name] = Repository(name, root, github, False)
    delivery_mode = data.get("delivery", {}).get("mode", "draft-only")
    if delivery_mode != "draft-only":
        raise ValueError("第一期只支持 delivery.mode = draft-only")
    var_root = Path(os.environ.get("OSS_WATCH_VAR", PROJECT_ROOT / "var")).expanduser().resolve()
    if not var_root.is_relative_to(PROJECT_ROOT) or var_root == PROJECT_ROOT:
        raise ValueError("OSS_WATCH_VAR 必须是 oss-watch 项目内的子目录")
    for repository in repositories.values():
        if var_root.is_relative_to(repository.root) or repository.root.is_relative_to(var_root):
            raise ValueError("本地状态目录不能与被值守仓库重叠")
    for path in (var_root / "state.json", var_root / "runs", var_root / "inbox", var_root / "inbox-archive"):
        if not path.resolve().is_relative_to(var_root):
            raise ValueError("本地状态、草稿和收件箱不得通过链接逃逸")
    return Settings(
        project_root=PROJECT_ROOT,
        state_path=var_root / "state.json",
        runs_root=var_root / "runs",
        fixtures_root=PROJECT_ROOT / "fixtures",
        repositories=repositories,
        github_enabled=os.environ.get("OSS_WATCH_GITHUB", "0") == "1",
        delivery_mode=delivery_mode,
        send_comments=os.environ.get("OSS_WATCH_SEND_COMMENTS", "0") == "1",
    )


def repository_path(repository: Repository, relative_path: str) -> Path:
    """把工具参数限制在已配置仓库内，拒绝绝对路径、.. 和符号链接逃逸。"""
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("仓库文件路径必须是相对路径")
    candidate = (repository.root / relative_path).resolve()
    if not candidate.is_relative_to(repository.root):
        raise ValueError("不允许读取仓库目录之外的文件")
    if not candidate.is_file():
        raise FileNotFoundError(f"文件不存在：{relative_path}")
    return candidate


def safe_event_id(event_id: str) -> str:
    if not isinstance(event_id, str) or not event_id or len(event_id) > 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in event_id):
        raise ValueError("event_id 只能包含字母、数字、点、下划线和连字符，且不超过 128 字符")
    if event_id in {".", ".."}:
        raise ValueError("无效的 event_id")
    return event_id
