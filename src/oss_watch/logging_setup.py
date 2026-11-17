"""运行日志：只记流程节点与标识，不记事件正文或环境变量值。"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured: Path | None = None


def setup_logging(state_root: Path) -> logging.Logger:
    """同一状态目录只装配一次；stdout 的 JSON 协议不受影响。"""
    global _configured
    logger = logging.getLogger("oss_watch")
    root = state_root.resolve()
    if _configured == root:
        return logger
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "oss-watch.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _configured = root
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("oss_watch")