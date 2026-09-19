import assert from "node:assert/strict";
import test from "node:test";
import { McpUiInitializeResultSchema, McpUiToolResultNotificationSchema } from "@modelcontextprotocol/ext-apps";
import { initializeResult, initialNotification } from "./mcp-host-contract.js";

test("smoke host initialization matches the installed MCP Apps SDK schema", () => {
  const result = initializeResult("2025-11-21");
  assert.deepEqual(McpUiInitializeResultSchema.parse(result), result);
});

test("smoke host initial result notification matches the SDK schema", () => {
  const message = initialNotification({ content: [], structuredContent: { text: "", charts: [] } });
  const parsed = McpUiToolResultNotificationSchema.parse(message);
  assert.equal(parsed.method, "ui/notifications/tool-result");
  assert.deepEqual(parsed.params, message.params);
});
