"""给人读的审阅视图：只读渲染待拍板或历史轮次，拍板仍走独立 approve/reject。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .config import Settings
from .state import StateStore


def render_review(settings: Settings, event_id: str | None = None, *, open_drafts: bool = False) -> str:
    store = StateStore(settings.state_path, settings)
    state = store.load()
    if event_id is None:
        record = state.get("pending_approval")
        if record is None:
            return "当前没有待拍板轮次。用 status 查看队列与历史；处理新事件用 enqueue / watch。"
        pending = True
    else:
        record = state["processed_events"].get(event_id)
        if record is None:
            raise ValueError(f"没有该事件的已处理记录：{event_id}")
        if state.get("pending_approval") and state["pending_approval"]["event_id"] == event_id:
            record = state["pending_approval"]
        pending = state.get("pending_approval") is not None and state["pending_approval"]["event_id"] == event_id
    conclusion = record.get("conclusion") or _read_conclusion(record)
    event = record.get("event", {})
    lines = [f"# 审阅：{record['event_id']}", ""]
    lines.append(f"- 仓库：{event.get('repository', conclusion.get('repository', '?'))}  类型：{event.get('type', '?')}"
                 f"{'  （模拟事件）' if event.get('fixture') is True else ''}")
    lines.append(f"- 状态：{'待拍板' if pending else record.get('status', '?')}")
    lines.append(f"- 判断：{conclusion.get('decision', '?')}")
    lines.append(f"- 置信度：{conclusion.get('confidence', '?')}")
    for title, key in (("理由", "reasons"), ("待验证", "validation_required")):
        items = conclusion.get(key) or []
        if items:
            lines += ["", f"## {title}"] + [f"- {item}" for item in items]
    evidence = conclusion.get("evidence") or []
    if evidence:
        lines += ["", "## 源码证据"]
        lines += [f"- {item.get('path')}:{item.get('line')} — {item.get('finding', '')}" for item in evidence]
    artifacts = record.get("artifacts", {})
    lines += ["", "## 产物"] + [f"- {kind}: {path}" for kind, path in artifacts.items()]
    if pending:
        lines += ["", "## 拍板（看完草稿后二选一，复制执行）", "",
                  f"    python demo.py approve {record['event_id']} --note \"同意\"",
                  f"    python demo.py reject {record['event_id']} --note \"理由\"", "",
                  "默认只留本地记录；真实 Issue/PR 评论需先开 OSS_WATCH_SEND_COMMENTS=1。"]
    if open_drafts:
        for kind in ("comment", "patch"):
            path = artifacts.get(kind)
            if path and Path(path).is_file():
                os.startfile(path)  # noqa: S606 — 打开本轮已核验路径的草稿供人审阅
    return "\n".join(lines)


def _read_conclusion(record: dict) -> dict:
    path = record.get("artifacts", {}).get("conclusion")
    if path and Path(path).is_file():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {}
