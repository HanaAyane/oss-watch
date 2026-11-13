"""人工拍板后的唯一外发路径：只允许 Issue/PR 讨论区评论。"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .config import Settings, safe_event_id
from .events import discussion_target, validate_event
from .state import StateStore, now_iso


class CommentDelivery:
    def __init__(self, settings: Settings, store: StateStore):
        self.settings, self.store = settings, store

    def _update(self, event_id: str, **values) -> dict:
        with self.store.locked():
            state = self.store.load()
            review = next(item for item in reversed(state["reviews"]) if item["event_id"] == event_id)
            review.update(values)
            self.store.save(state)
            return {**review, "message": review["delivery_reason"] + "；补丁永远只保留本地草稿。"}

    def after_review(self, review: dict) -> dict:
        event_id = review["event_id"]
        reason = None
        event = review.get("event")
        if review["decision"] != "approve":
            reason = "已拒绝，未发出"
        elif not self.settings.send_comments:
            reason = "评论发送开关关闭，未发出"
        elif not event:
            reason = "旧轮次没有可核验的评论目标，未发出"
        else:
            validate_event(event, self.settings)
            if discussion_target(event) is None:
                reason = "版本适配仅记录本地决定，评论和补丁均未发出"
            elif event.get("fixture") is True:
                reason = "模拟事件禁止向真实讨论区发送，未发出"
            elif review.get("github_repository") != self.settings.repositories[event["repository"]].github:
                reason = "批准目标缺失或仓库映射已改变，未发出"
        if reason:
            return self._update(event_id, delivery_reason=reason)

        # 使用批准时冻结的正文及事件目标，绝不相信模型生成的 URL 或草稿目标参数。
        safe_event_id(event_id)
        expected_path = (self.settings.runs_root / event_id / "comment.md").resolve()
        path = Path(review["artifacts"]["comment"]).resolve()
        try:
            if path != expected_path or not path.is_relative_to(self.settings.runs_root.resolve()):
                raise ValueError("评论路径不属于本轮")
            content = path.read_bytes()
            if not review.get("comment_sha256") or hashlib.sha256(content).hexdigest() != review["comment_sha256"]:
                raise ValueError("评论草稿在提交后改变")
            body = content.decode("utf-8")
            if not body.strip() or len(body) > 60000:
                raise ValueError("评论正文为空或过长")
        except (OSError, ValueError) as error:
            return self._update(event_id, delivery_reason=f"草稿校验失败，未发出：{error}")

        try:
            auth = subprocess.run(["gh", "auth", "status", "--hostname", "github.com"],
                                  capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return self._update(event_id, delivery_reason="本机 GitHub 凭据不可用，未发出")
        if auth.returncode != 0:
            return self._update(event_id, delivery_reason="本机 GitHub 凭据不可用，未发出")

        repository = self.settings.repositories[event["repository"]].github
        endpoint = f"repos/{repository}/issues/{discussion_target(event)}/comments"
        # 先记录尝试再发出：断电、网络超时等结果不明时不自动重发，避免重复评论。
        with self.store.locked():
            state = self.store.load()
            current = next(item for item in reversed(state["reviews"]) if item["event_id"] == event_id)
            if current.get("delivery_attempted_at"):
                return {**current, "message": "已尝试发送，不会自动重发；请核对讨论区。"}
            current.update(delivery="sending", delivery_reason="已记录发送尝试，结果待确认", delivery_attempted_at=now_iso())
            self.store.save(state)
        try:
            sent = subprocess.run(["gh", "api", "--hostname", "github.com", "--method", "POST", endpoint, "--input", "-"],
                                  input=json.dumps({"body": body}, ensure_ascii=False),
                                  capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
            if sent.returncode != 0:
                return self._update(event_id, delivery="unknown", delivery_reason="发送失败或结果不明，请人工核对讨论区；不会自动重发")
            url = json.loads(sent.stdout)["html_url"]
            if not isinstance(url, str) or not url.startswith(f"https://github.com/{repository}/"):
                raise ValueError("无效的评论回执")
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            return self._update(event_id, delivery="unknown", delivery_reason="发送结果不明，请人工核对讨论区；不会自动重发")
        return self._update(event_id, delivery="sent", delivery_reason="评论已发出", comment_url=url)
