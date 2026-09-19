// Contract: ext-apps 2.0.0 generated/schema.json, McpUiInitializeResult and
// McpUiToolResultNotification. This mocks only the embedding host, not chart tools.
import { initializeResult, initialNotification } from "/host-contract.js";

const status = document.querySelector("#status");
const log = document.querySelector("#protocol-log");
let nextId = 1;
let notifications = 0;
let calls = 0;

function fail(error) {
  status.dataset.state = "error";
  status.textContent = error.message;
}

async function callTool(name, args) {
  const response = await fetch("/mcp", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: nextId++, method: "tools/call", params: { name, arguments: args } }),
  });
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error?.message || payload.error || `MCP HTTP ${response.status}`);
  if (payload.result?.isError) throw new Error(payload.result.content?.find((item) => item.type === "text")?.text || "Chart tool failed.");
  return payload.result;
}

async function start() {
  const initial = await callTool("drill_chart", {
    request: {
      kind: "grouped_bar",
      title: "MCP smoke · revenue by region and channel",
      query: { metric: "revenue", group_by: "region", series: "channel" },
    },
  });
  const frame = document.createElement("iframe");
  const widgetOrigin = `http://localhost:${window.location.port}`;
  frame.title = "Strict CSP interactive chart";
  frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-downloads");
  window.addEventListener("message", async (event) => {
    if (event.source !== frame.contentWindow || event.origin !== widgetOrigin) return;
    const message = event.data;
    if (message?.jsonrpc !== "2.0") return;
    log.textContent += `${message.method || "response"}\n`;
    const reply = (result) => event.source.postMessage({ jsonrpc: "2.0", id: message.id, result }, widgetOrigin);
    if (message.method === "ui/initialize") {
      reply(initializeResult(message.params.protocolVersion));
    } else if (message.method === "ui/notifications/initialized") {
      if (notifications) return;
      notifications++;
      document.querySelector("#notification-count").textContent = String(notifications);
      event.source.postMessage(initialNotification(initial), widgetOrigin);
      status.dataset.state = "ready";
      status.textContent = "Widget connected. Its chart is rendered under strict CSP. Ready for interaction checks.";
    } else if (message.method === "tools/call") {
      calls++;
      document.querySelector("#call-count").textContent = String(calls);
      try {
        if (message.params?.name !== "drill_chart") throw new Error("Only drill_chart is enabled in this smoke host.");
        reply(await callTool(message.params.name, message.params.arguments));
        status.textContent = "Direct tools/call response delivered. No extra tool-result notification was sent.";
      } catch (error) {
        event.source.postMessage({ jsonrpc: "2.0", id: message.id, error: { code: -32603, message: error.message } }, widgetOrigin);
        fail(error);
      }
    }
  });
  frame.src = `${widgetOrigin}/widget`;
  document.querySelector("#widget-container").append(frame);
}

void start().catch(fail);
