import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const host = "127.0.0.1";
const port = Number(process.env.MCP_SMOKE_PORT || 8192);
const origin = `http://${host}:${port}`;
const widgetOrigin = `http://localhost:${port}`;
const gateway = new URL(process.env.MCP_SMOKE_GATEWAY || "http://127.0.0.1:8190");
if (gateway.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(gateway.hostname) || gateway.username || gateway.password || gateway.pathname !== "/" || gateway.search || gateway.hash) {
  throw new Error("The test gateway must be a loopback HTTP origin.");
}
const paths = {
  "/": [new URL("./mcp-smoke-host.html", import.meta.url), "text/html"],
  "/host.js": [new URL("./mcp-smoke-host.js", import.meta.url), "text/javascript"],
  "/host-contract.js": [new URL("./mcp-host-contract.js", import.meta.url), "text/javascript"],
  "/widget": [new URL("../dist/mcp-app.html", import.meta.url), "text/html"],
};
const widgetCsp = `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors ${origin}`;
const hostCsp = `default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; frame-src ${widgetOrigin}; base-uri 'none'; object-src 'none'`;

function json(response, status, value) {
  response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  response.end(JSON.stringify(value));
}

const server = createServer(async (request, response) => {
  const expectedHost = request.url === "/widget" ? `localhost:${port}` : `${host}:${port}`;
  if (request.headers.host !== expectedHost) return json(response, 403, { error: "Unexpected test host." });
  if (request.url === "/health" && request.method === "GET") return json(response, 200, { status: "ok", testOnly: true });
  if (request.url === "/mcp" && request.method === "POST") {
    if (request.headers.origin !== origin) return json(response, 403, { error: "Same-origin test requests only." });
    const chunks = [];
    let length = 0;
    for await (const chunk of request) {
      length += chunk.length;
      if (length > 32768) return json(response, 413, { error: "Test request too large." });
      chunks.push(chunk);
    }
    try {
      const upstream = await fetch(new URL("/mcp", gateway), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json, text/event-stream",
          Origin: gateway.origin,
          "MCP-Protocol-Version": "2025-11-25",
        },
        body: Buffer.concat(chunks),
        signal: AbortSignal.timeout(120000),
      });
      response.writeHead(upstream.status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      response.end(await upstream.text());
    } catch (error) {
      json(response, 502, { error: `Test gateway request failed: ${error.message}` });
    }
    return;
  }
  const entry = paths[request.url];
  if (!entry || request.method !== "GET") return json(response, 404, { error: "No such test resource." });
  try {
    const content = await readFile(fileURLToPath(entry[0]));
    response.writeHead(200, {
      "Content-Type": entry[1],
      "Content-Security-Policy": request.url === "/widget" ? widgetCsp : hostCsp,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    });
    response.end(content);
  } catch (error) {
    if (error.code === "ENOENT") return json(response, 503, { error: "Run npm --prefix web run build first." });
    console.error(error);
    json(response, 500, { error: "Cannot read the test resource." });
  }
});
server.listen(port, host, () => console.log(`MCP strict-CSP smoke host: ${origin}\nGateway: ${gateway.origin}\nTest-only; no external assets or browser credentials.`));
