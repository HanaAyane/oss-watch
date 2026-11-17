"""轮询器：到期调度、事件转换、幂等与失败退避；gh 全部用替身。"""
import copy
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import test_workflow as fixtures
from oss_watch.config import load_settings
from oss_watch.poller import EventPoller


def issue_item(number=42, created="2026-11-17T01:00:00Z", login="someone", pr=False):
    item = {"number": number, "title": "菜单溢出", "body": "窄视口下溢出", "state": "open",
            "html_url": f"https://github.com/HanaAyane/dsh-reasoning-effort/issues/{number}",
            "created_at": created, "updated_at": created, "user": {"login": login}}
    if pr:
        item["pull_request"] = {"url": "x"}
    return item


def release_item(tag="v0.3.0", published="2026-11-17T02:00:00Z"):
    return {"tag_name": tag, "body": "发版说明", "html_url": "https://github.com/x/y/releases/tag/" + tag,
            "published_at": published, "draft": False}


class PollerTests(unittest.TestCase):
    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        self.settings = replace(self.settings, github_enabled=True)
        self.poller = EventPoller(self.settings)
        self.repo = self.settings.repositories["target"]
        # 水位在样例事件之前；首拉不回溯存量的行为另有专门测试。
        self.store.record_poll({"repository": "target", "attempted_at": "2026-11-17T00:00:00+00:00",
                                "ok": True, "cursor": "2026-11-17T00:00:00Z"})

    def tearDown(self):
        fixtures.WorkflowTests.tearDown(self)

    def responses(self, issues=(), releases=()):
        def call(path):
            if "/issues?" in path:
                return list(issues)
            if "/releases?" in path:
                return list(releases)
            raise AssertionError(path)
        return call

    def test_poll_requires_online_switch(self):
        poller = EventPoller(replace(self.settings, github_enabled=False))
        with self.assertRaises(PermissionError):
            poller.poll()

    def test_poll_enqueues_new_items_and_advances_cursor(self):
        with patch.object(self.poller.github, "_call_path",
                          side_effect=self.responses([issue_item()], [release_item()])):
            result = self.poller.poll(force=True)["results"][0]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["enqueued"]), 2)
        queue = self.store.load()["queue"]
        self.assertIn("gh-HanaAyane-dsh-reasoning-effort-issue-42-opened", queue)
        self.assertIn("gh-HanaAyane-dsh-reasoning-effort-release-v0.3.0", queue)
        event = queue["gh-HanaAyane-dsh-reasoning-effort-issue-42-opened"]
        self.assertFalse(event["fixture"])
        self.assertEqual(event["type"], "issues.opened")
        self.assertEqual(event["issue"]["number"], 42)
        record = self.store.load()["poll"]["target"]
        self.assertTrue(record["ok"])
        self.assertEqual(record["cursor"], "2026-11-17T02:00:00Z")

    def test_second_poll_is_idempotent_and_skips_old_and_bots(self):
        with patch.object(self.poller.github, "_call_path",
                          side_effect=self.responses([issue_item()], [])):
            self.poller.poll(force=True)
        # 同批重拉 + 旧事件 + bot + PR 分流。
        items = [issue_item(), issue_item(number=1, created="2020-01-01T00:00:00Z"),
                 issue_item(number=2, login="dependabot[bot]"), issue_item(number=7, pr=True, created="2026-11-17T03:00:00Z")]
        with patch.object(self.poller.github, "_call_path", side_effect=self.responses(items, [])):
            result = self.poller.poll(force=True)["results"][0]
        self.assertEqual(result["enqueued"], ["gh-HanaAyane-dsh-reasoning-effort-pull_request-7-opened"])
        queue = self.store.load()["queue"]
        self.assertEqual(len(queue), 2)
        self.assertEqual(queue["gh-HanaAyane-dsh-reasoning-effort-pull_request-7-opened"]["type"], "pull_request.opened")

    def test_due_schedule_is_per_repository(self):
        now = datetime.now().astimezone()
        recent = {"attempted_at": (now - timedelta(seconds=120)).isoformat(), "ok": True, "cursor": "2026-11-17T00:00:00Z"}
        daily = replace(self.repo, poll_interval_seconds=86400)
        minute = replace(self.repo, poll_interval_seconds=60)
        self.assertFalse(self.poller.due(daily, recent, now, force=False))
        self.assertTrue(self.poller.due(minute, recent, now, force=False))
        self.assertTrue(self.poller.due(daily, None, now, force=False))
        self.assertTrue(self.poller.due(daily, recent, now, force=True))
        # 同一心跳：日级未到期零调用，分钟级到期拉取。
        self.store.record_poll({"repository": "target", **recent})
        with patch.object(self.poller.github, "_call_path", side_effect=self.responses()) as call:
            results = self.poller.poll_once([daily])
            self.assertEqual(results[0]["status"], "not-due")
            call.assert_not_called()
            results = self.poller.poll_once([minute])
            self.assertEqual(results[0]["status"], "ok")
            self.assertEqual(call.call_count, 2)

    def test_failure_backs_off_without_advancing_cursor(self):
        with patch.object(self.poller.github, "_call_path",
                          side_effect=self.responses([issue_item()], [])):
            self.poller.poll(force=True)
        cursor = self.store.load()["poll"]["target"]["cursor"]
        with patch.object(self.poller.github, "_call_path", side_effect=RuntimeError("网络中断")):
            result = self.poller.poll(force=True)["results"][0]
        self.assertEqual(result["status"], "failed")
        record = self.store.load()["poll"]["target"]
        self.assertFalse(record["ok"])
        self.assertEqual(record["cursor"], cursor)
        # 失败后按 min(interval, 900) 退避：日级仓库 16 分钟后即可重试，2 分钟时还不行。
        now = datetime.now().astimezone()
        daily = replace(self.repo, poll_interval_seconds=86400)
        sixteen_min = {"attempted_at": (now - timedelta(seconds=961)).isoformat(), "ok": False}
        self.assertTrue(self.poller.due(daily, sixteen_min, now, force=False))
        two_min = {"attempted_at": (now - timedelta(seconds=120)).isoformat(), "ok": False}
        self.assertFalse(self.poller.due(daily, two_min, now, force=False))

    def test_first_poll_does_not_backfill_history(self):
        # 无水位记录时从启用时刻开始：早于此刻的存量不入队。
        store_root = self.settings.state_path.parent / "fresh"
        settings = replace(self.settings, state_path=store_root / "oss-watch.sqlite")
        poller = EventPoller(settings)
        past = issue_item(created="2020-01-01T00:00:00Z")
        with patch.object(poller.github, "_call_path", side_effect=self.responses([past], [])):
            result = poller.poll(force=True)["results"][0]
        self.assertEqual(result["enqueued"], [])

    def test_disabled_repository_skipped_unless_forced(self):
        disabled = replace(self.repo, poll_enabled=False)
        with patch.object(self.poller.github, "_call_path", side_effect=self.responses()) as call:
            self.assertEqual(self.poller.poll_once([disabled])[0]["status"], "disabled")
            call.assert_not_called()
            self.assertEqual(self.poller.poll_once([disabled], force=True)[0]["status"], "ok")

    def test_poll_config_validated(self):
        from pathlib import Path
        config = Path(self.repo.root).parent / "config.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        for bad in ({"interval_seconds": 5}, {"interval_seconds": "60"}, {"enabled": "yes"}):
            data["repositories"]["target"]["poll"] = bad
            config.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings()
        data["repositories"]["target"]["poll"] = {"interval_seconds": 86400, "enabled": False}
        config.write_text(json.dumps(data), encoding="utf-8")
        repo = load_settings().repositories["target"]
        self.assertEqual(repo.poll_interval_seconds, 86400)
        self.assertFalse(repo.poll_enabled)

    def test_poll_facts_replay_and_long_body_truncated(self):
        long_item = issue_item()
        long_item["body"] = "长" * 70000
        with patch.object(self.poller.github, "_call_path", side_effect=self.responses([long_item], [])):
            self.poller.poll(force=True)
        event = self.store.load()["queue"]["gh-HanaAyane-dsh-reasoning-effort-issue-42-opened"]
        self.assertLessEqual(len(event["issue"]["body"]), 60100)
        self.assertIn("截断", event["issue"]["body"])
        self.assertEqual(self.store.load(), self.store.project(self.store.facts()))


if __name__ == "__main__":
    unittest.main()
