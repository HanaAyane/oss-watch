"""SQLite 收件箱叫醒一轮交互 Pi；待拍板或异常时退出，事实始终落盘。"""
from __future__ import annotations

import time
from pathlib import Path

from .config import Settings
from .events import load_event
from .pi_judgment import PiJudgment
from .state import StateStore


class EventInbox:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = StateStore(settings.state_path, settings)
        self.pi = PiJudgment(settings)

    def enqueue(self, source: str | Path) -> dict:
        event = load_event(source, self.settings)
        with self.store.locked():
            self.store.enqueue(event)
        return {"status": "queued", "event_id": event["event_id"], "database": str(self.store.path),
                "message": "已入队；运行中的 watch 会扫描到事件，已退出的 watch 需手动重启。"}

    def inspect(self) -> dict:
        events = self.events()
        return {"inbox": str(self.store.path), "database": str(self.store.path),
                "queued_events": [event["event_id"] for event in events],
                "queued_files": [event["event_id"] + ".json" for event in events]}

    def events(self) -> list[dict]:
        queue = self.store.load()["queue"]
        # 保留旧版按 <event_id>.json 文件名排序的顺序。
        return [queue[key] for key in sorted(queue, key=lambda key: key + ".json")]

    def _archive(self, event: dict) -> None:
        with self.store.locked():
            self.store.archive(event)

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
                events = self.events()
                if not events:
                    if once:
                        return {"status": "idle", **self.inspect()}
                    time.sleep(interval)
                    continue
                event = events[0]
                prepared = self.pi.prepare(event)
                if prepared["status"] == "already_processed":
                    self._archive(event)
                    continue
                result = self.pi.launch(prepared)
                self._archive(event)
                return {**result, **self.inspect(), "message": "一轮已停在审批门；等待器退出，其他事件留在收件箱。"}
