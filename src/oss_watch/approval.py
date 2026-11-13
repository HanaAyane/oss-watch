"""审批门：所有发送意图都只能到草稿，不提供绕过审批的执行入口。"""
from __future__ import annotations

from pathlib import Path

from .config import Settings
from .state import StateStore


class ApprovalGate:
    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store

    def inspect(self) -> dict:
        state = self.store.load()
        return {"delivery_mode": self.settings.delivery_mode, "pending_approval": state.get("pending_approval"), "reviews": state.get("reviews", [])[-20:]}

    def draft_comment(self, event_id: str, repository: str, target_number: int | None, body: str) -> dict:
        if (target_number is not None and target_number < 1) or not body.strip():
            raise ValueError("target_number 和评论正文必须有效")
        return self._draft(event_id, "comment", body, {"repository": repository, "target_number": target_number})

    def draft_patch(self, event_id: str, repository: str, path: str, patch: str) -> dict:
        destination = Path(path)
        if destination.is_absolute() or len(destination.parts) != 1 or destination.suffix != ".md" or not patch.strip():
            raise ValueError("补丁草稿必须使用单层 .md 文件名和有效内容")
        return self._draft(event_id, "patch", patch, {"repository": repository, "path": path}, destination)

    def _draft(self, event_id: str, kind: str, body: str, metadata: dict, destination: Path | None = None) -> dict:
        from .config import safe_event_id
        safe_event_id(event_id)
        state = self.store.load()
        pending = state.get("pending_approval")
        if pending is None or pending.get("event_id") != event_id:
            raise ValueError("只能为当前待拍板轮次写草稿")
        path = self.settings.runs_root / event_id / (destination or Path(f"{kind}.md"))
        self.store.write_artifact(path, body)
        pending["artifacts"][kind] = str(path)
        processed = state["processed_events"].get(event_id)
        if processed is not None:
            processed["artifacts"][kind] = str(path)
        self.store.save(state)
        return {"status": "pending_approval", "delivery": "not-sent", "artifact": str(path), **metadata}
