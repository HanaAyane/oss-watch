"""待办通知：Windows toast 优先，失败静默降级到响铃与日志；不含评论正文。"""
from __future__ import annotations

import base64
import json
import subprocess

from .logging_setup import get_logger

# 通知文案只含标识与一句话状态；正文留在草稿文件里。
# Windows PowerShell 5.1 会把 -Command 后的剩余参数拼进脚本正文执行（$args 恒为空），
# 文案含空格/中文会被当命令解析，所以改走 -EncodedCommand，参数以 Base64 JSON 嵌进脚本。
_TOAST = """
$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($payload.title)) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode($payload.message)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('oss-watch').Show($toast)
"""


def notify(title: str, message: str) -> str:
    """返回实际使用的渠道；toast 失败不影响主流程。"""
    logger = get_logger()
    try:
        payload = base64.b64encode(json.dumps({"title": title, "message": message}, ensure_ascii=False).encode("utf-8")).decode("ascii")
        script = _TOAST.replace("__PAYLOAD__", payload)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, timeout=15, check=False)
        if completed.returncode == 0:
            logger.info("通知已发送(toast) %s", title)
            return "toast"
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        print(f"\a[oss-watch] {title} — {message}", flush=True)
    except UnicodeEncodeError:
        # 旧代码页控制台打不出中文时退回 ASCII 提示；完整文案已在日志里。
        print("\a[oss-watch] pending review; see logs/oss-watch.log", flush=True)
    logger.info("通知降级(bell) %s", title)
    return "bell"
