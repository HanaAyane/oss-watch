"""loop 常驻、通知降级与 review 审阅视图；不调用真实模型、不弹真实通知。"""
import copy
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_complete_loop as loop
from oss_watch.notify import notify
from oss_watch.review import render_review


class DaemonReviewTests(unittest.TestCase):
    setUp = loop.CompleteLoopTests.setUp
    tearDown = loop.CompleteLoopTests.tearDown
    file = loop.CompleteLoopTests.file
    complete = loop.CompleteLoopTests.complete
    pending = loop.CompleteLoopTests.pending

    def test_loop_waits_for_review_then_continues_next_event(self):
        first = copy.deepcopy(self.event)
        second = copy.deepcopy(self.event)
        second["event_id"] = "loop-second"
        self.inbox.enqueue(self.file(first))
        self.inbox.enqueue(self.file(second))
        results, errors = [], []
        seen_pending = []
        def worker():
            try:
                with patch.object(self.inbox.pi, "launch", side_effect=self.complete):
                    results.append(self.inbox.run(interval=0.1, notify=False, loop=True))
            except KeyboardInterrupt:
                pass  # 测试用 Ctrl+C 语义停掉常驻 loop，属预期退出。
            except BaseException as error:
                errors.append(error)
        # 替身 sleep 扮演「人」：看到待拍板就拒绝；两轮都拍板后停掉 loop。
        def human(seconds):
            pending = self.store.load().get("pending_approval")
            if pending is not None:
                seen_pending.append(pending["event_id"])
                self.gate.review(pending["event_id"], "reject", "loop 测试拍板")
                if len(seen_pending) >= 2:
                    raise KeyboardInterrupt
        with patch("oss_watch.inbox.time.sleep", side_effect=human):
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            thread.join(10)
        self.assertFalse(thread.is_alive(), "loop 未按预期退出")
        self.assertFalse(errors, errors)
        self.assertEqual(seen_pending, [first["event_id"], second["event_id"]])
        state = self.store.load()
        self.assertEqual(state["processed_events"][first["event_id"]]["status"], "rejected")
        self.assertEqual(state["processed_events"][second["event_id"]]["status"], "rejected")
        self.assertEqual(self.inbox.events(), [])

    def test_loop_failure_returns_needs_resume_without_retry(self):
        self.inbox.enqueue(self.file(self.event))
        with patch.object(self.inbox.pi, "launch", side_effect=ValueError("模型中断")) as launch, \
             patch("oss_watch.notify.notify") as note:
            result = self.inbox.run(once=True, loop=True, notify=True)
            self.assertEqual(result["status"], "needs-resume")
            launch.assert_called_once()
            note.assert_called_once()
        self.assertEqual(len(self.inbox.events()), 1)

    def test_loop_notifies_once_per_pending_event(self):
        self.pending()
        calls, ticks = [], []
        import time
        original_sleep = time.sleep
        def sleep_and_count(seconds):
            # 通知按事件去重后只发一次，不能拿通知次数当停止条件，用心跳计数。
            ticks.append(seconds)
            if len(ticks) >= 3:
                raise KeyboardInterrupt
            original_sleep(0.01)
        with patch("oss_watch.notify.notify", side_effect=lambda t, m: calls.append(m)), \
             patch("oss_watch.inbox.time.sleep", side_effect=sleep_and_count):
            with self.assertRaises(KeyboardInterrupt):
                self.inbox.run(loop=True, notify=True)
        self.assertEqual(len(calls), 1)
        self.assertIn(self.event["event_id"], calls[0])

    def test_notify_encodes_payload_instead_of_command_line_args(self):
        # PS 5.1 的 -Command 会把剩余参数拼进脚本执行；必须走 -EncodedCommand 传文案。
        import base64, json as jsonlib, re
        with patch("oss_watch.notify.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(notify("标题 有空格", "内容：needs_info（review 查看草稿）"), "toast")
        argv = run.call_args.args[0]
        self.assertIn("-EncodedCommand", argv)
        self.assertNotIn("-Command", argv)
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        payload = jsonlib.loads(base64.b64decode(re.search(r"FromBase64String\('([^']+)'\)", script).group(1)))
        self.assertEqual(payload, {"title": "标题 有空格", "message": "内容：needs_info（review 查看草稿）"})

    def test_notify_falls_back_to_bell_when_toast_fails(self):
        with patch("oss_watch.notify.subprocess.run", return_value=SimpleNamespace(returncode=1)):
            self.assertEqual(notify("标题", "内容"), "bell")
        with patch("oss_watch.notify.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            self.assertEqual(notify("标题", "内容"), "toast")
        with patch("oss_watch.notify.subprocess.run", side_effect=OSError):
            self.assertEqual(notify("标题", "内容"), "bell")

    def test_review_renders_pending_with_commands_and_is_read_only(self):
        self.pending()
        before = self.store.facts()
        text = render_review(self.settings)
        self.assertIn(self.event["event_id"], text)
        self.assertIn("待拍板", text)
        self.assertIn(f"python demo.py approve {self.event['event_id']}", text)
        self.assertIn("comment", text)
        self.assertEqual(self.store.facts(), before)

    def test_review_history_and_empty_state(self):
        self.assertIn("没有待拍板", render_review(self.settings))
        self.pending()
        self.gate.review(self.event["event_id"], "reject", "历史")
        text = render_review(self.settings, self.event["event_id"])
        self.assertIn("rejected", text)
        self.assertNotIn("python demo.py approve", text)
        with self.assertRaises(ValueError):
            render_review(self.settings, "no-such-event")


if __name__ == "__main__":
    unittest.main()
