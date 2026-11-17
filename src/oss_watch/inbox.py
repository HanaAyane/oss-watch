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
        # 投影按事实追加顺序插入队列，这里保持入队先后，不再按旧文件名排序。
        return list(self.store.load()["queue"].values())

    def _archive(self, event: dict) -> None:
        with self.store.locked():
            self.store.archive(event)

    def run(self, *, once: bool = False, interval: float = 1.0, interactive: bool = False,
            loop: bool = False, notify: bool = True) -> dict:
        if not 0.1 <= interval <= 60:
            raise ValueError("扫描间隔必须在 0.1 到 60 秒之间")
        notified: set[str] = set()
        with self.store.locked(".watch.lock"):
            while True:
                state = self.store.load()
                pending = state.get("pending_approval")
                active = state.get("active_judgment")
                if pending or active:
                    blocking = pending if pending else active["event"]
                    blocked = {"status": "blocked", "blocking_event_id": blocking["event_id"],
                               **self.inspect(), "message": "已有待拍板或未完成轮次，等待器退出；先拍板或 pi --resume-round，再启动 watch。"}
                    if not loop or active:
                        # loop 只等人工拍板，不代接续未完成的判断。
                        return blocked
                    self._notify_pending(pending, notify, notified)
                    time.sleep(interval)
                    continue
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
                try:
                    result = self.pi.launch(prepared, interactive=interactive)
                except ValueError:
                    if not loop:
                        raise
                    # 判断失败/超时：事实与产物已保留，loop 停下等人接续，不空转重试烧 Token。
                    self._notify_text("oss-watch 判断未完成", f"{event['event_id']} 需 pi --resume-round 接续", notify)
                    return {"status": "needs-resume", "event_id": event["event_id"], **self.inspect(),
                            "message": "本轮未完成；产物已保留，接续后重新启动 watch --loop。"}
                self._archive(event)
                if not loop:
                    return {**result, **self.inspect(), "message": "一轮已停在审批门；等待器退出，其他事件留在收件箱。"}
                self._notify_pending(self.store.load().get("pending_approval"), notify, notified)
                # 停在审批门等人；拍板事实出现后自动接续下一条。

    def _notify_pending(self, pending: dict | None, enabled: bool, notified: set[str]) -> None:
        if pending is None or pending["event_id"] in notified:
            return
        notified.add(pending["event_id"])
        conclusion = pending.get("conclusion", {})
        self._notify_text("oss-watch 待拍板", f"{pending['event_id']}：{conclusion.get('decision', '')}（review 查看草稿）", enabled)

    @staticmethod
    def _notify_text(title: str, message: str, enabled: bool) -> None:
        if not enabled:
            return
        from .notify import notify
        notify(title, message)
