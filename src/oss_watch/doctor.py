"""锁诊断：只报告持有者是否存活，绝不自动删锁。"""
from __future__ import annotations

import ctypes
import os

from .config import Settings
from .state import lock_owner

LOCK_SUFFIXES = (".lock", ".pi.lock", ".watch.lock")


def _pid_alive(pid: int) -> bool | None:
    """返回 True/False；无法判断时返回 None。"""
    if pid <= 0:
        return None
    if os.name == "nt":
        # OpenProcess 成功或报「拒绝访问」都说明进程存在；ERROR_INVALID_PARAMETER 才是已退出。
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False if kernel32.GetLastError() == 87 else None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def inspect_locks(settings: Settings) -> dict:
    locks = []
    for suffix in LOCK_SUFFIXES:
        path = settings.state_path.parent / ("state" + suffix)
        if not path.exists():
            continue
        owner = lock_owner(path)
        pid = owner.get("pid") if owner else None
        alive = _pid_alive(pid) if type(pid) is int else None
        advice = ("持有进程仍在运行，不要动锁" if alive is True else
                  "持有进程已退出，确认无相关 CLI/Pi 后可人工移走此锁" if alive is False else
                  "无法判断持有进程（旧版锁或读取失败），请人工确认后再处理")
        locks.append({"lock": str(path), "owner": owner, "pid_alive": alive, "advice": advice})
    return {"locks": locks, "message": "doctor 只诊断不删锁；移锁前先确认对应进程已退出。"}