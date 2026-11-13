"""固定规则对照与回归编排器；智能判断由 Pi 完成。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings, repository_path, safe_event_id
from .history import HistoryReader
from .repository import RepositoryReader
from .state import StateStore, now_iso


class VersionWatchWorkflow:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.reader = RepositoryReader(settings)
        self.store = StateStore(settings.state_path)

    def load_event(self, event_path: str) -> dict[str, Any]:
        path = Path(event_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"事件文件不存在：{path}")
        event = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise ValueError("事件必须是 JSON 对象")
        safe_event_id(event.get("event_id", ""))
        if event.get("type") != "release.published":
            raise ValueError("第一期仅处理 release.published 事件")
        if not isinstance(event.get("release"), dict) or not isinstance(event.get("repository"), str):
            raise ValueError("事件缺少 release 或 repository")
        return event

    def _text(self, repo: str, path: str) -> str:
        return repository_path(self.settings.repositories[repo], path).read_text(encoding="utf-8")

    @staticmethod
    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(f"仓库源码与本次适配检查的前提不符：{message}")

    def run(self, event: dict[str, Any]) -> dict[str, Any]:
        with self.store.locked():
            return self._run(event)

    def _run(self, event: dict[str, Any]) -> dict[str, Any]:
        state = self.store.load()
        event_id = event["event_id"]
        if event_id in state["processed_events"]:
            return {"status": "already_processed", "event_id": event_id, "record": state["processed_events"][event_id], "pending_approval": state.get("pending_approval"), "message": "事件已处理，未覆盖原草稿。请查看现有待拍板产物。"}
        if state.get("pending_approval") is not None:
            pending_id = state["pending_approval"]["event_id"]
            raise ValueError(f"事件 {pending_id} 仍在待拍板，请先 approve 或 reject，再处理新事件。")
        if state.get("active_judgment") is not None:
            raise ValueError("Pi 判断尚未结束，不能启动固定规则对照或新事件")
        repo_name = event["repository"]
        if repo_name not in self.settings.repositories:
            raise ValueError(f"未配置的被值守仓库：{repo_name}")
        contract = event.get("compatibility")
        if not isinstance(contract, dict) or contract.get("contract") != "dsh.native-model-menu/v1":
            return self._unsupported(event, "发版说明没有提供第一期可识别的 native-model-menu/v1 适配契约，需人工确认。")
        release = event["release"]
        body = release.get("body", "")
        for field in (contract.get("menu_marker"), contract.get("pane_marker"), contract.get("aria_label_key")):
            if not isinstance(field, str) or field not in body:
                raise ValueError("结构化契约与发版说明正文不一致，拒绝自动判断")

        selector_path = "src/client/native-selector.tsx"
        selector = self._text(repo_name, selector_path)
        package = json.loads(self._text(repo_name, "package.json"))
        changelog = self._text(repo_name, "CHANGELOG.md")
        self._require("element.props['aria-label'] !== props.t('menu.aria')" in selector, "未找到旧的 aria-label 匹配")
        self._require("const [modelRow, effortRow, ...options] = root.props.children" in selector, "未找到旧的固定子节点顺序假设")
        self._require("'data-re-effort-entry': ''" in selector, "未找到推理等级入口注入逻辑")
        self._require("select(restoreRememberedEffort(directory.getSnapshot(), selection))" in selector, "未找到模型选择包装逻辑")

        evidence: list[dict[str, Any]] = []
        old_aria = "element.props['aria-label'] !== props.t('menu.aria')"
        for item in self.reader.search(repo_name, old_aria, "*.tsx")["matches"]:
            evidence.append({"kind": "code", **item, "finding": f"仍按旧 locale key 定位菜单；新标记为 {contract['menu_marker']}。"})
        evidence.append({"kind": "code", "path": selector_path, "line": 65, "finding": "把模型列表第二个子节点解释为 effortRow；新说明中推理入口已迁往独立窗格。"})
        evidence.append({"kind": "release", "path": release.get("url", "fixture"), "finding": f"声明 {contract['pane_marker']} 窗格标识，并允许窗格重排。"})
        evidence.append({"kind": "manifest", "path": "package.json", "line": 33, "finding": f"engines.dsh 为 {package.get('engines', {}).get('dsh')}，不包含模拟版本 {release.get('tag')}。"})
        if "0.2.2-rc.2" in changelog:
            evidence.append({"kind": "document", "path": "CHANGELOG.md", "line": 23, "finding": "现有兼容基线为 DSH 0.2.2-rc.2；新版结构没有对应适配记录。"})
        history = HistoryReader(self.settings).search(repo_name, "移动端")
        for item in history["results"][:1]:
            evidence.append({"kind": "history", "path": item.get("url", item.get("path")), "finding": "历史移动端溢出反馈提示：结构修复后仍需做窄视口回归。"})

        # 只有实际源码同时存在旧标记和顺序假设，才给出需要修改的结论。
        reasons = [
            "菜单识别仍依赖旧的无障碍标签 key，没有读取发版说明提供的稳定标记。",
            "菜单内容仍按固定位置拆分为 modelRow / effortRow / options，不能表达独立的 models、effort、session 窗格及重排。",
            "当前兼容范围止于 0.2.x；模拟发版为 0.3.0-rc.1，虽保留 ModelDirectory.select，但菜单结构适配不兼容。",
        ]
        patch = self._patch(selector, contract)
        comment = self._comment(event, reasons)
        conclusion = {
            "decision": "需要修改",
            "release": release.get("tag"),
            "repository": repo_name,
            "confidence": "高（针对静态结构适配）；新版运行时兼容性未验证",
            "reasons": reasons,
            "evidence": evidence,
            "validation_required": ["在新版实际菜单中确认 models / effort / session 窗格与 Agent Teams 开关均保留", "运行原仓库 pnpm run check", "在窄视口下回归菜单定位", "人工审查草稿后再决定是否在目标仓库建立分支"],
            "generated_at": now_iso(),
            "simulation": event.get("fixture") is True,
        }
        artifacts = self._write(event, conclusion, patch, comment)
        self.store.finish_pending(event, conclusion, artifacts)
        return {"status": "pending_approval", "event_id": event_id, "conclusion": conclusion, "artifacts": artifacts, "delivery": "not-sent", "message": "演示已停在待拍板状态；未修改被值守仓库，未对外发评论或开 PR。"}

    def _unsupported(self, event: dict[str, Any], reason: str) -> dict[str, Any]:
        conclusion = {"decision": "无法自动判断", "repository": event["repository"], "release": event["release"].get("tag"), "reasons": [reason], "evidence": [], "generated_at": now_iso(), "simulation": event.get("fixture") is True}
        comment = f"# 发版适配待人工确认\n\n{reason}\n\n第一期没有可靠的结构化适配契约，因此不自动写补丁。\n"
        artifacts = self._write(event, conclusion, None, comment)
        self.store.finish_pending(event, conclusion, artifacts)
        return {"status": "pending_approval", "event_id": event["event_id"], "conclusion": conclusion, "artifacts": artifacts, "delivery": "not-sent"}

    def _write(self, event: dict[str, Any], conclusion: dict[str, Any], patch: str | None, comment: str) -> dict[str, str]:
        event_id = event["event_id"]
        root = self.settings.runs_root / event_id
        artifacts = {"conclusion": str(root / "conclusion.json"), "comment": str(root / "comment.md")}
        self.store.write_artifact(root / "conclusion.json", json.dumps(conclusion, ensure_ascii=False, indent=2) + "\n")
        self.store.write_artifact(root / "comment.md", comment)
        if patch is not None:
            artifacts["patch"] = str(root / "patch.md")
            self.store.write_artifact(root / "patch.md", patch)
        return artifacts

    @staticmethod
    def _comment(event: dict[str, Any], reasons: list[str]) -> str:
        return "\n".join([
            "# 评论草稿（未发送）", "",
            f"针对模拟发版 `{event['release']['tag']}`，静态检查结论：**需要修改原生模型菜单适配层**。", "",
            *[f"- {reason}" for reason in reasons], "",
            "补丁草稿建议按 `data-dsh-model-menu` / `data-dsh-pane` 定位，避免按子节点下标猜测；同时保留模型选择、搜索和会话窗格。", "",
            "> 这是本地模拟发版生成的草稿，尚未在目标版本实测，也未发送为 GitHub 评论。", "",
        ])

    @staticmethod
    def _patch(selector: str, contract: dict[str, Any]) -> str:
        import difflib

        def replace_once(source: str, old: str, new: str) -> str:
            if source.count(old) != 1:
                raise ValueError("补丁上下文与实际源码不一致，拒绝编造补丁")
            return source.replace(old, new, 1)

        first_old = """      if (element.props['aria-label'] !== props.t('menu.aria') || !Array.isArray(element.props.children)) return element
      const [root, ...otherPanes] = element.props.children"""
        first_new = f"""      const isNativeMenu =
        element.props['aria-label'] === props.t('menu.aria') ||
        element.props['{contract['menu_marker']}'] !== undefined
      if (!isNativeMenu || !Array.isArray(element.props.children)) return element
      const [root] = element.props.children"""
        second_old = """      if (!isValidElement<{ children: ReactNode[] }>(root) || !Array.isArray(root.props.children)) return element
      // The native menu keeps the effort position even when it renders nothing.
      const [modelRow, effortRow, ...options] = root.props.children
      if (!isValidElement<ElementProps>(modelRow) || modelRow.props.role !== 'menuitem') return element"""
        second_new = f"""      const panes = element.props.children.filter(
        (pane): pane is ReactElement<ElementProps> => isValidElement<ElementProps>(pane))
      const structuredMenu = element.props['{contract['menu_marker']}'] !== undefined
      const modelPane = (structuredMenu
        ? panes.find(pane => pane.props['{contract['pane_marker']}'] === '{contract['model_pane']}')
        : root) ?? root
      const effortPane = structuredMenu
        ? panes.find(pane => pane.props['{contract['pane_marker']}'] === '{contract['effort_pane']}')
        : undefined
      // 推理入口仍放回 models 窗格；session 等其他窗格绝不替换，避免覆盖 Agent Teams。
      const remainingPanes = element.props.children.filter(pane => pane !== modelPane && pane !== effortPane)
      if (!isValidElement<{{ children: ReactNode[] }}>(modelPane) || !Array.isArray(modelPane.props.children)) return element
      const modelRow = structuredMenu
        ? modelPane.props.children.find(child =>
            isValidElement<ElementProps>(child) && child.props['{contract['model_row_marker']}'] !== undefined)
        : modelPane.props.children[0]
      const effortRow = structuredMenu ? undefined : modelPane.props.children[1]
      const options = modelPane.props.children.filter(child => child !== modelRow && child !== effortRow)
      if (!isValidElement<ElementProps>(modelRow) || (!structuredMenu && modelRow.props.role !== 'menuitem')) return element"""
        third_old = """      }, cloneElement(root, {}, modelRow, row, ...options), ...otherPanes)"""
        third_new = """      }, cloneElement(modelPane, {}, modelRow, row, ...options), ...remainingPanes)"""
        updated = replace_once(selector, first_old, first_new)
        updated = replace_once(updated, second_old, second_new)
        updated = replace_once(updated, third_old, third_new)
        diff = "".join(difflib.unified_diff(
            selector.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile="a/src/client/native-selector.tsx", tofile="b/src/client/native-selector.tsx", n=3,
        ))
        return "\n".join([
            "# 补丁草稿（未应用）", "",
            "**目标文件：`src/client/native-selector.tsx`**", "",
            "以下 unified diff 按实际读到的 1.0.0 源码生成，三处替换均已校验唯一命中。它仍是待审查草稿：没有应用到目标仓库，也没有在新版运行时验证。", "",
            "```diff", diff.rstrip("\n"), "```", "",
            "## 人工接续事项", "",
            "- 在真实新版菜单中确认 `models` / `effort` / `session` 窗格结构，并确认 Agent Teams 等选项未被替换。",
            "- 补齐或调整仓库现有组件回归测试，覆盖窗格重排、缺少窗格、键盘导航和窄视口定位。",
            "- 保留模型搜索、portal 身份、选中档位恢复和 RPC 调用约定。",
            "- 运行目标仓库自身的 `pnpm run check` 并在新版实际页面验证；第一期不会执行这些写操作。", "",
        ])
