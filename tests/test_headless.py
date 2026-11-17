"""headless 启动、超时看门狗、日志与锁诊断；不调用真实模型。"""
import json
import logging
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_workflow as fixtures
from oss_watch.config import load_settings
from oss_watch.doctor import inspect_locks
from oss_watch.logging_setup import setup_logging
from oss_watch.pi_judgment import PiJudgment


class HeadlessTests(unittest.TestCase):
    def setUp(self):
        fixtures.WorkflowTests.setUp(self)
        self.pi = PiJudgment(self.settings)

    def tearDown(self):
        fixtures.WorkflowTests.tearDown(self)

    def fake_process(self, returncode=0, wait_side_effect=None):
        process = SimpleNamespace(pid=4321)
        # 只有带超时的首次 wait 抛超时；杀树后的收尾 wait() 正常返回。
        process.wait = lambda timeout=None: (wait_side_effect(timeout) if wait_side_effect and timeout is not None else returncode)
        process.kill = lambda: None
        return process

    def test_headless_is_default_and_writes_output_files(self):
        prepared = self.pi.prepare(self.event)
        with patch("oss_watch.pi_judgment.shutil.which", return_value="pi.cmd"), \
             patch("oss_watch.pi_judgment.subprocess.Popen", return_value=self.fake_process()) as popen:
            with self.assertRaisesRegex(ValueError, "未完成"):
                self.pi.launch(prepared)
            args = popen.call_args.args[0]
            self.assertIn("-p", args)
            self.assertIn("--mode", args)
            self.assertIn("--session-dir", args)
            self.assertIn("mcp__oss_watch__record_approval", args)
            self.assertEqual(popen.call_args.kwargs["env"]["OSS_WATCH_SEND_COMMENTS"], "0")
        run_root = self.settings.runs_root / self.event["event_id"]
        self.assertTrue((run_root / "pi-output.jsonl").is_file())
        self.assertTrue((run_root / "pi-stderr.log").is_file())
        self.assertTrue((run_root / "session").is_dir())
        # 未完成轮次保持活动，可接续。
        self.assertEqual(self.store.load()["active_judgment"]["event"], self.event)

    def test_interactive_flag_keeps_tui_without_print_flags(self):
        prepared = self.pi.prepare(self.event)
        with patch("oss_watch.pi_judgment.shutil.which", return_value="pi.cmd"), \
             patch("oss_watch.pi_judgment.subprocess.run", return_value=SimpleNamespace(returncode=1)) as run, \
             patch("oss_watch.pi_judgment.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "未完成"):
                self.pi.launch(prepared, interactive=True)
            self.assertNotIn("-p", run.call_args.args[0])
            popen.assert_not_called()

    def test_timeout_kills_tree_records_fact_and_allows_resume(self):
        prepared = self.pi.prepare(self.event)
        def hang(timeout):
            raise subprocess.TimeoutExpired("pi", timeout)
        with patch("oss_watch.pi_judgment.shutil.which", return_value="pi.cmd"), \
             patch("oss_watch.pi_judgment.subprocess.Popen", return_value=self.fake_process(wait_side_effect=hang)), \
             patch("oss_watch.pi_judgment.subprocess.run") as kill:
            with self.assertRaisesRegex(ValueError, "超过 .* 秒已终止"):
                self.pi.launch(prepared)
            if kill.call_args is not None:  # Windows 走 taskkill /T；其他平台走 process.kill
                self.assertEqual(kill.call_args.args[0][:3], ["taskkill", "/T", "/F"])
        active = self.store.load()["active_judgment"]
        self.assertEqual(active["timeouts"], 1)
        self.assertEqual(active["last_timeout"]["timeout_seconds"], self.settings.judgment_timeout_seconds)
        self.assertEqual(self.pi.prepare(self.event, resume=True)["status"], "judging")
        # 超时事实可重放：投影与事实一致。
        self.assertEqual(self.store.load(), self.store.project(self.store.facts()))

    def test_timeout_fact_rejected_without_matching_active_round(self):
        with self.assertRaises(ValueError):
            self.store.record_timeout(self.event["event_id"], 900)

    def test_judgment_timeout_config_validated(self):
        config = Path(self.settings.repositories["target"].root).parent / "config.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        for bad in (5, "900", True, 99999):
            data["judgment"] = {"timeout_seconds": bad}
            config.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                load_settings()
        data["judgment"] = {"timeout_seconds": 120}
        config.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(load_settings().judgment_timeout_seconds, 120)

    def test_log_records_nodes_without_event_body(self):
        logger = setup_logging(self.settings.state_path.parent)
        self.pi.prepare(self.event)
        logger.info("启动 Pi 判断 event=%s mode=%s", self.event["event_id"], "headless")
        for handler in logger.handlers:
            handler.flush()
        text = (self.settings.state_path.parent / "logs" / "oss-watch.log").read_text(encoding="utf-8")
        self.assertIn(self.event["event_id"], text)
        self.assertNotIn(self.event["release"]["body"], text)
        logging.getLogger("oss_watch").handlers.clear()

    def test_lock_owner_is_recorded_and_doctor_reports_alive(self):
        import os
        with self.store.locked(".pi.lock"):
            owner = json.loads((self.settings.state_path.parent / "state.pi.lock").read_text(encoding="utf-8"))
            self.assertEqual(owner["pid"], os.getpid())
            report = inspect_locks(self.settings)
            self.assertEqual(len(report["locks"]), 1)
            self.assertIs(report["locks"][0]["pid_alive"], True)
            self.assertIn("不要动锁", report["locks"][0]["advice"])
        self.assertEqual(inspect_locks(self.settings)["locks"], [])

    def test_doctor_reports_dead_and_legacy_locks_without_deleting(self):
        self.settings.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.settings.state_path.parent / "state.lock"
        lock.write_text(json.dumps({"pid": 0x7FFFFFFF, "started_at": "test"}), encoding="utf-8")
        legacy = self.settings.state_path.parent / "state.watch.lock"
        legacy.write_text("", encoding="utf-8")
        report = inspect_locks(self.settings)
        by_name = {Path(item["lock"]).name: item for item in report["locks"]}
        self.assertIs(by_name["state.lock"]["pid_alive"], False)
        self.assertIn("可人工移走", by_name["state.lock"]["advice"])
        self.assertIsNone(by_name["state.watch.lock"]["pid_alive"])
        self.assertTrue(lock.exists() and legacy.exists())

    def test_lock_conflict_message_includes_owner(self):
        with self.store.locked():
            with self.assertRaisesRegex(ValueError, "持有者"):
                self.pi.prepare(self.event)


if __name__ == "__main__":
    unittest.main()
