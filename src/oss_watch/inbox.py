"""本地文件叫醒一轮交互 Pi；待拍板或异常时退出，队列留在磁盘。"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from .config import Settings
from .events import load_event
from .pi_judgment import PiJudgment
from .state import StateStore


class EventInbox:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = StateStore(settings.state_path)
        self.root = settings.state_path.parent / "inbox"
        self.archive = settings.state_path.parent / "inbox-archive"
        self.pi = PiJudgment(settings)

    def enqueue(self, source: str | Path) -> dict:
        event = load_event(source, self.settings)
        with self.store.locked():
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / (event["event_id"] + ".json")
            if path.exists():
                if load_event(path, self.settings) != event:
                    raise ValueError("队列中相同事件 ID 的正文不同，拒绝覆盖")
            else:
                temporary = self.root / (uuid.uuid4().hex + ".tmp")
                try:
                    temporary.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        return {"status": "queued", "event_id": event["event_id"], "path": str(path),
                "message": "已投递；前台 watch 等待器将自动派一轮 Pi，待拍板时保留队列。"}

    def inspect(self) -> dict:
        return {"inbox": str(self.root), "queued_files": [path.name for path in self.files()]}

    def files(self) -> list[Path]:
        return sorted(self.root.glob("*.json"))

    def _archive(self, path: Path, event: dict) -> None:
        with self.store.locked():
            if load_event(path, self.settings) != event:
                raise ValueError("处理期间收件文件发生变化，保留文件等待人工核对")
            self.archive.mkdir(parents=True, exist_ok=True)
            destination = self.archive / (event["event_id"] + ".json")
            if destination.exists() and load_event(destination, self.settings) != event:
                raise ValueError("归档中相同事件 ID 正文不同，保留收件文件")
            os.replace(path, destination)

    def run(self, *, once: bool = False, interval: float = 1.0) -> dict:
        if not 0.1 <= interval <= 60:
            raise ValueError("扫描间隔必须在 0.1 到 60 秒之间")
        with self.store.locked(".watch.lock"):
            while True:
                state = self.store.load()
                pending = state.get("pending_approval")
                active = state.get("active_judgment")
                if pending or active:
                    blocking = pending if pending else active["event"]
                    return {"status": "blocked", "blocking_event_id": blocking["event_id"],
                            **self.inspect(), "message": "已有待拍板或未完成轮次，等待器退出；先拍板或 pi --resume-round，再启动 watch。"}
                paths = self.files()
                if not paths:
                    if once:
                        return {"status": "idle", **self.inspect()}
                    time.sleep(interval)
                    continue
                path = paths[0]
                if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
                    raise ValueError("收件文件必须位于本地收件箱内，禁止符号链接")
                event = load_event(path, self.settings)
                prepared = self.pi.prepare(event)
                if prepared["status"] == "already_processed":
                    self._archive(path, event)
                    continue
                result = self.pi.launch(prepared)
                self._archive(path, event)
                return {**result, **self.inspect(), "message": "一轮已停在审批门；等待器退出，其他事件留在收件箱。"}
