import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** 仅由主演示入口显式加载：工具无需逐条确认，业务审批留给 CLI。 */
export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    if (!event.toolName.startsWith("mcp__oss_watch__") ||
        event.toolName === "mcp__oss_watch__record_approval") {
      return { block: true, reason: "本轮只允许开源值守 MCP 读取和草稿工具；人工审批走 CLI。" };
    }
  });
  // settled 是自动重试和队列处理后的最终边界，避免在 agent_end 过早退出。
  pi.on("agent_settled", async (_event, ctx) => {
    ctx.shutdown();
  });
}
