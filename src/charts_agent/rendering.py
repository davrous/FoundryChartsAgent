"""One embedded-data Vega-Lite specification for browser, SVG, and PNG views."""

from collections import defaultdict
from dataclasses import dataclass
import json
import math
from typing import Any

import vl_convert as vlc

from contracts import ChartRequest, ChartRow

SAMPLE_CARD_BYTE_BUDGET = 20 * 1024
SAMPLE_NATIVE_POINT_BUDGET = 80
SAMPLE_TABLE_ROW_BUDGET = 12
SAMPLE_RENDER_POINT_BUDGET = 256
NATIVE_TYPES = {
    "bar": "Chart.VerticalBar",
    "horizontal_bar": "Chart.HorizontalBar",
    "grouped_bar": "Chart.VerticalBar.Grouped",
    "stacked_bar": "Chart.HorizontalBar.Stacked",
    "line": "Chart.Line",
    "pie": "Chart.Pie",
    "donut": "Chart.Donut",
    "gauge": "Chart.Gauge",
}


@dataclass(frozen=True)
class RenderedChart:
    spec: dict[str, Any]
    png: bytes
    svg: str


def _has_series(request: ChartRequest, rows: list[ChartRow]) -> bool:
    return request.query.series is not None or len({row.series for row in rows}) > 1


def _static_reason(request: ChartRequest, rows: list[ChartRow]) -> str | None:
    kind = request.kind
    if kind not in NATIVE_TYPES:
        return f"{kind.title()} has no equivalent native Adaptive Card chart; using the shared PNG."
    if not rows:
        return "No matching data; using the shared empty-state image."
    if len(rows) > SAMPLE_NATIVE_POINT_BUDGET:
        return (
            f"More than {SAMPLE_NATIVE_POINT_BUDGET} points exceeds this sample's native-chart "
            "budget (not a platform limit); using the full shared PNG."
        )
    if kind in {"bar", "horizontal_bar", "pie", "donut", "gauge"} and _has_series(request, rows):
        return f"Native {kind} cannot preserve this request's separate series; using the shared PNG."
    if kind in {"horizontal_bar", "stacked_bar", "pie", "donut", "gauge"} and any(
        row.y < 0 for row in rows
    ):
        return f"Native {kind} cannot represent negative values; using the shared signed-value image."
    if kind in {"pie", "donut"} and sum(row.y for row in rows) <= 0:
        return "A pie or donut needs a positive total; using a zero-value bar image."
    if kind == "gauge" and sum(row.y for row in rows) > request.target:
        return "The total exceeds the gauge target; using an unclipped bar image with the target marked."
    if len({(row.x, row.series) for row in rows}) != len(rows):
        return "Repeated category/series pairs cannot be represented faithfully by the native chart."
    return None


def build_spec(request: ChartRequest, rows: list[ChartRow]) -> dict[str, Any]:
    """Build a pure, self-contained Vega-Lite v6 spec using v5-compatible marks."""
    if len(rows) > SAMPLE_RENDER_POINT_BUDGET:
        raise ValueError("Too many rows for the sample rendering budget; narrow the query.")
    metric = request.query.metric.title()
    dimension = request.query.group_by.title()
    has_series = _has_series(request, rows)
    values = [row.model_dump() for row in rows]
    spec: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v6.json",
        "description": f"{request.title}. Synthetic sales, {metric} by {dimension}.",
        "width": 960,
        "height": 540,
        "autosize": {"type": "fit", "contains": "padding"},
        "padding": 24,
        "background": "#ffffff",
        "title": {"text": request.title, "anchor": "start", "fontSize": 20},
        "data": {"name": "table", "values": values},
        "config": {
            "font": "Arial",
            "view": {"stroke": None},
            "axis": {"labelFontSize": 12, "titleFontSize": 13, "labelLimit": 150},
            "legend": {"orient": "bottom", "labelLimit": 200, "columns": 4},
            "range": {"category": ["#2563eb", "#0d9488", "#ea580c", "#7c3aed", "#db2777", "#65a30d"]},
        },
    }
    tooltip = [
        {"field": "x", "type": "nominal", "title": dimension},
        {"field": "series", "type": "nominal", "title": "Series"},
        {"field": "y", "type": "quantitative", "title": metric, "format": ",.2f"},
        {"field": "units", "type": "quantitative", "title": "Units", "format": ",.0f"},
    ]
    if not rows:
        spec["title"]["subtitle"] = "No data matches the selected filters."
        spec["mark"] = {"type": "text", "fontSize": 18, "color": "#475569"}
        spec["encoding"] = {
            "text": {"value": "No data matches the selected filters."},
            "tooltip": {"value": "No data matches the selected filters."},
        }
        return spec

    kind = request.kind
    invalid_arc = kind in {"pie", "donut"} and (
        any(row.y < 0 for row in rows) or sum(row.y for row in rows) <= 0
    )
    invalid_gauge = kind == "gauge" and (
        has_series or any(row.y < 0 for row in rows) or sum(row.y for row in rows) > request.target
    )
    if invalid_arc or invalid_gauge:
        kind = "horizontal_bar"
        spec["title"]["subtitle"] = (
            "Signed/zero values shown as bars; proportions would be misleading."
            if invalid_arc else "Values shown without clipping; dashed rule marks the target."
        )

    category: dict[str, Any] = {"field": "x", "type": "nominal", "title": dimension, "sort": None}
    measure = {"field": "y", "type": "quantitative", "title": metric}
    color = {
        "field": "series" if has_series else "x",
        "type": "nominal",
        "title": request.query.series.title() if request.query.series else dimension,
    }
    encoding: dict[str, Any] = {"tooltip": tooltip}
    if kind in {"bar", "grouped_bar"}:
        spec["mark"] = {"type": "bar", "cornerRadiusEnd": 3}
        encoding.update(x=category, y={**measure, "stack": None}, color=color)
        if has_series:
            encoding["xOffset"] = {"field": "series"}
    elif kind in {"horizontal_bar", "stacked_bar"}:
        spec["mark"] = {"type": "bar", "cornerRadiusEnd": 3}
        encoding.update(
            x={**measure, "stack": "zero" if kind == "stacked_bar" else None},
            y=category,
            color=color,
        )
        if kind == "horizontal_bar" and has_series:
            encoding["yOffset"] = {"field": "series"}
    elif kind in {"line", "area"}:
        spec["mark"] = (
            {"type": "line", "point": True, "strokeWidth": 3}
            if kind == "line" else {"type": "area", "opacity": 0.65, "line": True}
        )
        time_axis = (
            {"field": "x", "type": "temporal", "title": dimension, "axis": {"format": "%b %Y"}}
            if request.query.group_by == "month" else category
        )
        encoding.update(x=time_axis, y={**measure, "stack": None})
        if has_series:
            encoding["color"] = {**color, "field": "series"}
    elif kind in {"pie", "donut"}:
        spec["mark"] = {"type": "arc", "innerRadius": 115 if kind == "donut" else 0}
        spec["transform"] = [
            {"calculate": "datum.x + ' / ' + datum.series" if has_series else "datum.x", "as": "label"}
        ]
        encoding.update(
            theta={**measure, "stack": True},
            color={"field": "label", "type": "nominal", "title": dimension},
        )
    elif kind == "scatter":
        spec["mark"] = {"type": "point", "filled": True, "size": 140, "opacity": 0.85}
        encoding.update(
            x={"field": "units", "type": "quantitative", "title": "Units"},
            y=measure,
            color=color,
        )
    elif kind == "heatmap":
        spec["mark"] = {"type": "rect"}
        encoding.update(
            x=category,
            y={"field": "series", "type": "nominal", "title": request.query.series or "Metric"},
            color={**measure, "scale": {"scheme": "blueorange", "domainMid": 0}}
            if any(row.y < 0 for row in rows) else {**measure, "scale": {"scheme": "blues"}},
        )
    elif kind == "gauge":
        spec["transform"] = [
            {"aggregate": [{"op": "sum", "field": "y", "as": "total"}]},
            {"calculate": str(request.target), "as": "target"},
            {"calculate": f"datum.total / {request.target} * PI - PI / 2", "as": "angle"},
            {"calculate": f"format(datum.total, ',.2f') + ' / ' + format({request.target}, ',.2f')", "as": "label"},
        ]
        gauge_tooltip = [
            {"field": "total", "type": "quantitative", "title": f"Total {metric}", "format": ",.2f"},
            {"field": "target", "type": "quantitative", "title": "Target", "format": ",.2f"},
        ]
        spec["layer"] = [
            {"mark": {"type": "arc", "innerRadius": 130, "outerRadius": 190, "theta": -math.pi / 2,
                      "theta2": math.pi / 2, "color": "#e2e8f0"}},
            {"mark": {"type": "arc", "innerRadius": 130, "outerRadius": 190, "color": "#2563eb"},
             "encoding": {
                 "theta": {"field": "angle", "type": "quantitative", "scale": None},
                 "theta2": {"datum": -math.pi / 2},
                 "tooltip": gauge_tooltip,
             }},
            {"mark": {"type": "text", "fontSize": 22, "dy": 25},
             "encoding": {"text": {"field": "label"}, "tooltip": gauge_tooltip}},
        ]
        return spec
    spec["encoding"] = encoding
    if invalid_gauge:
        mark = spec.pop("mark")
        spec.pop("encoding")
        spec["layer"] = [
            {"mark": mark, "encoding": encoding},
            {"data": {"values": [{"target": request.target}]},
             "mark": {"type": "rule", "strokeDash": [6, 4], "color": "#dc2626"},
             "encoding": {
                 "x": {"field": "target", "type": "quantitative"},
                 "tooltip": {"field": "target", "type": "quantitative", "title": "Target"},
             }},
        ]
    return spec


def render_chart(request: ChartRequest, rows: list[ChartRow]) -> RenderedChart:
    """Render the exact same spec locally, without fetching remote data or images."""
    spec = build_spec(request, rows)
    svg = vlc.vegalite_to_svg(spec)
    png = vlc.vegalite_to_png(spec, scale=1)
    if len(png) >= 1_000_000:
        raise ValueError("PNG exceeds this sample's 1 MB rendering budget; narrow the query.")
    return RenderedChart(spec=spec, png=png, svg=svg)


def _native_element(request: ChartRequest, rows: list[ChartRow]) -> dict[str, Any]:
    element: dict[str, Any] = {"type": NATIVE_TYPES[request.kind], "title": request.title}
    if request.kind in {"bar", "horizontal_bar"}:
        element["data"] = [{"x": row.x, "y": row.y} for row in rows]
    elif request.kind in {"grouped_bar", "line"}:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            x = row.x + "-01" if request.kind == "line" and request.query.group_by == "month" else row.x
            groups[row.series].append({"x": x, "y": row.y})
        element["data"] = [{"legend": label, "values": values} for label, values in groups.items()]
    elif request.kind == "stacked_bar":
        stacks: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            stacks[row.x].append({"legend": row.series, "value": row.y})
        element["data"] = [{"title": label, "data": values} for label, values in stacks.items()]
    elif request.kind in {"pie", "donut"}:
        element["data"] = [{"legend": row.x, "value": row.y} for row in rows]
    elif request.kind == "gauge":
        total = sum(row.y for row in rows)
        element.update(
            min=0, max=request.target, value=total, valueFormat="Fraction",
            segments=[
                {"legend": "Achieved", "value": total},
                {"legend": "Remaining", "value": request.target - total},
            ],
        )
    return element


def _drill_actions(request: ChartRequest, rows: list[ChartRow]) -> list[dict[str, Any]]:
    dimension = request.query.group_by
    next_dimension = {"region": "country", "category": "product"}.get(dimension)
    if next_dimension is None:
        return []
    actions = []
    for value in list(dict.fromkeys(row.x for row in rows))[:3]:
        payload = request.model_dump()
        payload["title"] = f"{request.query.metric.title()} by {next_dimension} - {value}"[:120]
        payload["query"]["group_by"] = next_dimension
        payload["query"]["filters"][dimension] = [value]
        if payload["query"]["series"] == next_dimension:
            payload["query"]["series"] = None
        drilled = ChartRequest.model_validate(payload)
        actions.append({
            "type": "Action.Submit",
            "title": f"Explore {value}",
            "data": {"action": "drill", "request": drilled.model_dump()},
        })
    return actions


def adaptive_card(
    request: ChartRequest, rows: list[ChartRow], png_url: str
) -> tuple[dict[str, Any], bool, str | None]:
    """Return a native card or explicit image fallback, with accessible data and drill actions.

    The 20 KB card and 80-point native limits are application sample budgets, not
    assertions about Adaptive Cards or Teams platform limits.
    """
    total = sum(row.y for row in rows)
    summary = (
        f"Synthetic sales. {request.query.metric.title()}: {total:,.2f} across {len(rows)} groups. "
        f"{request.query.start_month} to {request.query.end_month}."
    ) if rows else "No data matches the selected filters."
    image = {"type": "Image", "url": png_url, "altText": f"{request.title}. {summary}", "size": "Stretch"}
    reason = _static_reason(request, rows)
    supported = reason is None
    element = _native_element(request, rows) if supported else image.copy()
    if supported:
        element["fallback"] = image.copy()
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": request.title, "weight": "Bolder", "size": "Large", "wrap": True},
        {"type": "TextBlock", "text": summary, "wrap": True},
        element,
    ]
    if reason:
        body.append({"type": "TextBlock", "text": reason, "wrap": True})
    if rows:
        body.append({
            "type": "FactSet",
            "facts": [
                {"title": f"{row.x} / {row.series}", "value": f"{row.y:,.2f} (units: {row.units:,.0f})"}
                for row in rows[:SAMPLE_TABLE_ROW_BUDGET]
            ],
        })
        if len(rows) > SAMPLE_TABLE_ROW_BUDGET:
            body.append({
                "type": "TextBlock", "wrap": True,
                "text": f"Data preview: first {SAMPLE_TABLE_ROW_BUDGET} of {len(rows)} rows; the chart includes every row.",
            })
    card = {
        "$schema": "https://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.5",
        "fallbackText": f"{request.title}. {summary}",
        "body": body,
        "actions": _drill_actions(request, rows),
    }
    if len(json.dumps(card, ensure_ascii=False).encode("utf-8")) > SAMPLE_CARD_BYTE_BUDGET:
        reason = "The native card exceeds this sample's 20 KB card budget (not a platform limit); using an image."
        supported = False
        card["body"] = body[:2] + [image, {"type": "TextBlock", "text": reason, "wrap": True}]
        card["actions"] = []
    if len(json.dumps(card, ensure_ascii=False).encode("utf-8")) > SAMPLE_CARD_BYTE_BUDGET:
        raise ValueError("Image URL or summary exceeds the sample's 20 KB card budget.")
    return card, supported, reason
