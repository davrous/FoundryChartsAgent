import { App } from "@modelcontextprotocol/ext-apps";
import { createChartView, element } from "./chart-view.js";
import { toolPayload } from "./chart-data.js";
import "./style.css";

const root = document.querySelector("#app");
const app = new App(
  { name: "Foundry interactive charts", version: "1.0.0" },
  {},
  { autoResize: true, allowUnsafeEval: false },
);
let charts = [];

function showError(error) {
  let banner = root.querySelector(".mcp-error");
  if (!banner) {
    banner = element("p", "error mcp-error");
    banner.setAttribute("role", "alert");
    root.prepend(banner);
  }
  banner.textContent = error instanceof Error ? error.message : "The chart host reported an error.";
}

function display(result) {
  const payload = toolPayload(result);
  charts.forEach((chart) => chart.destroy());
  charts = [];
  root.replaceChildren();
  if (payload.text) root.append(element("p", "mcp-summary", payload.text));
  for (const bundle of payload.charts) {
    const chart = createChartView(bundle, {
      onDrill: async (request) => {
        // App-initiated calls return directly; hosts need not emit another ontoolresult.
        const response = await app.callServerTool({ name: "drill_chart", arguments: { request } });
        const drilled = toolPayload(response);
        if (drilled.charts.length !== 1) throw new Error("The hosted agent did not return the requested chart.");
        return drilled.charts[0];
      },
    });
    charts.push(chart);
    root.append(chart.element);
  }
  if (!payload.charts.length) root.append(element("p", "empty-state", "No chart was returned. Ask the analyst to visualize the data."));
}

app.ontoolresult = (result) => {
  try { display(result); } catch (error) { showError(error); }
};
app.onerror = showError;
app.ontoolcancelled = () => showError(new Error("The chart request was cancelled."));
app.onteardown = async () => {
  charts.forEach((chart) => chart.destroy());
  charts = [];
  return {};
};
await app.connect().catch(showError);
