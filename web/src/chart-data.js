export const DATASET = "table";

export function chartSpec(bundle) {
  if (!bundle?.vega_lite || !Array.isArray(bundle.rows)) {
    throw new Error("The agent returned an invalid chart.");
  }
  const spec = structuredClone(bundle.vega_lite);
  // Name, do not reinterpret, the agent's data source. Transforms and marks stay identical.
  spec.data = { ...spec.data, name: DATASET };
  delete spec.data.url;
  return spec;
}

export function filteredRows(rows, categories, series) {
  return rows.filter((row) => categories.has(row.x) && series.has(row.series));
}

export function drillRequest(bundle, category) {
  const next = { region: "country", country: "product", category: "product", channel: "region", month: "region" };
  const query = bundle.request.query;
  const dimension = next[query.group_by];
  if (!dimension || !bundle.rows.some((row) => row.x === category)) {
    throw new Error("Choose a category with a supported drill-down.");
  }
  const request = structuredClone(bundle.request);
  request.title = `${category} · ${dimension}`.slice(0, 120);
  request.query.filters[query.group_by] = [category];
  request.query.group_by = dimension;
  if (request.query.series === dimension || request.query.series === query.group_by) {
    request.query.series = null;
  }
  if (["gauge", "heatmap", "line", "area"].includes(request.kind)) request.kind = "bar";
  return request;
}

export function toolPayload(result) {
  if (result?.isError) {
    const text = result.content?.find((item) => item.type === "text")?.text;
    throw new Error(text || "The chart tool failed.");
  }
  let payload = result?.structuredContent;
  if (!payload) {
    const text = result?.content?.find((item) => item.type === "text")?.text;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        throw new Error("The chart tool returned invalid JSON.");
      }
    }
  }
  if (!payload || !Array.isArray(payload.charts)) throw new Error("No chart payload was returned.");
  return payload;
}
