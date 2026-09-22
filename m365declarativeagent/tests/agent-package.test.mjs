import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFileSync(resolve(root, path), "utf8");
const json = (path) => JSON.parse(read(path));
const plugin = json("appPackage/ai-plugin.json");
const agent = json("appPackage/declarativeAgent.json");
const manifest = json("appPackage/manifest.json");
const runtime = plugin.runtimes[0];
const tools = runtime.spec.mcp_tool_description.tools;

test("pinned tool names and MCP App metadata are wired consistently", () => {
    const names = ["drill_chart", "get_chart"];
    assert.deepEqual(plugin.functions.map((fn) => fn.name).sort(), names);
    assert.deepEqual([...runtime.run_for_functions].sort(), names);
    assert.deepEqual(tools.map((tool) => tool.name).sort(), names);
    assert.equal(runtime.type, "RemoteMCPServer");
    assert.equal(runtime.spec.mcp_tool_description.file, undefined);
    assert.ok(Array.isArray(tools), "Inline descriptors must survive Toolkit ZIP packaging");
    for (const tool of tools) {
        assert.equal(tool._meta.ui.resourceUri, "ui://charts/app.html");
        assert.equal(tool.inputSchema.type, "object");
        assert.equal(tool.outputSchema.type, "object");
        if (tool._meta.ui.visibility) {
            assert.ok(tool._meta.ui.visibility.includes("model"));
            assert.ok(tool._meta.ui.visibility.includes("app"));
        }
    }
    assert.deepEqual(tools.find((tool) => tool.name === "get_chart").inputSchema.required, ["message"]);
    assert.deepEqual(tools.find((tool) => tool.name === "drill_chart").inputSchema.required, ["request"]);
});

test("editor and packaged agent target the same HTTPS MCP endpoint", () => {
    const servers = Object.values(json(".vscode/mcp.json").servers);
    assert.ok(servers.some((server) => server.url === runtime.spec.url));
    const url = new URL(runtime.spec.url);
    assert.equal(url.protocol, "https:");
    assert.equal(url.pathname, "/mcp");
    assert.equal(url.username, "");
    assert.equal(url.password, "");
    assert.equal(url.search, "");
});

test("instructions are included and fit the platform limit", () => {
    assert.equal(agent.instructions, "$[file('instruction.txt')]");
    const instructions = read("appPackage/instruction.txt");
    assert.ok(instructions.length <= 8000, `${instructions.length} exceeds 8000 characters`);
    for (const term of ["get_chart", "drill_chart", "previous_response_id", "charts[].request",
        "ui://charts/app.html", "synthetic", "EVERY sales/data question"]) {
        assert.ok(instructions.includes(term), `Missing instruction contract: ${term}`);
    }
    for (const fn of plugin.functions) {
        assert.ok(fn.description.length <= 1024);
    }
    assert.ok(plugin.description_for_model.length <= 1024);
});

test("manifest references and DA branding are consistent", () => {
    assert.equal(manifest.copilotAgents.declarativeAgents[0].file, "declarativeAgent.json");
    assert.equal(agent.actions[0].file, "ai-plugin.json");
    assert.equal(manifest.name.short, agent.name);
    assert.ok(agent.name.includes(" DA"));
    assert.equal(manifest.accentColor, "#2735A6");
    assert.equal(agent.conversation_starters.length, 6);
    assert.ok(agent.conversation_starters.every((starter) => starter.text.includes("interactive")));
});

test("package icons are real PNGs with required native dimensions", () => {
    for (const [kind, size] of [["color", 192], ["outline", 32]]) {
        const bytes = readFileSync(resolve(root, "appPackage", manifest.icons[kind]));
        assert.equal(bytes.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
        assert.equal(bytes.toString("ascii", 12, 16), "IHDR");
        assert.equal(bytes.readUInt32BE(16), size);
        assert.equal(bytes.readUInt32BE(20), size);
        assert.ok(read(`appPackage/icon-sources/${kind}.svg`).includes("Foundry Charts DA"));
    }
});

test("quality evaluation dataset covers chart and non-chart sales questions", () => {
    const dataset = json("evals/prompts.json");
    assert.equal(dataset.schemaVersion, "1.2.0");
    assert.equal(dataset.items.length, 6);
    assert.ok(dataset.items.every((item) => item.prompt && item.expected_response));
    assert.ok(dataset.items.some((item) => item.prompt.includes("highest")));
    assert.ok(dataset.items.some((item) => item.prompt.includes("guess")));
});
