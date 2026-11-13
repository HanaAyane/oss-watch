"""小型 JSON 状态库：原子写入，并保留审批历史。"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "processed_events": {}, "last_conclusion": None, "pending_approval": None, "reviews": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("本地状态版本不受支持")
        return data

    @contextmanager
    def locked(self, suffix: str = ".lock"):
        """跨 CLI/MCP 串行化短暂写入；锁冲突直接返回，不空转等待。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(suffix)
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise ValueError(f"状态正在写入或本轮 Pi 正在运行；若进程异常终止，请确认进程已退出后移走 {lock}") from error
        try:
            os.close(descriptor)
            yield
        finally:
            lock.unlink()

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def write_artifact(self, path: Path, content: str) -> Path:
        """一期草稿只允许首次写入；重放事件不能覆盖已经看过的内容。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as file:
                file.write(content)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != content:
                raise ValueError(f"产物已存在且内容不同，拒绝覆盖：{path}")
        return path

    def finish_pending(self, event: dict[str, Any], conclusion: dict[str, Any], artifacts: dict[str, str]) -> None:
        state = self.load()
        event_id = event["event_id"]
        if event_id in state["processed_events"]:
            raise ValueError(f"事件已处理：{event_id}")
        if state.get("pending_approval") is not None:
            raise ValueError("上一轮仍在待拍板")
        active = state.get("active_judgment")
        if active is not None and active["event"]["event_id"] != event_id:
            raise ValueError("另一轮 Pi 判断尚未结束")
        comment = Path(artifacts["comment"]).read_bytes()
        record = {"event_id": event_id, "type": event["type"], "event": event,
                  "comment_sha256": hashlib.sha256(comment).hexdigest(),
                  "status": "pending_approval", "processed_at": now_iso(), "artifacts": artifacts}
        state["processed_events"][event_id] = record
        state["last_conclusion"] = {"event_id": event_id, **conclusion}
        state["pending_approval"] = {**record, "conclusion": conclusion}
        state.pop("active_judgment", None)
        self.save(state)

    def review(self, event_id: str, decision: str, note: str) -> dict[str, Any]:
        with self.locked():
            return self._review(event_id, decision, note)

    def _review(self, event_id: str, decision: str, note: str) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision 只能是 approve 或 reject")
        state = self.load()
        pending = state.get("pending_approval")
        if pending is None or pending.get("event_id") != event_id:
            raise ValueError("该事件当前不在待拍板状态")
        status = "approved" if decision == "approve" else "rejected"
        review = {"event_id": event_id, "decision": decision, "note": note.strip(), "reviewed_at": now_iso(),
                  "delivery": "not-sent", "delivery_reason": "仅记录本地决定，未发出",
                  "event": pending.get("event"), "artifacts": dict(pending["artifacts"]),
                  "github_repository": pending.get("conclusion", {}).get("github_repository"),
                  "comment_sha256": pending.get("comment_sha256"), "patch_delivery": "local-only"}
        state["reviews"].append(review)
        state["processed_events"][event_id]["status"] = status
        state["pending_approval"] = None
        self.save(state)
        return {**review, "message": "已记录本地决定，未发出；补丁永远只保留本地草稿。"}
