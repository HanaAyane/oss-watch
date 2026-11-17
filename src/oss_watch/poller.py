"""按仓库独立频率的只读轮询器：到期才联网，事件走既有 enqueue 幂等入队。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from .config import Repository, Settings
from .github import GitHubReader
from .logging_setup import get_logger
from .state import StateStore, now_iso

FAILURE_BACKOFF_SECONDS = 900
MAX_BODY = 60_000


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_slug(value: str) -> str:
    return "".join(c if c.isascii() and (c.isalnum() or c in "._-") else "-" for c in value)


class EventPoller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = StateStore(settings.state_path, settings)
        self.github = GitHubReader(settings)

    def due(self, repo: Repository, record: dict | None, now: datetime, force: bool) -> bool:
        if force or record is None:
            return True
        last = record.get("attempted_at")
        if not last:
            return True
        interval = repo.poll_interval_seconds if record.get("ok") else min(repo.poll_interval_seconds, FAILURE_BACKOFF_SECONDS)
        return now >= _parse_time(last) + timedelta(seconds=interval)

    def poll(self, *, once: bool = True, repo: str | None = None, force: bool = False,
             tick: float = 60.0) -> dict:
        if not self.settings.github_enabled:
            raise PermissionError("轮询需要只读 GitHub 访问；在 .env 设置 OSS_WATCH_GITHUB=1 并完成 gh auth login。")
        if not 60 <= tick <= 3600:
            raise ValueError("tick 必须在 60 到 3600 秒之间")
        targets = list(self.settings.repositories.values())
        if repo is not None:
            if repo not in self.settings.repositories:
                raise ValueError(f"未配置的仓库：{repo}")
            targets = [self.settings.repositories[repo]]
        import time
        while True:
            results = self.poll_once(targets, force=force)
            if once:
                return {"status": "polled", "results": results}
            time.sleep(tick)

    def poll_once(self, targets: list[Repository], *, force: bool = False) -> list[dict]:
        state = self.store.load()
        results = []
        for repository in targets:
            if not repository.poll_enabled and not force:
                results.append({"repository": repository.name, "status": "disabled"})
                continue
            record = state["poll"].get(repository.name)
            now = datetime.now().astimezone()
            if not self.due(repository, record, now, force):
                results.append({"repository": repository.name, "status": "not-due"})
                continue
            results.append(self._poll_repository(repository, record, now, state))
        return results

    def _poll_repository(self, repository: Repository, record: dict | None, now: datetime, state: dict | None = None) -> dict:
        logger = get_logger()
        # 首次启用不回溯存量：水位从启用时刻开始。
        cursor = (record or {}).get("cursor") or now_iso()
        attempted_at = now_iso()
        enqueued: list[str] = []
        # GitHub 返回 Z 时区，本地水位带偏移；必须按时间比较，不能比字符串。
        cursor_time = _parse_time(cursor)
        watermark, watermark_time = cursor, cursor_time
        try:
            issues = self.github._call_path(
                f"repos/{repository.github}/issues?state=all&sort=updated&direction=desc&since={quote(cursor)}&per_page=50")
            releases = self.github._call_path(f"repos/{repository.github}/releases?per_page=10")
            known = (state or {}).get("events", {})
            for item in issues:
                created = item.get("created_at") or ""
                if not created or _parse_time(created) < cursor_time or (item.get("user") or {}).get("login", "").endswith("[bot]"):
                    continue
                event = self._discussion_event(repository, item)
                if event["event_id"] not in known:
                    self.store.enqueue(event)
                    enqueued.append(event["event_id"])
                if _parse_time(created) > watermark_time:
                    watermark, watermark_time = created, _parse_time(created)
            for item in releases:
                published = item.get("published_at") or ""
                if not published or _parse_time(published) < cursor_time or item.get("draft"):
                    continue
                event = self._release_event(repository, item)
                if event["event_id"] not in known:
                    self.store.enqueue(event)
                    enqueued.append(event["event_id"])
                if _parse_time(published) > watermark_time:
                    watermark, watermark_time = published, _parse_time(published)
        except Exception as error:  # 网络/gh/事件校验失败都只记退避，不影响其他仓库。
            self.store.record_poll({"repository": repository.name, "attempted_at": attempted_at,
                                    "ok": False, "cursor": cursor, "error": str(error)[:300]})
            logger.warning("轮询失败 repo=%s error=%s", repository.name, str(error)[:200])
            return {"repository": repository.name, "status": "failed", "error": str(error)[:300]}
        self.store.record_poll({"repository": repository.name, "attempted_at": attempted_at,
                                "ok": True, "cursor": watermark, "enqueued": enqueued})
        logger.info("轮询完成 repo=%s enqueued=%s", repository.name, enqueued)
        return {"repository": repository.name, "status": "ok", "cursor": watermark, "enqueued": enqueued}

    @staticmethod
    def _body(item: dict) -> str:
        body = item.get("body") or ""
        if len(body) > MAX_BODY:
            return body[:MAX_BODY] + "\n\n（正文过长，已由轮询器截断；完整内容见原链接）"
        return body

    def _discussion_event(self, repository: Repository, item: dict) -> dict:
        is_pr = "pull_request" in item
        key = "pull_request" if is_pr else "issue"
        slug = _safe_slug(repository.github)
        return {"event_id": f"gh-{slug}-{key}-{item['number']}-opened",
                "repository": repository.name, "fixture": False,
                "type": "pull_request.opened" if is_pr else "issues.opened",
                key: {"number": item["number"], "title": item.get("title") or f"{key} #{item['number']}",
                      "body": self._body(item), "url": item.get("html_url", ""),
                      "state": item.get("state", ""), "created_at": item.get("created_at", "")}}

    def _release_event(self, repository: Repository, item: dict) -> dict:
        slug = _safe_slug(repository.github)
        return {"event_id": f"gh-{slug}-release-{_safe_slug(item['tag_name'])}",
                "repository": repository.name, "fixture": False, "type": "release.published",
                "release": {"tag": item["tag_name"], "body": self._body(item),
                            "url": item.get("html_url", ""), "published_at": item.get("published_at", "")}}
