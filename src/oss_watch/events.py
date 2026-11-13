"""统一校验本地事件；事件正文是数据，不构成工具授权。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings, safe_event_id

DISCUSSIONS = {"issues.opened": "issue", "pull_request.opened": "pull_request"}


def validate_event(event: Any, settings: Settings) -> dict:
    if not isinstance(event, dict):
        raise ValueError("事件必须是 JSON 对象")
    safe_event_id(event.get("event_id", ""))
    repository = event.get("repository")
    if not isinstance(repository, str) or repository not in settings.repositories:
        raise ValueError("事件仓库不在白名单内")
    if "fixture" in event and type(event["fixture"]) is not bool:
        raise ValueError("fixture 必须为布尔值")
    kind = event.get("type")
    if not isinstance(kind, str):
        raise ValueError("事件 type 必须为文本")
    key = "release" if kind == "release.published" else DISCUSSIONS.get(kind)
    if key is None:
        raise ValueError("仅支持 release.published、issues.opened 和 pull_request.opened")
    item = event.get(key)
    if not isinstance(item, dict):
        raise ValueError(f"事件缺少 {key} 对象")
    if key != "release" and (type(item.get("number")) is not int or item["number"] < 1):
        raise ValueError("Issue/PR 编号必须为正整数")
    for field in (("tag", "body") if key == "release" else ("title", "body")):
        if not isinstance(item.get(field), str) or (field != "body" and not item[field].strip()):
            raise ValueError(f"{key}.{field} 必须是有效文本")
    return event


def load_event(path: str | Path, settings: Settings) -> dict:
    return validate_event(json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8-sig")), settings)


def discussion_target(event: dict) -> int | None:
    key = DISCUSSIONS.get(event.get("type"))
    return event[key]["number"] if key else None
