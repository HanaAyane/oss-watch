"""集中创建工具依赖，确保 CLI 与 MCP 使用同一个审批门和状态文件。"""
from __future__ import annotations

from dataclasses import dataclass

from .approval import ApprovalGate
from .config import Settings, load_settings
from .github import GitHubReader
from .history import HistoryReader
from .repository import RepositoryReader
from .state import StateStore
from .workflow import VersionWatchWorkflow


@dataclass
class Services:
    settings: Settings
    repositories: RepositoryReader
    github: GitHubReader
    history: HistoryReader
    approval: ApprovalGate
    state: StateStore
    workflow: VersionWatchWorkflow


def create_services() -> Services:
    settings = load_settings()
    state = StateStore(settings.state_path)
    return Services(
        settings=settings,
        repositories=RepositoryReader(settings),
        github=GitHubReader(settings),
        history=HistoryReader(settings),
        approval=ApprovalGate(settings, state),
        state=state,
        workflow=VersionWatchWorkflow(settings),
    )
