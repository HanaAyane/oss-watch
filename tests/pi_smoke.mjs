/** 不调用模型：使用本机 Pi SDK 加载项目配置、列工具并真正调用 MCP。 */
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

if (!process.argv[2]) throw new Error("请传入本机 @earendil-works/pi-coding-agent 包目录");
process.env.PI_OFFLINE = "1";
const sdk = await import(pathToFileURL(resolve(process.argv[2], "dist/index.js")).href);
const agentDir = await mkdtemp(join(tmpdir(), "oss-watch-pi-smoke-"));
const cwd = process.cwd();
const settingsManager = sdk.SettingsManager.create(cwd, agentDir);
const loader = new sdk.DefaultResourceLoader({
  cwd, agentDir, settingsManager, noExtensions: true, noSkills: true,
  noPromptTemplates: true, noThemes: true, noContextFiles: true,
  additionalExtensionPaths: [resolve(".pi/one-round.ts")],
  extensionFactories: [sdk.createMcpExtension()],
});
let session;
let shutdownRequested = false;
try {
  // 仅本次探针信任项目，不修改用户 trust.json、MCP 配置或登录资料。
  await loader.reload({ resolveProjectTrust: async () => true });
  assert.deepEqual(loader.getExtensions().errors, []);
  ({ session } = await sdk.createAgentSession({
    cwd, agentDir, resourceLoader: loader, settingsManager,
    sessionManager: sdk.SessionManager.inMemory(),
    tools: ["mcp__oss_watch__*"], excludeTools: ["mcp__oss_watch__record_approval"],
  }));
  await session.bindExtensions({ shutdownHandler: () => { shutdownRequested = true; } });
  const deadline = Date.now() + 15000;
  while (!session.getActiveToolNames().includes("mcp__oss_watch__submit_judgment") && Date.now() < deadline) {
    await new Promise((done) => setTimeout(done, 100));
  }
  const names = session.getActiveToolNames();
  assert.equal(names.length, 11); // 服务共 12 个，人工审批工具被入口排除。
  assert(names.every((name) => name.startsWith("mcp__oss_watch__")));
  assert(!names.includes("mcp__oss_watch__record_approval"));
  const call = async (name, args) => {
    const tool = session.agent.state.tools.find((item) => item.name === "mcp__oss_watch__" + name);
    assert(tool, "Pi 未注册工具 " + name);
    const result = await tool.execute("smoke-" + name, args, new AbortController().signal);
    assert(!result.isError, JSON.stringify(result));
    return JSON.parse(result.content.find((item) => item.type === "text").text);
  };
  assert((await call("read_repository_file", { repository: "dsh-reasoning-effort", path: "package.json", start_line: 1, end_line: 5 })).content.startsWith("1: {"));
  assert((await call("search_repository", { repository: "dsh-reasoning-effort", query: "aria-label", glob: "*.tsx" })).matches.length);
  assert("pending_approval" in await call("get_pending_approval", {}));
  await session.extensionRunner.emit({ type: "agent_settled", messages: [] });
  assert(shutdownRequested, "一轮结束没有请求退出");
  console.log(JSON.stringify({ pi: "项目 MCP 配置、扩展加载、工具限制与三项真实调用通过；未调用模型", tools: names }, null, 2));
} finally {
  if (session) await session.extensionRunner.emit({ type: "session_shutdown", reason: "quit" });
  session?.dispose();
  await rm(agentDir, { recursive: true, force: true });
}
