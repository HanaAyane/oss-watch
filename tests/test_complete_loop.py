"""隔离验证收件、Pi 提交、人工拍板和发送边界；不调用真实模型或外发。"""
import copy
import json
import os
import subprocess
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_workflow as fixtures
from oss_watch.approval import ApprovalGate
from oss_watch.cli import main
from oss_watch.config import PROJECT_ROOT, load_settings
from oss_watch.delivery import CommentDelivery
from oss_watch.events import load_event, validate_event
from oss_watch.github import GitHubReader
from oss_watch.history import HistoryReader
from oss_watch.inbox import EventInbox
from oss_watch.pi_judgment import PiJudgment
from oss_watch.services import create_services


class CompleteLoopTests(unittest.TestCase):
    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        self.pi = PiJudgment(self.settings)
        self.inbox = EventInbox(self.settings)
        self.release = self.event
        self.event = json.loads((PROJECT_ROOT / "fixtures/issue-menu-overflow.json").read_text(encoding="utf-8"))
        self.event["repository"] = "target"
        self.before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}

    def tearDown(self):
        try:
            after = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
            self.assertEqual(self.before, after, "被值守仓库发生写入")
        finally:
            fixtures.WorkflowTests.tearDown(self)

    def file(self, event):
        path = self.root / (event["event_id"] + "-source.json")
        path.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8-sig")
        return path

    def complete(self, prepared=None):
        event = self.store.load()["active_judgment"]["event"]
        event_id = event["event_id"]
        self.gate.draft_comment(event_id, "target", None, "中文回复草稿：请补充浏览器和视口。\n")
        self.gate.draft_patch(event_id, "target", "patch.md", "仅诊断建议，补丁永远留在本地。\n")
        return self.pi.submit(event_id, "建议补充复现条件", ["未验证运行时"],
                              [{"path": "package.json", "line": 1, "text": "{", "finding": "真实源码原文"}],
                              ["人工确认浏览器、版本和视口"], "证据有限")

    def pending(self, *, fixture=True, pr=False):
        event = copy.deepcopy(self.event)
        event["fixture"] = fixture
        if pr:
            event["type"] = "pull_request.opened"
            event["pull_request"] = event.pop("issue")
        self.pi.prepare(event)
        self.complete()
        return event

    def sender(self):
        return ApprovalGate(replace(self.settings, send_comments=True), self.store)

    def test_issue_and_pr_snapshots_use_existing_tools_without_network(self):
        for name, key in (("issue-menu-overflow.json", "issue"), ("pr-menu-position.json", "pull_request")):
            event = json.loads((PROJECT_ROOT / "fixtures" / name).read_text(encoding="utf-8"))
            event["repository"] = "target"
            self.pi.prepare(event)
            with patch("oss_watch.github.subprocess.run") as run:
                reader = GitHubReader(self.settings)
                result = getattr(reader, key)("target", event[key]["number"])
                self.assertEqual(result["source"], "local-event")
                self.assertTrue(result["fixture"])
                with self.assertRaises(PermissionError):
                    getattr(reader, key)("target", 999999)
                run.assert_not_called()
            self.assertIn("read_github_issue", Path(self.store.load()["active_judgment"]["prompt"]).read_text(encoding="utf-8"))
            self.complete()
            self.gate.review(event["event_id"], "reject", "模拟验收")

    def test_queue_blocks_without_overwriting_and_continues_after_review(self):
        first = self.inbox.enqueue(self.file(self.event))
        other = copy.deepcopy(self.event)
        other["event_id"] = "z-next-issue"
        self.inbox.enqueue(self.file(other))
        with patch.object(self.inbox.pi, "launch", side_effect=self.complete) as launch:
            self.assertEqual(self.inbox.run(once=True)["status"], "pending_approval")
            launch.assert_called_once()
            self.assertEqual(self.inbox.run(once=True)["status"], "blocked")
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(self.store.load()["last_conclusion"]["event_id"], self.event["event_id"])
        self.assertFalse(Path(first["path"]).exists())
        self.assertEqual(len(self.inbox.files()), 1)
        self.gate.review(self.event["event_id"], "reject", "继续下一轮")
        with patch.object(self.inbox.pi, "launch", side_effect=self.complete):
            self.assertEqual(self.inbox.run(once=True)["event_id"], other["event_id"])
        state = self.store.load()
        self.assertEqual(state["processed_events"][self.event["event_id"]]["status"], "rejected")
        self.assertEqual(state["pending_approval"]["event_id"], other["event_id"])

    def test_waiter_wakes_on_file_and_dispatches_only_one_round(self):
        entered = threading.Event()
        errors, results = [], []
        original_sleep = __import__("time").sleep
        def pause(seconds):
            entered.set()
            original_sleep(0.01)
        def worker():
            try:
                results.append(self.inbox.run(interval=0.1))
            except BaseException as error:
                errors.append(error)
        with patch("oss_watch.inbox.time.sleep", side_effect=pause), patch.object(self.inbox.pi, "launch", side_effect=self.complete) as launch:
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(3))
            self.inbox.enqueue(self.file(self.event))
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, errors)
            self.assertEqual(results[0]["status"], "pending_approval")
            launch.assert_called_once()

    def test_incomplete_round_preserves_event_and_resume_uses_saved_issue(self):
        self.inbox.enqueue(self.file(self.event))
        with patch.object(self.inbox.pi, "launch", side_effect=ValueError("模型中断")):
            with self.assertRaisesRegex(ValueError, "模型中断"):
                self.inbox.run(once=True)
        self.assertEqual(len(self.inbox.files()), 1)
        services = create_services(self.settings)
        with patch("oss_watch.cli.create_services", return_value=services), \
             patch("sys.argv", ["demo.py", "pi", "--resume-round", "--prepare-only"]), \
             patch("oss_watch.cli.print_json") as output:
            main()
        self.assertEqual(output.call_args.args[0]["event_id"], self.event["event_id"])
        self.assertEqual(self.inbox.run(once=True)["status"], "blocked")

    def test_duplicate_payload_conflicts_fail_without_overwriting(self):
        self.inbox.enqueue(self.file(self.event))
        changed = copy.deepcopy(self.event)
        changed["issue"]["body"] = "同 ID 的另一份正文"
        with self.assertRaises(ValueError):
            self.inbox.enqueue(self.file(changed))
        self.pending()
        with self.assertRaises(ValueError):
            self.pi.prepare(changed)
        with patch.object(self.inbox.pi, "launch") as launch:
            self.gate.review(self.event["event_id"], "approve", "本地")
            self.assertEqual(self.inbox.run(once=True)["status"], "idle")
            launch.assert_not_called()

    def test_invalid_event_and_mismatched_draft_target_are_rejected(self):
        for changes in ({"event_id": 2}, {"type": []}, {"fixture": "false"}, {"repository": "unknown"}, {"issue": {"number": True}}):
            with self.assertRaises(ValueError):
                validate_event({**self.event, **changes}, self.settings)
        self.pi.prepare(self.event)
        with self.assertRaises(ValueError):
            self.gate.draft_comment(self.event["event_id"], "target", 123, "错编号")

    def test_default_approval_and_rejection_do_not_call_github(self):
        self.pending(fixture=False)
        with patch("oss_watch.delivery.subprocess.run") as run:
            result = self.gate.review(self.event["event_id"], "approve", "默认关闭")
            self.assertEqual(result["delivery"], "not-sent")
            self.assertIn("开关关闭", result["delivery_reason"])
            run.assert_not_called()

    def test_reject_fixture_and_release_never_send_even_with_switch(self):
        self.pending()
        with patch("oss_watch.delivery.subprocess.run") as run:
            result = self.sender().review(self.event["event_id"], "approve", "模拟")
            self.assertIn("模拟事件", result["delivery_reason"])
            release = {**self.release, "fixture": False}
            self.pi.prepare(release)
            self.complete()
            result = self.sender().review(release["event_id"], "approve", "版本适配")
            self.assertIn("版本适配", result["delivery_reason"])
            self.assertEqual(result["patch_delivery"], "local-only")
            event = {**self.event, "event_id": "rejected-real", "fixture": False}
            self.pi.prepare(event)
            self.complete()
            self.assertEqual(self.sender().review(event["event_id"], "reject", "拒绝")["delivery"], "not-sent")
            run.assert_not_called()

    def test_missing_credentials_preserve_local_approval(self):
        self.pending(fixture=False)
        with patch("oss_watch.delivery.subprocess.run", return_value=SimpleNamespace(returncode=1)) as run:
            result = self.sender().review(self.event["event_id"], "approve", "没有凭据")
        self.assertEqual(run.call_count, 1)
        self.assertIn("凭据不可用", result["delivery_reason"])
        self.assertEqual(result["delivery"], "not-sent")
        self.assertEqual(self.store.load()["processed_events"][self.event["event_id"]]["status"], "approved")

    def test_send_only_discussion_comment_after_persisted_approval(self):
        self.pending(fixture=False, pr=True)
        calls = []
        def send(args, **kwargs):
            state = self.store.load()
            self.assertIsNone(state["pending_approval"])
            self.assertEqual(state["reviews"][-1]["decision"], "approve")
            calls.append(args)
            if args[1] == "auth":
                self.assertEqual(state["reviews"][-1]["delivery"], "not-sent")
                return SimpleNamespace(returncode=0)
            self.assertEqual(state["reviews"][-1]["delivery"], "sending")
            self.assertEqual(args, ["gh", "api", "--hostname", "github.com", "--method", "POST",
                                   "repos/HanaAyane/dsh-reasoning-effort/issues/100001/comments", "--input", "-"])
            self.assertEqual(json.loads(kwargs["input"])["body"], "中文回复草稿：请补充浏览器和视口。\n")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"html_url": "https://github.com/HanaAyane/dsh-reasoning-effort/pull/100001#issuecomment-42"}))
        with patch("oss_watch.delivery.subprocess.run", side_effect=send):
            result = self.sender().review(self.event["event_id"], "approve", "发评论")
            self.assertEqual(result["delivery"], "sent")
            with self.assertRaises(ValueError):
                self.sender().review(self.event["event_id"], "approve", "重复")
        self.assertEqual(len(calls), 2)

    def test_changed_draft_is_not_sent(self):
        self.pending(fixture=False)
        pending = self.store.load()["pending_approval"]
        Path(pending["artifacts"]["comment"]).write_text("偷偷修改", encoding="utf-8")
        with patch("oss_watch.delivery.subprocess.run") as run:
            result = self.sender().review(self.event["event_id"], "approve", "")
            self.assertIn("草稿校验失败", result["delivery_reason"])
            run.assert_not_called()

    def test_changed_repository_mapping_cannot_redirect_approved_comment(self):
        self.pending(fixture=False)
        repository = replace(self.settings.repositories["target"], github="example/different")
        settings = replace(self.settings, repositories={"target": repository}, send_comments=True)
        with patch("oss_watch.delivery.subprocess.run") as run:
            result = ApprovalGate(settings, self.store).review(self.event["event_id"], "approve", "")
            self.assertEqual(result["delivery"], "not-sent")
            self.assertIn("映射已改变", result["delivery_reason"])
            run.assert_not_called()

    def test_timeout_records_uncertainty_and_never_automatically_retries(self):
        self.pending(fixture=False)
        with patch("oss_watch.delivery.subprocess.run", side_effect=[SimpleNamespace(returncode=0), subprocess.TimeoutExpired("gh", 30)]) as run:
            result = self.sender().review(self.event["event_id"], "approve", "")
            self.assertEqual(result["delivery"], "unknown")
            self.assertEqual(run.call_count, 2)
        with patch("oss_watch.delivery.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            replay = CommentDelivery(replace(self.settings, send_comments=True), self.store).after_review(result)
            self.assertEqual(replay["delivery"], "unknown")
            self.assertEqual(run.call_count, 1)  # 仅 auth 检查，没有第二次 POST。

    def test_unrelated_repository_does_not_receive_history_snapshot(self):
        repository = replace(self.settings.repositories["target"], github="example/other")
        settings = replace(self.settings, repositories={"other": repository})
        self.assertFalse(any(item.get("number") == 8 for item in HistoryReader(settings).search("other", "移动端")["results"]))

    def test_second_waiter_and_invalid_head_file_fail_without_launch(self):
        with self.store.locked(".watch.lock"), patch.object(self.inbox.pi, "launch") as launch:
            with self.assertRaises(ValueError):
                self.inbox.run(once=True)
            launch.assert_not_called()

    def test_stale_prepared_round_never_launches_model_again(self):
        prepared = self.pi.prepare(self.event)
        self.complete()
        with patch("oss_watch.pi_judgment.subprocess.run") as run:
            self.assertEqual(self.pi.launch(prepared)["status"], "already_processed")
            run.assert_not_called()

    def test_real_issue_online_read_requires_explicit_switch_but_send_stays_off(self):
        self.event["fixture"] = False
        prepared = self.pi.prepare(self.event)
        enabled = PiJudgment(replace(self.settings, github_enabled=True, send_comments=True))
        with patch("oss_watch.pi_judgment.shutil.which", return_value="pi.cmd"), \
             patch("oss_watch.pi_judgment.subprocess.run", return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaises(ValueError):
                enabled.launch(prepared)
            self.assertEqual(run.call_args.kwargs["env"]["OSS_WATCH_GITHUB"], "1")
            self.assertEqual(run.call_args.kwargs["env"]["OSS_WATCH_SEND_COMMENTS"], "0")

    def test_config_rejects_writable_targets_and_state_overlap(self):
        config = self.root / "config.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data["repositories"]["target"]["writable"] = True
        config.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "只读"):
            load_settings()
        data["repositories"]["target"]["writable"] = False
        data["repositories"]["target"]["root"] = str(PROJECT_ROOT)
        config.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重叠"):
            load_settings()

    def test_state_outside_project_is_rejected(self):
        with patch.dict(os.environ, {"OSS_WATCH_VAR": str(self.root / "outside")}):
            with self.assertRaisesRegex(ValueError, "项目内"):
                load_settings()

    def test_invalid_head_file_stops_waiter_without_launch(self):
        self.inbox.root.mkdir(parents=True)
        (self.inbox.root / "broken.json").write_text("{", encoding="utf-8")
        with patch.object(self.inbox.pi, "launch") as launch:
            with self.assertRaises(ValueError):
                self.inbox.run(once=True)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
