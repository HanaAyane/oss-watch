"""使用目标源码临时副本，验证 Pi 判断与人工审批之间的边界。"""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_workflow as fixtures
from oss_watch.pi_judgment import PiJudgment


class PiJudgmentTests(unittest.TestCase):
    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        self.pi = PiJudgment(self.settings)

    def tearDown(self):
        fixtures.WorkflowTests.tearDown(self)

    def evidence(self):
        return [{"path": "package.json", "line": 1, "text": "{", "finding": "已读取当前仓库清单；这里只验证证据落盘协议"}]

    def drafts(self):
        self.gate.draft_comment(self.event["event_id"], "target", None, "# Pi 评论草稿\n")
        self.gate.draft_patch(self.event["event_id"], "target", "patch.md", "# Pi 补丁草稿\n测试数据，未应用。\n")

    def submit(self, evidence=None):
        return self.pi.submit(self.event["event_id"], "留待人工验证", ["模型判断测试值"],
                              self.evidence() if evidence is None else evidence, ["实际运行时未验证"], "静态判断")

    def test_model_content_reaches_pending_without_fixed_rules(self):
        with patch.object(self.workflow, "run", side_effect=AssertionError("不应运行规则")):
            prepared = self.pi.prepare(self.event)
            self.assertEqual(prepared["status"], "judging")
            prompt = Path(prepared["prompt"]).read_text(encoding="utf-8")
            self.assertIn(json.dumps(self.event, ensure_ascii=False, indent=2), prompt)
            self.assertIsNone(self.store.load()["pending_approval"])
            with self.assertRaises(ValueError):
                self.store.review(self.event["event_id"], "approve", "不能审批未完成判断")
            self.drafts()
            result = self.submit()
        self.assertEqual(result["status"], "pending_approval")
        self.assertEqual(result["conclusion"]["engine"], "pi")
        self.assertEqual(result["conclusion"]["decision"], "留待人工验证")
        self.assertNotIn("active_judgment", self.store.load())
        for artifact in result["artifacts"].values():
            self.assertTrue(Path(artifact).is_file())
        with self.assertRaises(ValueError):
            self.gate.draft_patch(self.event["event_id"], "target", "new.md", "不许改待审草稿")
        review = self.store.review(self.event["event_id"], "approve", "仅本地")
        self.assertEqual(review["delivery"], "not-sent")
        self.assertEqual(self.pi.prepare(self.event)["status"], "already_processed")

    def test_incomplete_and_pending_rounds_block_new_events_and_rules(self):
        self.pi.prepare(self.event)
        other = copy.deepcopy(self.event)
        other["event_id"] = "another-release"
        for operation in (lambda: self.pi.prepare(other), lambda: self.pi.prepare(self.event),
                          lambda: self.workflow.run(other)):
            with self.assertRaises(ValueError):
                operation()
        self.drafts()
        self.submit()
        with self.assertRaises(ValueError):
            self.pi.prepare(other)
        self.assertEqual(self.pi.prepare(self.event)["status"], "already_processed")

    def test_resume_requires_identical_event_and_keeps_drafts(self):
        self.pi.prepare(self.event)
        self.drafts()
        changed = copy.deepcopy(self.event)
        changed["release"]["body"] = "相同 ID 的另一份正文"
        with self.assertRaises(ValueError):
            self.pi.prepare(changed, resume=True)
        self.assertEqual(self.pi.prepare(self.event, resume=True)["status"], "judging")
        self.assertIn("Pi 评论草稿", self.gate.inspect()["active_drafts"]["comment"]["content"])
        with self.assertRaises(ValueError):
            self.gate.draft_comment(self.event["event_id"], "target", None, "覆盖旧草稿")
        with self.assertRaises(ValueError):
            self.gate.draft_comment(self.event["event_id"], "another-repo", None, "跨仓库")
        with self.assertRaises(ValueError):
            self.gate.draft_patch(self.event["event_id"], "target", "comment.md", "覆盖评论")

    def test_submit_checks_artifacts_event_and_source_evidence(self):
        self.pi.prepare(self.event)
        with self.assertRaises(ValueError):
            self.submit()
        self.drafts()
        for evidence in ([], [{**self.evidence()[0], "text": "编造原文"}],
                         [{**self.evidence()[0], "path": "../outside"}]):
            with self.assertRaises(ValueError):
                self.submit(evidence)
        with self.assertRaises(ValueError):
            self.pi.submit("wrong-event", "判断", ["理由"], self.evidence(), ["待验证"], "低")
        self.assertIsNone(self.store.load()["pending_approval"])

    def test_launcher_is_interactive_restricted_and_reports_incomplete(self):
        prepared = self.pi.prepare(self.event)
        with patch("oss_watch.pi_judgment.shutil.which", return_value="pi.cmd"), \
             patch("oss_watch.pi_judgment.subprocess.run") as run:
            run.return_value.returncode = 0
            with self.assertRaisesRegex(ValueError, "未完成"):
                self.pi.launch(prepared)
            args = run.call_args.args[0]
            self.assertNotIn("-p", args)
            self.assertIn("--approve", args)
            self.assertIn("builtin:mcp", args)
            self.assertIn("mcp__oss_watch__record_approval", args)
            self.assertIn("@" + prepared["prompt"], args)
            self.assertEqual(run.call_args.kwargs["env"]["OSS_WATCH_GITHUB"], "0")
        self.assertEqual(self.store.load()["active_judgment"]["event"], self.event)

    def test_state_lock_rejects_concurrent_writers(self):
        with self.store.locked():
            with self.assertRaisesRegex(ValueError, "正在写入"):
                self.pi.prepare(self.event)
        self.assertEqual(self.pi.prepare(self.event)["status"], "judging")

    def test_second_launcher_cannot_join_running_pi(self):
        prepared = self.pi.prepare(self.event)
        with self.store.locked(".pi.lock"), patch("oss_watch.pi_judgment.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "正在运行"):
                self.pi.launch(prepared)
            run.assert_not_called()

    def test_successful_launcher_reports_only_matching_pending_round(self):
        prepared = self.pi.prepare(self.event)
        def complete(*args, **kwargs):
            self.drafts()
            self.submit()
            from types import SimpleNamespace
            return SimpleNamespace(returncode=0)
        with patch("oss_watch.pi_judgment.shutil.which", return_value="pi.cmd"), \
             patch("oss_watch.pi_judgment.subprocess.run", side_effect=complete):
            self.assertEqual(self.pi.launch(prepared)["status"], "pending_approval")
        with patch("oss_watch.pi_judgment.subprocess.run") as run:
            self.assertEqual(self.pi.launch(self.pi.prepare(self.event))["status"], "already_processed")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
