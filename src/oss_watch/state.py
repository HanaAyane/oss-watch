"""SQLite 追加式事实日志；当前视图每次从事实重建，草稿仍在文件中。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def lock_owner(lock: Path) -> dict | None:
    """读取锁内诊断信息；旧版空锁或损坏内容返回 None。"""
    try:
        owner = json.loads(lock.read_text(encoding="utf-8"))
        return owner if isinstance(owner, dict) else None
    except (OSError, ValueError):
        return None


def empty_state() -> dict:
    return {"schema_version": 1, "processed_events": {}, "last_conclusion": None,
            "pending_approval": None, "reviews": [], "queue": {}, "archived_events": {}, "events": {},
            "poll": {}}


class StateStore:
    def __init__(self, path: Path, settings=None):
        self.path = path
        self.settings = settings

    @contextmanager
    def locked(self, suffix: str = ".lock"):
        """文件锁保护草稿及单飞进程；冲突直接返回。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.parent / ("state" + suffix)
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            owner = lock_owner(lock)
            detail = f"（持有者 {owner}）" if owner else ""
            raise ValueError(f"状态正在写入或本轮 Pi 正在运行{detail}；可用 doctor 诊断，确认进程已退出后移走 {lock}") from error
        try:
            # 锁内容仅供诊断；写失败不影响互斥语义。
            try:
                os.write(descriptor, json.dumps({"pid": os.getpid(), "started_at": now_iso()}).encode("utf-8"))
            except OSError:
                pass
            os.close(descriptor)
            yield
        finally:
            lock.unlink()

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # 首开建库/迁移与并发读存在毫秒级窗口（CI 慢盘上必现）；短暂等待写锁，
            # 超时仍立刻报错不悬挂。文件锁语义不变，进程级互斥仍由 locked() 保证。
            with closing(sqlite3.connect(self.path, timeout=5, isolation_level=None)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA synchronous=FULL")
                # journal_mode 切换需要独占锁且不受 busy timeout 保护：并发首开时另一
                # 进程哪怕只持有毫秒级读事务也会让它立刻报锁。先建表（普通写，受
                # timeout 保护），确认没有并发活动后再一次性切 WAL；已是 WAL 则跳过。
                connection.execute("""CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                    type TEXT NOT NULL, event_id TEXT, payload TEXT NOT NULL)""")
                connection.execute("CREATE INDEX IF NOT EXISTS facts_event ON facts(event_id, id)")
                connection.execute("CREATE INDEX IF NOT EXISTS facts_type ON facts(type, id)")
                # 防止应用误把追加日志改成覆盖式状态表。
                connection.execute("""CREATE TRIGGER IF NOT EXISTS facts_no_update BEFORE UPDATE ON facts
                    BEGIN SELECT RAISE(ABORT, '事实日志禁止改写'); END""")
                connection.execute("""CREATE TRIGGER IF NOT EXISTS facts_no_delete BEFORE DELETE ON facts
                    BEGIN SELECT RAISE(ABORT, '事实日志禁止删除'); END""")
                if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                    for _ in range(50):
                        try:
                            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                            if str(mode).lower() == "wal":
                                break
                        except sqlite3.OperationalError as error:
                            if "lock" not in str(error) and "busy" not in str(error):
                                raise
                        # 并发连接占锁时 PRAGMA 返回 delete 或直接抛锁错误；短暂让位后重试。
                        time.sleep(0.1)
                    else:
                        raise sqlite3.OperationalError("database is locked (WAL switch)")
                if connection.execute("SELECT 1 FROM facts LIMIT 1").fetchone() is None:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        if connection.execute("SELECT 1 FROM facts LIMIT 1").fetchone() is None:
                            self._migrate(connection)
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
                yield connection
        except sqlite3.Error as error:
            raise ValueError(f"本地事实库不可用（锁冲突等待超时，请稍后重试）：{error}") from error

    @staticmethod
    def _append(connection, kind: str, payload: dict, event_id: str | None = None):
        connection.execute("INSERT INTO facts(ts, type, event_id, payload) VALUES (?, ?, ?, ?)",
                           (now_iso(), kind, event_id, json.dumps(payload, ensure_ascii=False)))

    @staticmethod
    def _facts(connection) -> list[dict]:
        return [{**dict(row), "payload": json.loads(row["payload"])}
                for row in connection.execute("SELECT * FROM facts ORDER BY id")]

    def facts(self) -> list[dict]:
        with self._connection() as connection:
            return self._facts(connection)

    @staticmethod
    def project(facts: list[dict]) -> dict:
        """纯投影：不依赖缓存表、旧 JSON 或产物文件内容。"""
        state = empty_state()
        for fact in facts:
            kind, event_id, data = fact["type"], fact["event_id"], copy.deepcopy(fact["payload"])
            if kind == "legacy_state_imported":
                state.update(data["state"])
                state["events"].update(data["events"])
            elif kind == "event_enqueued":
                state["events"][event_id] = data["event"]
                state["queue"][event_id] = data["event"]
            elif kind == "inbox_archived":
                state["events"][event_id] = data["event"]
                state["archived_events"][event_id] = data["event"]
                state["queue"].pop(event_id, None)
            elif kind == "judgment_started":
                state["events"][event_id] = data["event"]
                state["active_judgment"] = data
            elif kind == "judgment_timed_out":
                active = state.get("active_judgment")
                if active is None or (active.get("event") or {}).get("event_id") != event_id:
                    raise ValueError("超时事实必须对应当前活动轮次")
                active["timeouts"] = active.get("timeouts", 0) + 1
                active["last_timeout"] = data
            elif kind == "draft_recorded":
                for record in (state.get("active_judgment"), state.get("pending_approval"),
                               state["processed_events"].get(event_id)):
                    if record and (record.get("event_id") or (record.get("event") or {}).get("event_id")) == event_id:
                        record["artifacts"].update(data["artifacts"])
            elif kind == "judgment_submitted":
                record, conclusion = data["record"], data["conclusion"]
                state["events"][event_id] = record["event"]
                state["processed_events"][event_id] = record
                state["pending_approval"] = {**copy.deepcopy(record), "conclusion": conclusion}
                state["last_conclusion"] = {"event_id": event_id, **conclusion}
                state.pop("active_judgment", None)
            elif kind in {"approved", "rejected"}:
                state["reviews"].append(data)
                state["processed_events"][event_id]["status"] = "approved" if kind == "approved" else "rejected"
                state["pending_approval"] = None
            elif kind == "delivery_updated":
                review = next(item for item in reversed(state["reviews"]) if item["event_id"] == event_id)
                review.update(data)
            elif kind == "poll_updated":
                state["poll"][data["repository"]] = data
            else:
                raise ValueError(f"未知事实类型：{kind}")
        return state

    def load(self) -> dict[str, Any]:
        return self.project(self.facts())

    @contextmanager
    def _transaction(self):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection, self.project(self._facts(connection))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _validate_event(self, event):
        from types import SimpleNamespace
        from .events import validate_event
        if not isinstance(event, dict):
            raise ValueError("事件必须是 JSON 对象")
        repository = event.get("repository")
        settings = self.settings or SimpleNamespace(repositories={repository: None} if isinstance(repository, str) else {})
        return validate_event(event, settings)

    def check_event(self, state: dict, event: dict):
        self._validate_event(event)
        original = state["events"].get(event["event_id"])
        if original is not None and original != event:
            raise ValueError("相同事件 ID 的正文不同，拒绝覆盖或重放")

    def _migrate(self, connection):
        """先校验全部输入，再一次提交；旧文件原样保留为只读备份。"""
        root = self.path.parent.resolve()
        state_path = root / "state.json"
        paths = [state_path, root / "inbox", root / "inbox-archive"]
        for path in paths:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("旧状态或收件箱不得通过链接逃逸")
        legacy = empty_state()
        if state_path.exists():
            raw = json.loads(state_path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict) or raw.get("schema_version") != 1:
                raise ValueError("本地状态版本不受支持")
            for field in ("processed_events", "last_conclusion", "pending_approval", "reviews", "active_judgment"):
                if field in raw:
                    legacy[field] = raw[field]
        if not isinstance(legacy["processed_events"], dict) or not isinstance(legacy["reviews"], list):
            raise ValueError("旧状态记录格式无效")
        events = {}
        def remember(event, expected=None):
            self._validate_event(event)
            event_id = event["event_id"]
            if expected is not None and expected != event_id:
                raise ValueError("旧状态记录与事件 ID 不一致")
            if event_id in events and events[event_id] != event:
                raise ValueError("旧数据中相同事件 ID 的正文不同，拒绝导入")
            events[event_id] = event
        for event_id, record in legacy["processed_events"].items():
            from .config import safe_event_id
            safe_event_id(event_id)
            if not isinstance(record, dict) or record.get("event_id") != event_id:
                raise ValueError("旧已处理记录格式无效")
            if record.get("event") is not None:
                remember(record["event"], event_id)
        pending, active = legacy["pending_approval"], legacy.get("active_judgment")
        if pending is not None:
            if not isinstance(pending, dict) or pending.get("event_id") not in legacy["processed_events"]:
                raise ValueError("旧待拍板记录缺少对应的已处理记录")
            if pending.get("event") is not None:
                remember(pending["event"], pending["event_id"])
        if active is not None:
            if not isinstance(active, dict) or pending is not None:
                raise ValueError("旧活动轮次与待拍板记录冲突")
            remember(active["event"])
            if active["event"]["event_id"] in legacy["processed_events"]:
                raise ValueError("旧活动轮次已经处理")
        for review in legacy["reviews"]:
            if not isinstance(review, dict) or review.get("event_id") not in legacy["processed_events"]:
                raise ValueError("旧审批记录缺少对应的已处理记录")
            if review.get("event") is not None:
                remember(review["event"], review["event_id"])
        imported = []
        # 先归档后队列，兼容旧版同一已处理事件再次投递的情形。
        for directory, kind in ((root / "inbox-archive", "inbox_archived"), (root / "inbox", "event_enqueued")):
            for path in sorted(directory.glob("*.json")):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("旧收件文件不得通过链接逃逸")
                event = json.loads(path.read_text(encoding="utf-8-sig"))
                remember(event)
                imported.append((kind, event))
        # 只在首次打开导入；即使旧目录为空，也写入初始化事实作为分界。
        legacy.pop("events")
        legacy.pop("queue")
        legacy.pop("archived_events")
        self._append(connection, "legacy_state_imported", {"state": legacy, "events": events,
                     "source": "旧 JSON 只读备份；后续不再读取"})
        for kind, event in imported:
            self._append(connection, kind, {"event": event, "migration": True}, event["event_id"])

    def enqueue(self, event: dict) -> None:
        with self._transaction() as (connection, state):
            self.check_event(state, event)
            if event["event_id"] not in state["queue"]:
                self._append(connection, "event_enqueued", {"event": event}, event["event_id"])

    def archive(self, event: dict) -> None:
        with self._transaction() as (connection, state):
            self.check_event(state, event)
            event_id = event["event_id"]
            if event_id in state["queue"]:
                if event_id not in state["processed_events"]:
                    raise ValueError("未提交判断的事件不能从收件箱归档")
                self._append(connection, "inbox_archived", {"event": event}, event_id)

    def start_judgment(self, active: dict) -> None:
        with self._transaction() as (connection, state):
            event = active["event"]
            self.check_event(state, event)
            if state.get("active_judgment") or state["pending_approval"] or event["event_id"] in state["processed_events"]:
                raise ValueError("已有活动、待拍板或已处理轮次")
            self._append(connection, "judgment_started", active, event["event_id"])

    def record_poll(self, data: dict) -> None:
        """记录一次轮询结果：成功推进水位，失败只记尝试时间用于退避。"""
        with self._transaction() as (connection, state):
            self._append(connection, "poll_updated", data)

    def record_timeout(self, event_id: str, timeout_seconds: int) -> None:
        with self._transaction() as (connection, state):
            active = state.get("active_judgment")
            if active is None or active["event"]["event_id"] != event_id:
                raise ValueError("只能记录当前活动轮次的超时")
            self._append(connection, "judgment_timed_out",
                         {"timeout_seconds": timeout_seconds, "at": now_iso()}, event_id)

    def record_draft(self, event_id: str, artifacts: dict) -> None:
        with self._transaction() as (connection, state):
            record = state.get("active_judgment") or state.get("pending_approval")
            if not record or (record.get("event_id") or (record.get("event") or {}).get("event_id")) != event_id:
                raise ValueError("只能记录当前轮次草稿")
            self._append(connection, "draft_recorded", {"artifacts": artifacts}, event_id)

    def write_artifact(self, path: Path, content: str) -> Path:
        """草稿只允许首次写入，重放不能覆盖已审阅内容。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as file:
                file.write(content)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != content:
                raise ValueError(f"产物已存在且内容不同，拒绝覆盖：{path}")
        return path

    def finish_pending(self, event: dict, conclusion: dict, artifacts: dict) -> None:
        with self._transaction() as (connection, state):
            self.check_event(state, event)
            event_id = event["event_id"]
            if event_id in state["processed_events"] or state["pending_approval"] is not None:
                raise ValueError("事件已处理或上一轮仍在待拍板")
            active = state.get("active_judgment")
            if active is not None and active["event"] != event:
                raise ValueError("另一轮 Pi 判断尚未结束")
            record = {"event_id": event_id, "type": event["type"], "event": event,
                      "comment_sha256": hashlib.sha256(Path(artifacts["comment"]).read_bytes()).hexdigest(),
                      "status": "pending_approval", "processed_at": now_iso(), "artifacts": artifacts}
            self._append(connection, "judgment_submitted", {"record": record, "conclusion": conclusion}, event_id)

    def review(self, event_id: str, decision: str, note: str) -> dict:
        with self.locked(), self._transaction() as (connection, state):
            if decision not in {"approve", "reject"}:
                raise ValueError("decision 只能是 approve 或 reject")
            pending = state["pending_approval"]
            if pending is None or pending.get("event_id") != event_id:
                raise ValueError("该事件当前不在待拍板状态")
            review = {"event_id": event_id, "decision": decision, "note": note.strip(), "reviewed_at": now_iso(),
                      "delivery": "not-sent", "delivery_reason": "仅记录本地决定，未发出",
                      "event": pending.get("event"), "artifacts": dict(pending["artifacts"]),
                      "github_repository": pending.get("conclusion", {}).get("github_repository"),
                      "comment_sha256": pending.get("comment_sha256"), "patch_delivery": "local-only"}
            self._append(connection, "approved" if decision == "approve" else "rejected", review, event_id)
            return {**review, "message": "已记录本地决定，未发出；补丁永远只保留本地草稿。"}

    def update_review(self, event_id: str, values: dict, *, claim_delivery: bool = False) -> dict:
        with self.locked(), self._transaction() as (connection, state):
            review = next(item for item in reversed(state["reviews"]) if item["event_id"] == event_id)
            if claim_delivery and review.get("delivery_attempted_at"):
                return {**review, "message": "已尝试发送，不会自动重发；请核对讨论区。"}
            self._append(connection, "delivery_updated", values, event_id)
            return {**review, **values, **({"delivery_claimed": True} if claim_delivery else {}), "message": values.get("delivery_reason", review["delivery_reason"]) + "；补丁永远只保留本地草稿。"}
