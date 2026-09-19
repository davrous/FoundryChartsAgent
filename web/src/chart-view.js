import { changeset, parse, View } from "vega";
import { compile } from "vega-lite";
import { expressionInterpreter } from "vega-interpreter";
import { chartSpec, DATASET, drillRequest, filteredRows } from "./chart-data.js";

export function element(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

export function createChartView(bundle, { onDrill } = {}) {
  const root = element("article", "chart-card");
  let view;
  let disposed = false;
  let renderVersion = 0;
  let busy = false;
  const tooltip = element("div", "chart-tooltip");
  tooltip.hidden = true;
  tooltip.setAttribute("role", "tooltip");
  document.body.append(tooltip);
  let observer;

  function reportError(error) {
    status.textContent = error instanceof Error ? error.message : "The chart could not be updated.";
    status.classList.add("error");
  }

  const status = element("p", "chart-status");
  status.setAttribute("role", "status");

  async function render(nextBundle) {
    const version = ++renderVersion;
    bundle = nextBundle;
    observer?.disconnect();
    view?.finalize();
    view = null;
    tooltip.hidden = true;
    root.replaceChildren();
    status.textContent = "";
    status.classList.remove("error");
    const heading = element("div", "chart-heading");
    const titles = element("div");
    titles.append(element("span", "eyebrow", "LIVE EXPLORATION"), element("h3", "", bundle.title));
    heading.append(titles, element("span", "chip", bundle.request.kind.replaceAll("_", " ")));
    const summary = element("p", "chart-summary", bundle.summary);
    const toolbar = element("div", "chart-toolbar");
    const categories = new Set(bundle.rows.map((row) => row.x));
    const series = new Set(bundle.rows.map((row) => row.series));
    const checks = [];
    const categoryChoices = [...categories];
    const update = async () => {
      if (!view || disposed) return;
      const rows = filteredRows(bundle.rows, categories, series);
      await view.change(DATASET, changeset().remove(() => true).insert(structuredClone(rows))).runAsync();
      status.classList.remove("error");
      status.textContent = rows.length
        ? `${rows.length} of ${bundle.rows.length} data points · filters run locally`
        : "No visible data. Select a category and series, or reset filters.";
    };
    function filter(label, values, selected) {
      const details = element("details", "filter");
      details.append(element("summary", "", label));
      const options = element("div", "filter-options");
      for (const value of values) {
        const item = element("label", "filter-option");
        const input = element("input");
        input.type = "checkbox";
        input.checked = true;
        input.addEventListener("change", () => {
          input.checked ? selected.add(value) : selected.delete(value);
          void update().catch(reportError);
        });
        checks.push(input);
        item.append(input, element("span", "", value));
        options.append(item);
      }
      details.append(options);
      return details;
    }
    toolbar.append(filter("Categories", categoryChoices, categories));
    if (series.size > 1) toolbar.append(filter("Series", [...series], series));
    const reset = element("button", "button subtle", "Reset filters");
    reset.type = "button";
    reset.addEventListener("click", () => {
      bundle.rows.forEach((row) => { categories.add(row.x); series.add(row.series); });
      checks.forEach((input) => { input.checked = true; });
      void update().catch(reportError);
    });
    const download = element("button", "button subtle", "↓ Download SVG");
    download.type = "button";
    download.addEventListener("click", () => {
      void (async () => {
        const svg = await view.toSVG();
        const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
        const anchor = element("a");
        anchor.href = url;
        anchor.download = `${bundle.title.replace(/[^a-z0-9_-]+/gi, "-") || "chart"}.svg`;
        anchor.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      })().catch(reportError);
    });
    toolbar.append(reset, download);
    const canvas = element("div", "chart-canvas");
    canvas.setAttribute("aria-label", `${bundle.title}. Hover for values; use filters or drill-down controls below.`);
    canvas.setAttribute("role", "img");
    root.append(heading, summary, toolbar, canvas, status);
    const nextDimensions = { region: "country", country: "product", category: "product", channel: "region", month: "region" };
    let select;
    if (onDrill && nextDimensions[bundle.request.query.group_by]) {
      const drillBar = element("div", "drill-bar");
      const label = element("label", "drill-label", `Explore ${nextDimensions[bundle.request.query.group_by]} within`);
      select = element("select", "select");
      select.setAttribute("aria-label", "Category to drill into");
      for (const x of categoryChoices) {
        const option = element("option", "", x);
        option.value = x;
        select.append(option);
      }
      const drill = element("button", "button primary", "Drill down ↗");
      drill.type = "button";
      drill.disabled = !categoryChoices.length;
      drill.addEventListener("click", () => {
        void (async () => {
          if (busy) return;
          busy = true;
          drill.disabled = true;
          status.classList.remove("error");
          status.textContent = "Requesting a new chart from the hosted agent…";
          try {
            const result = await onDrill(drillRequest(bundle, select.value));
            if (!disposed) await render(result);
          } finally {
            busy = false;
            drill.disabled = false;
          }
        })().catch(reportError);
      });
      label.append(select);
      drillBar.append(label, drill);
      root.append(drillBar);
    }
    root.append(element("p", "chart-footnote", "SYNTHETIC DATA · 2025   /   Same Vega-Lite specification as the hosted agent"));
    const compiled = compile(chartSpec(bundle)).spec;
    const runtime = parse(compiled, null, { ast: true });
    const loader = {
      load: async () => { throw new Error("External chart resources are disabled."); },
      sanitize: async () => { throw new Error("External chart URLs are disabled."); },
    };
    const currentView = new View(runtime, {
      renderer: "svg",
      container: canvas,
      hover: true,
      expr: expressionInterpreter,
      loader,
      tooltip: (_handler, event, _item, value) => {
        if (!value || (typeof value === "object" && !Object.keys(value).length)) {
          tooltip.hidden = true;
          return;
        }
        tooltip.replaceChildren();
        for (const [key, text] of Object.entries(typeof value === "object" ? value : { Value: value })) {
          const row = element("div", "tooltip-row");
          row.append(element("span", "", key), element("strong", "", String(text)));
          tooltip.append(row);
        }
        tooltip.hidden = false;
        tooltip.style.left = `${Math.max(8, Math.min(event.clientX + 14, window.innerWidth - tooltip.offsetWidth - 10))}px`;
        tooltip.style.top = `${Math.max(8, Math.min(event.clientY + 14, window.innerHeight - tooltip.offsetHeight - 10))}px`;
      },
    });
    view = currentView;
    if (select) currentView.addEventListener("click", (_event, item) => {
      const datum = item?.datum;
      const row = bundle.rows.find((row) => row.x === datum?.x)
        || bundle.rows.find((row) => row.y === datum?.y && row.series === datum?.series);
      if (row) {
        select.value = row.x;
        status.textContent = `Selected ${row.x}. Use “Drill down” to query its details.`;
      }
    });
    await currentView.runAsync();
    if (disposed || version !== renderVersion) return;
    status.textContent = `${bundle.rows.length} data points · hover over a mark for details`;
    observer = new ResizeObserver(([entry]) => {
      if (view !== currentView || disposed) return;
      const width = Math.max(280, Math.floor(entry.contentRect.width));
      if (currentView.width() !== width) {
        void currentView.width(width).runAsync().catch(reportError);
      }
    });
    observer.observe(canvas);
  }
  void render(bundle).catch(reportError);
  return {
    element: root,
    destroy() {
      disposed = true;
      renderVersion++;
      observer?.disconnect();
      view?.finalize();
      tooltip.remove();
      root.remove();
    },
  };
}
