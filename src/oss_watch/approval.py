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
        active = state.get("active_judgment")
        drafts = {}
        if active is not None:
            root = (self.settings.runs_root / active["event"]["event_id"]).resolve()
            for kind, value in active["artifacts"].items():
                path = Path(value).resolve()
                if path.is_relative_to(root) and path.is_file():
                    text = path.read_text(encoding="utf-8")
                    drafts[kind] = {"content": text[:30000], "truncated": len(text) > 30000}
        return {"delivery_mode": self.settings.delivery_mode, "send_comments": self.settings.send_comments,
                "active_judgment": active, "active_drafts": drafts, "pending_approval": state.get("pending_approval"),
                "last_conclusion": state.get("last_conclusion"),
                "processed_events": {key: value["status"] for key, value in state["processed_events"].items()},
                "reviews": state.get("reviews", [])[-20:]}

    def draft_comment(self, event_id: str, repository: str, target_number: int | None, body: str) -> dict:
        if (target_number is not None and target_number < 1) or not body.strip():
            raise ValueError("target_number 和评论正文必须有效")
        return self._draft(event_id, "comment", body, {"repository": repository, "target_number": target_number})

    def review(self, event_id: str, decision: str, note: str) -> dict:
        """仅 CLI 调用：先持久化人工决定，再按开关尝试发送讨论区评论。"""
        review = self.store.review(event_id, decision, note)
        from .delivery import CommentDelivery
        return CommentDelivery(self.settings, self.store).after_review(review)

    def draft_patch(self, event_id: str, repository: str, path: str, patch: str) -> dict:
        destination = Path(path)
        if destination.is_absolute() or len(destination.parts) != 1 or destination.suffix != ".md" or not patch.strip():
            raise ValueError("补丁草稿必须使用单层 .md 文件名和有效内容")
        return self._draft(event_id, "patch", patch, {"repository": repository, "path": path}, destination)

    def _draft(self, event_id: str, kind: str, body: str, metadata: dict, destination: Path | None = None) -> dict:
        with self.store.locked():
            return self._write_draft(event_id, kind, body, metadata, destination)

    def _write_draft(self, event_id: str, kind: str, body: str, metadata: dict, destination: Path | None) -> dict:
        from .config import safe_event_id
        safe_event_id(event_id)
        state = self.store.load()
        pending = state.get("pending_approval")
        active = state.get("active_judgment")
        is_active = active is not None and active["event"]["event_id"] == event_id
        if not is_active and (pending is None or pending.get("event_id") != event_id):
            raise ValueError("只能为当前待拍板轮次写草稿")
        repository = metadata["repository"]
        if repository not in self.settings.repositories:
            raise ValueError("草稿仓库不在白名单内")
        if is_active and active["event"]["repository"] != repository:
            raise ValueError("草稿仓库与当前事件不一致")
        if is_active and kind == "comment":
            from .events import discussion_target
            expected = discussion_target(active["event"])
            if metadata["target_number"] is not None and metadata["target_number"] != expected:
                raise ValueError("评论目标与本轮事件不一致")
            metadata["target_number"] = expected
        if not is_active and pending.get("conclusion", {}).get("engine") == "pi":
            raise ValueError("Pi 已提交判断，待拍板草稿不可变更")
        if destination is not None and destination.name in {"comment.md", "conclusion.md", "prompt.md"}:
            raise ValueError("补丁文件名与保留产物冲突")
        path = self.settings.runs_root / event_id / (destination or Path(f"{kind}.md"))
        if not path.resolve().is_relative_to(self.settings.runs_root.resolve()):
            raise ValueError("草稿路径不允许链接逃逸")
        self.store.write_artifact(path, body)
        record = active if is_active else pending
        record["artifacts"][kind] = str(path)
        processed = state["processed_events"].get(event_id)
        if processed is not None:
            processed["artifacts"][kind] = str(path)
        self.store.save(state)
        return {"status": "judging" if is_active else "pending_approval", "delivery": "not-sent", "artifact": str(path), **metadata}
