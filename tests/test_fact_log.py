"""事实库、一次迁移及事务边界；只在临时目录操作。"""
import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import test_complete_loop as loop
from oss_watch.config import load_settings
from oss_watch.state import StateStore


class FactLogTests(unittest.TestCase):
    setUp = loop.CompleteLoopTests.setUp
    tearDown = loop.CompleteLoopTests.tearDown
    file = loop.CompleteLoopTests.file
    complete = loop.CompleteLoopTests.complete
    pending = loop.CompleteLoopTests.pending

    def migrated_store(self):
        path = self.root / "legacy" / "oss-watch.sqlite"
        settings = replace(self.settings, state_path=path)
        return StateStore(path, settings)

    def write_legacy(self, state=None, inbox=(), archive=()):
        root = self.root / "legacy"
        root.mkdir(exist_ok=True)
        if state is not None:
            (root / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        for name, events in (("inbox", inbox), ("inbox-archive", archive)):
            (root / name).mkdir(exist_ok=True)
            for event in events:
                (root / name / (event["event_id"] + ".json")).write_text(json.dumps(event), encoding="utf-8")
        return {str(p): p.read_bytes() for p in root.rglob("*.json")}

    def old_state(self):
        return {k: v for k, v in self.store.load().items() if k not in {"events", "queue", "archived_events"}}

    def test_enqueue_is_queryable_and_idempotent_and_survives_new_process(self):
        self.inbox.enqueue(self.file(self.event))
        self.inbox.enqueue(self.file(self.event))
        with closing(sqlite3.connect(self.store.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            rows = connection.execute("SELECT payload FROM facts WHERE type='event_enqueued'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][0])["event"], self.event)
        code = "import json,sys; from pathlib import Path; from oss_watch.state import StateStore; print(json.dumps(StateStore(Path(sys.argv[1])).load()))"
        result = subprocess.run([sys.executable, "-c", code, str(self.store.path)],
                                capture_output=True, text=True, check=True)
        self.assertIn(self.event["event_id"], json.loads(result.stdout)["queue"])
        self.assertEqual(self.store.load(), StateStore.project(self.store.facts()))
        self.assertFalse((self.store.path.parent / "inbox").exists())

    def test_full_lifecycle_is_append_only_and_rebuilds_approvals_and_rejections(self):
        for decision in ("approve", "reject"):
            self.event["event_id"] = "event-" + decision
            self.inbox.enqueue(self.file(self.event))
            self.pi.prepare(self.event)
            self.complete()
            self.inbox._archive(self.event)
            before = self.store.facts()
            self.gate.review(self.event["event_id"], decision, "本地拍板")
            after = self.store.facts()
            self.assertEqual(after[:len(before)], before)
            self.assertIn("approved" if decision == "approve" else "rejected", [f["type"] for f in after[len(before):]])
            # 只复制事实到新库，没有任何投影或旧 JSON，状态仍完整重建。
            rebuilt = self.root / (decision + ".sqlite")
            with closing(sqlite3.connect(self.store.path)) as source, closing(sqlite3.connect(rebuilt)) as destination:
                source.backup(destination)
            self.assertEqual(StateStore(rebuilt).load(), self.store.load())
        kinds = {f["type"] for f in self.store.facts()}
        self.assertTrue({"event_enqueued", "judgment_started", "draft_recorded", "judgment_submitted", "inbox_archived", "approved", "rejected"} <= kinds)
        payloads = json.dumps(self.store.facts(), ensure_ascii=False)
        self.assertNotIn("中文回复草稿：", payloads)
        self.assertNotIn("仅诊断建议，补丁永远留在本地。", payloads)
        with closing(sqlite3.connect(self.store.path)) as connection:
            for sql in ("UPDATE facts SET type='broken'", "DELETE FROM facts"):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(sql)

    def test_pending_queue_archive_and_review_history_migrate_once_without_touching_backups(self):
        self.pending()
        self.gate.review(self.event["event_id"], "reject", "已拒绝旧事件")
        archived = copy.deepcopy(self.event)
        self.event["event_id"] = "pending-second"
        self.pending()
        old = self.old_state()
        queued = {**self.event, "event_id": "queued-third"}
        before = self.write_legacy(old, inbox=[queued], archive=[archived])
        store = self.migrated_store()
        migrated = store.load()
        self.assertEqual({k: migrated[k] for k in old}, old)
        self.assertEqual(migrated["queue"], {queued["event_id"]: queued})
        self.assertEqual(migrated["archived_events"], {archived["event_id"]: archived})
        self.assertEqual({p: Path(p).read_bytes() for p in before}, before)
        count = len(store.facts())
        # 成功后旧文件即使被外部改坏，也不会再成为运行输入。
        (store.path.parent / "state.json").write_text("{", encoding="utf-8")
        self.assertEqual(store.load(), migrated)
        self.assertEqual(len(store.facts()), count)
        review = store.review(self.event["event_id"], "approve", "迁移后拍板")
        self.assertEqual(review["delivery"], "not-sent")
        self.assertIsNone(store.load()["pending_approval"])
        self.assertEqual(store.load(), StateStore.project(store.facts()))

    def test_active_round_with_existing_drafts_migrates_and_resumes(self):
        self.pi.prepare(self.event)
        self.gate.draft_comment(self.event["event_id"], "target", None, "已有评论")
        old = self.old_state()
        self.write_legacy(old, inbox=[self.event])
        store = self.migrated_store()
        self.assertEqual(store.load()["active_judgment"], old["active_judgment"])
        settings = replace(self.settings, state_path=store.path)
        from oss_watch.pi_judgment import PiJudgment
        self.assertEqual(PiJudgment(settings).prepare(self.event, resume=True)["status"], "judging")

    def test_conflicting_migration_rolls_back_every_fact_and_can_retry_empty_database(self):
        self.pending()
        original = self.old_state()
        changed = copy.deepcopy(self.event)
        changed["issue"]["body"] = "冲突正文"
        before = self.write_legacy(original, inbox=[changed], archive=[self.event])
        store = self.migrated_store()
        with self.assertRaisesRegex(ValueError, "正文不同"):
            store.load()
        with closing(sqlite3.connect(store.path)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM facts").fetchone()[0], 0)
        self.assertEqual({p: Path(p).read_bytes() for p in before}, before)
        path = store.path.parent / "inbox" / (self.event["event_id"] + ".json")
        path.write_text(json.dumps(self.event), encoding="utf-8")
        self.assertEqual(store.load()["pending_approval"], original["pending_approval"])

    def test_migration_rejects_invalid_body_and_foreign_repository(self):
        for invalid in ({**self.event, "repository": "foreign"}, {**self.event, "issue": {"number": 0}}):
            self.write_legacy(inbox=[invalid])
            with self.assertRaises(ValueError):
                self.migrated_store().load()

    def test_database_lock_conflict_is_immediate_and_does_not_append(self):
        self.store.load()
        before = self.store.facts()
        with closing(sqlite3.connect(self.store.path, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "锁冲突"):
                self.store.enqueue(self.event)
            connection.rollback()
        self.assertEqual(self.store.facts(), before)
        self.store.enqueue(self.event)

    def test_failed_multi_fact_transaction_has_no_partial_commit(self):
        self.store.load()
        before = self.store.facts()
        with self.assertRaisesRegex(RuntimeError, "模拟中断"):
            with self.store._transaction() as (connection, state):
                self.store._append(connection, "event_enqueued", {"event": self.event}, self.event["event_id"])
                raise RuntimeError("模拟中断")
        self.assertEqual(self.store.facts(), before)

    def test_parallel_claims_only_create_one_active_round(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        def worker():
            barrier.wait()
            try:
                results.append(self.pi.prepare(self.event))
            except ValueError as error:
                errors.append(error)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(sum(f["type"] == "judgment_started" for f in self.store.facts()), 1)

    def test_conflict_remains_rejected_after_archive_or_direct_judgment(self):
        self.pending()
        self.gate.review(self.event["event_id"], "approve", "本地")
        self.store.enqueue(self.event)
        self.store.archive(self.event)
        changed = copy.deepcopy(self.event)
        changed["issue"]["body"] = "另一份正文"
        with self.assertRaisesRegex(ValueError, "正文不同"):
            self.store.enqueue(changed)
        self.assertEqual(self.store.load()["queue"], {})

    def test_cli_processes_share_isolated_database_without_launching_model_or_sending(self):
        with tempfile.TemporaryDirectory(prefix=".test-cli-state-", dir=self.settings.project_root) as temporary:
            state_root = Path(temporary)
            environment = dict(os.environ, OSS_WATCH_VAR=temporary,
                               OSS_WATCH_GITHUB="0", OSS_WATCH_SEND_COMMENTS="0")
            def cli(*args):
                result = subprocess.run([sys.executable, "demo.py", *args], env=environment,
                                        cwd=self.settings.project_root, capture_output=True,
                                        text=True, encoding="utf-8", check=True)
                # watch 在 JSON 前有一行提示。
                return json.loads(result.stdout[result.stdout.index("{"):])
            for decision in ("approve", "reject"):
                event = {"event_id": "cli-" + decision, "repository": "target", "fixture": True,
                         "type": "release.published", "release": {"tag": "v-test", "body": "协议验证"}}
                source = state_root / "source.json"
                source.write_text(json.dumps(event), encoding="utf-8")
                queued = cli("enqueue", str(source))
                self.assertEqual(queued["database"], str(state_root / "oss-watch.sqlite"))
                self.assertEqual(cli("status")["queued_events"], [event["event_id"]])
                self.assertEqual(cli("demo", str(source))["status"], "pending_approval")
                self.assertEqual(cli("watch", "--once")["status"], "blocked")
                self.assertEqual(cli(decision, event["event_id"])["delivery"], "not-sent")
                self.assertEqual(cli("watch", "--once")["status"], "idle")
                self.assertEqual(cli("status")["reviews"][-1]["decision"], decision)
            self.assertFalse((state_root / "state.json").exists())

    def test_database_and_wal_resolved_escape_is_rejected_by_configuration(self):
        project = self.root / "project"
        state = project / "isolated"
        state.mkdir(parents=True)
        original_resolve = Path.resolve
        for filename in ("oss-watch.sqlite", "oss-watch.sqlite-wal", "oss-watch.sqlite-shm"):
            def resolve(path, *args, **kwargs):
                # Windows 无创建链接权限时也能覆盖解析后越界的分支。
                if path == state / filename:
                    return self.root / (filename + "-outside")
                return original_resolve(path, *args, **kwargs)
            with patch("oss_watch.config.PROJECT_ROOT", project), \
                 patch.dict("os.environ", {"OSS_WATCH_VAR": str(state)}), \
                 patch.object(Path, "resolve", resolve):
                with self.assertRaisesRegex(ValueError, "逃逸"):
                    load_settings()


if __name__ == "__main__":
    unittest.main()
