"""第一期闭环测试；使用临时仓库副本，不修改被值守仓库。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oss_watch.approval import ApprovalGate
from oss_watch.config import PROJECT_ROOT, Settings, load_settings, safe_event_id
from oss_watch.github import GitHubReader
from oss_watch.history import HistoryReader
from oss_watch.repository import RepositoryReader
from oss_watch.state import StateStore
from oss_watch.workflow import VersionWatchWorkflow


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="oss-watch-test-")
        with patch.dict(os.environ, {"OSS_WATCH_CONFIG": str(PROJECT_ROOT / "config" / "watch.json"), "OSS_WATCH_TARGET_ROOT": str(PROJECT_ROOT.parent / "插件" / "dsh-reasoning-effort")}, clear=False):
            source_root = load_settings().repositories["dsh-reasoning-effort"].root
        self.root = Path(self.temp.name)
        self.repo = self.root / "target"
        self.repo.mkdir()
        for relative in ("src/client/native-selector.tsx", "package.json", "CHANGELOG.md", "README.md"):
            source = source_root / relative
            destination = self.repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(json.dumps({"schema_version": 1, "repositories": {"target": {"root": str(self.repo), "github": "HanaAyane/dsh-reasoning-effort", "writable": False}}, "delivery": {"mode": "draft-only"}}), encoding="utf-8")
        self.env = patch.dict(os.environ, {"OSS_WATCH_CONFIG": str(config), "OSS_WATCH_TARGET_ROOT": str(self.repo), "OSS_WATCH_GITHUB": "0"})
        self.env.start()
        self.settings = load_settings()
        self.settings = Settings(
            project_root=self.settings.project_root,
            repositories=self.settings.repositories,
            fixtures_root=self.settings.fixtures_root,
            github_enabled=False,
            delivery_mode="draft-only",
            state_path=self.root / "var" / "state.json",
            runs_root=self.root / "var" / "runs",
        )
        self.workflow = VersionWatchWorkflow(self.settings)
        self.store = StateStore(self.settings.state_path)
        self.gate = ApprovalGate(self.settings, self.store)
        self.event = json.loads((PROJECT_ROOT / "fixtures" / "release-v0.3.0-rc.1.json").read_text(encoding="utf-8"))
        self.event["repository"] = "target"

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_real_source_produces_pending_review_and_drafts(self) -> None:
        result = self.workflow.run(self.event)
        self.assertEqual(result["status"], "pending_approval")
        self.assertEqual(result["conclusion"]["decision"], "需要修改")
        self.assertEqual(result["delivery"], "not-sent")
        self.assertTrue(any(item["path"] == "src/client/native-selector.tsx" for item in result["conclusion"]["evidence"]))
        patch = Path(result["artifacts"]["patch"]).read_text(encoding="utf-8")
        self.assertIn("data-dsh-pane", patch)
        self.assertIn("--- a/src/client/native-selector.tsx", patch)
        self.assertIn("+++ b/src/client/native-selector.tsx", patch)
        self.assertIn("const structuredMenu", patch)
        self.assertNotIn("const nativeEffort =", patch)
        self.assertFalse((self.repo / "src/client/native-selector.tsx").read_text(encoding="utf-8").find("data-dsh-pane") >= 0)
        replay = self.workflow.run(self.event)
        self.assertEqual(replay["status"], "already_processed")
        self.assertEqual(self.store.load()["pending_approval"]["event_id"], self.event["event_id"])

    def test_approval_records_decision_without_delivery(self) -> None:
        self.workflow.run(self.event)
        with self.assertRaises(ValueError):
            self.gate.draft_comment("other-event", "target", None, "不该写入")
        with self.assertRaises(ValueError):
            self.gate.draft_patch(self.event["event_id"], "target", "../escape.md", "内容")
        draft = self.gate.draft_patch(self.event["event_id"], "target", "reviewed-change.md", "# 审查后的补丁\n")
        self.assertTrue(Path(draft["artifact"]).is_file())
        state = self.store.load()
        self.assertEqual(state["pending_approval"]["artifacts"]["patch"], draft["artifact"])
        self.assertEqual(state["processed_events"][self.event["event_id"]]["artifacts"]["patch"], draft["artifact"])
        review = self.store.review(self.event["event_id"], "approve", "先留档")
        self.assertEqual(review["delivery"], "not-sent")
        self.assertIsNone(self.store.load()["pending_approval"])
        with self.assertRaises(ValueError):
            self.store.review(self.event["event_id"], "approve", "重复审批")

    def test_unsupported_contract_does_not_fabricate_patch(self) -> None:
        del self.event["compatibility"]
        result = self.workflow.run(self.event)
        self.assertEqual(result["conclusion"]["decision"], "无法自动判断")
        self.assertNotIn("patch", result["artifacts"])

    def test_changed_source_stops_instead_of_guessing(self) -> None:
        path = self.repo / "src/client/native-selector.tsx"
        path.write_text(path.read_text(encoding="utf-8").replace("const [modelRow, effortRow, ...options]", "const [modelRow, nativeEffortRow, ...options]"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "前提不符"):
            self.workflow.run(self.event)
        self.assertIsNone(self.store.load()["pending_approval"])

class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="oss-watch-tool-test-")
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src/a.ts").write_text("菜单标记\n", encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(json.dumps({"schema_version": 1, "repositories": {"target": {"root": str(self.root), "github": "HanaAyane/dsh-reasoning-effort"}}, "delivery": {"mode": "draft-only"}}), encoding="utf-8")
        with patch.dict(os.environ, {"OSS_WATCH_CONFIG": str(config), "OSS_WATCH_TARGET_ROOT": str(self.root), "OSS_WATCH_GITHUB": "0"}):
            self.settings = load_settings()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_read_search_and_path_fence(self) -> None:
        reader = RepositoryReader(self.settings)
        self.assertIn("菜单标记", reader.read("target", "src/a.ts")["content"])
        self.assertEqual(reader.search("target", "菜单")["matches"][0]["line"], 1)
        for path in ("../secret.txt", str(self.root / "absolute.txt")):
            with self.assertRaises(ValueError):
                reader.read("target", path)
        with self.assertRaises(ValueError):
            safe_event_id("../escape")

    def test_github_is_opt_in(self) -> None:
        with self.assertRaises(PermissionError):
            GitHubReader(self.settings).release("target")
        self.assertFalse(any(callable(getattr(GitHubReader(self.settings), name, None)) for name in ("create_issue", "create_comment", "create_pull_request")))

    def test_github_read_only_paths_are_well_formed(self) -> None:
        settings = replace(self.settings, github_enabled=True)
        issue = {"number": 8, "title": "移动端", "state": "open", "html_url": "https://github.com/x/y/issues/8", "body": "视口"}
        responses = [
            SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"tag_name": "v1.0.0", "name": "x", "html_url": "https://example.invalid", "body": ""})),
            SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(issue)),
            SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(issue)),
            SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"total_count": 1, "items": [issue]})),
        ]
        reader = GitHubReader(settings)
        with patch("oss_watch.github.subprocess.run", side_effect=responses) as run:
            reader.release("target")
            reader.issue("target", 8)
            reader.pull_request("target", 8)
            result = reader.search_history("target", "移动端")
        paths = [call.args[0][2] for call in run.call_args_list]
        self.assertEqual(paths[:3], ["repos/HanaAyane/dsh-reasoning-effort/releases/latest", "repos/HanaAyane/dsh-reasoning-effort/issues/8", "repos/HanaAyane/dsh-reasoning-effort/pulls/8"])
        self.assertTrue(paths[3].startswith("search/issues?q=repo%3AHanaAyane/dsh-reasoning-effort"))
        self.assertEqual(result["items"][0]["number"], 8)

    def test_local_history_is_directional(self) -> None:
        result = HistoryReader(self.settings).search("target", "移动端")
        self.assertTrue(result["results"])
        self.assertTrue(any(item.get("number") == 8 for item in result["results"]))


if __name__ == "__main__":
    unittest.main()
