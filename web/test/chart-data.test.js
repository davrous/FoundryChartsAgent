import assert from "node:assert/strict";
import test from "node:test";
import { compile } from "vega-lite";
import { changeset, parse, View } from "vega";
import { expressionInterpreter } from "vega-interpreter";
import { chartSpec, DATASET, drillRequest, filteredRows, toolPayload } from "../src/chart-data.js";

const rows = [
  { x: "Europe", y: 300, series: "Online", units: 8 },
  { x: "Americas", y: 200, series: "Retail", units: 4 },
];
const bundle = {
  title: "Revenue", rows,
  request: { kind: "bar", title: "Revenue", target: 1000, query: { metric: "revenue", group_by: "region", series: "channel", filters: {}, start_month: "2025-01", end_month: "2025-12" } },
  vega_lite: { data: { name: "table", values: rows }, mark: "bar", encoding: { x: { field: "x", type: "nominal" }, y: { field: "y", type: "quantitative" }, tooltip: [{ field: "x" }, { field: "y" }] } },
};

test("the agent spec is preserved and data changes drive the SVG without eval", async () => {
  const spec = chartSpec(bundle);
  const originalRows = structuredClone(bundle.rows);
  assert.deepEqual(spec, bundle.vega_lite);
  assert.equal(spec.data.name, "table");
  assert.equal(DATASET, "table");
  const OriginalFunction = globalThis.Function;
  globalThis.Function = new Proxy(OriginalFunction, {
    apply() { throw new Error("unsafe-eval is forbidden"); },
    construct() { throw new Error("unsafe-eval is forbidden"); },
  });
  let view;
  try {
    view = new View(parse(compile(spec).spec, null, { ast: true }), { renderer: "none", expr: expressionInterpreter });
    await view.runAsync();
    const svg = await view.toSVG();
    assert.match(svg, /Europe/);
    assert.match(svg, /Americas/);
    await view.change(DATASET, changeset().remove(() => true).insert(structuredClone([rows[0]]))).runAsync();
    const filtered = await view.toSVG();
    assert.match(filtered, /Europe/);
    assert.doesNotMatch(filtered, /Americas/);
    assert.deepEqual(bundle.rows, originalRows);
    assert.deepEqual(bundle.vega_lite.data.values, originalRows);
  } finally {
    globalThis.Function = OriginalFunction;
    view?.finalize();
  }
});

test("client category and series filters intersect without modifying data", () => {
  assert.deepEqual(filteredRows(rows, new Set(["Europe"]), new Set(["Online"])), [rows[0]]);
  assert.deepEqual(filteredRows(rows, new Set(["Europe"]), new Set(["Retail"])), []);
  assert.equal(rows.length, 2);
});

test("drill-down passes a structured filtered ChartRequest", () => {
  const request = drillRequest(bundle, "Europe");
  assert.equal(request.query.group_by, "country");
  assert.deepEqual(request.query.filters, { region: ["Europe"] });
  assert.equal(request.query.series, "channel");
  assert.deepEqual(bundle.request.query.filters, {});
  assert.throws(() => drillRequest(bundle, "Invented"), /Choose a category/);
});

test("tool results prefer structured content, support text, and propagate errors", () => {
  const payload = { text: "Hello", charts: [bundle] };
  assert.deepEqual(toolPayload({ structuredContent: payload }), payload);
  assert.deepEqual(toolPayload({ content: [{ type: "text", text: JSON.stringify(payload) }] }), payload);
  assert.throws(() => toolPayload({ isError: true, content: [{ type: "text", text: "Unavailable" }] }), /Unavailable/);
  assert.throws(() => toolPayload({ content: [{ type: "text", text: "no JSON" }] }), /invalid JSON/);
});
