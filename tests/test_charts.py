import asyncio
import json
import sqlite3
import struct

import httpx
import pytest
from starlette.testclient import TestClient

from contracts import ChartRequest, ChartRow, QuerySpec
from mock_data import create_mock_app, schema
from rendering import NATIVE_TYPES, adaptive_card, build_spec, render_chart


@pytest.fixture
def client():
    with TestClient(create_mock_app()) as client:
        yield client


def test_schema_and_deterministic_read_only_queries(client):
    metadata = client.get("/schema").json()
    assert metadata == schema()
    assert set(metadata["dimensions"]) == {"month", "region", "country", "category", "product", "channel"}
    result = client.post("/query", json={}).json()
    assert result["rows"] and result["summary"]
    assert result == client.post("/query", json={}).json()
    assert all(row["units"] > 0 for row in result["rows"])
    with TestClient(create_mock_app()) as other:
        assert client.app.state.database is not other.app.state.database
        assert result == other.post("/query", json={}).json()
    assert result == client.post("/query", json={}).json()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        client.app.state.database.execute("DELETE FROM sales")


def test_asgi_transport_works_without_running_lifespan():
    app = create_mock_app()

    async def query():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://mock-db"
        ) as client:
            metadata = await client.get("/schema")
            result = await client.post("/query", json={})
        assert metadata.status_code == 200
        assert result.status_code == 200, result.text
        assert result.json()["rows"] and result.json()["summary"]

    try:
        asyncio.run(query())
    finally:
        app.state.database.close()


@pytest.mark.parametrize("payload", [
    {"sql": "DROP TABLE sales"},
    {"group_by": "country; DROP TABLE sales"},
    {"metric": "COUNT(*)"},
    {"filters": {"bad_column": ["value"]}},
    {"filters": {"country": ["Canada' OR 1=1 --"]}},
    {"filters": {"region": []}},
    {"series": "region", "group_by": "region"},
    {"start_month": "2025-06", "end_month": "2025-05"},
    {"start_month": "2024-12"},
    {"end_month": "2026-01"},
    {"start_month": "2025-13"},
])
def test_bad_queries_return_explicit_422(client, payload):
    response = client.post("/query", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]
    assert client.post("/query", json={}).status_code == 200


def test_date_filters_series_and_aggregate_totals(client):
    query = {"metric": "units", "group_by": "month", "series": "channel",
             "filters": {"region": ["Europe"]}, "start_month": "2025-03", "end_month": "2025-04"}
    rows = client.post("/query", json=query).json()["rows"]
    assert len(rows) == 4
    assert {row["x"] for row in rows} == {"2025-03", "2025-04"}
    assert {row["series"] for row in rows} == {"Online", "Retail"}
    assert all(row["y"] == row["units"] for row in rows)
    query.update(group_by="country", series=None)
    total_rows = client.post("/query", json=query).json()["rows"]
    assert {row["x"] for row in total_rows} == {"France", "Germany"}
    assert sum(row["y"] for row in rows) == sum(row["y"] for row in total_rows)


def test_empty_malformed_and_large_request(client):
    response = client.post("/query", json={"filters": {"region": ["Europe"], "country": ["Canada"]}})
    assert response.json()["rows"] == []
    assert client.post("/query", content="{oops").status_code == 422
    assert client.post("/query", content=" " * 17000).status_code == 422


def test_query_budget_rejects_instead_of_truncating(client, monkeypatch):
    monkeypatch.setattr("mock_data.MAX_QUERY_ROWS", 1)
    response = client.post("/query", json={})
    assert response.status_code == 422
    assert "row budget" in response.json()["detail"]


@pytest.mark.parametrize("kind", [*NATIVE_TYPES, "area", "scatter", "heatmap"])
def test_every_chart_renders_same_embedded_spec_within_image_budgets(client, kind):
    request = ChartRequest(kind=kind, target=100_000_000)
    if kind in {"grouped_bar", "stacked_bar", "line", "area", "heatmap"}:
        request.query = QuerySpec(group_by="month", series="region")
    rows = [ChartRow.model_validate(row) for row in client.post("/query", json=request.query.model_dump()).json()["rows"]]
    original = [row.model_dump() for row in rows]
    rendered = render_chart(request, rows)
    assert rendered.spec == build_spec(request, rows)
    assert rendered.spec["data"]["name"] == "table"
    assert rendered.spec["data"]["values"] == original
    assert [row.model_dump() for row in rows] == original
    assert "<svg" in rendered.svg
    assert rendered.png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", rendered.png[16:24])
    assert width == 960 and height <= 1024
    assert len(rendered.png) < 1_000_000
    assert "tooltip" in json.dumps(rendered.spec)
    if kind == "scatter":
        assert rendered.spec["encoding"]["x"]["field"] == "units"


@pytest.mark.parametrize("kind,shape", [
    ("bar", "xy"), ("horizontal_bar", "xy"), ("grouped_bar", "groups"),
    ("line", "groups"), ("stacked_bar", "stacks"), ("pie", "slices"),
    ("donut", "slices"), ("gauge", "gauge"),
])
def test_exact_native_shapes_and_host_fallback(kind, shape):
    rows = [ChartRow(x="Europe", y=30, units=4), ChartRow(x="Americas", y=20, units=3)]
    request = ChartRequest(kind=kind, target=100)
    card, supported, reason = adaptive_card(request, rows, "https://example.test/chart.png")
    assert supported and reason is None
    chart = card["body"][2]
    assert chart["type"] == NATIVE_TYPES[kind]
    assert chart["fallback"]["type"] == "Image"
    assert chart["fallback"]["altText"]
    assert len(json.dumps(card).encode()) < 20 * 1024
    if shape == "xy":
        assert chart["data"] == [{"x": "Europe", "y": 30}, {"x": "Americas", "y": 20}]
    elif shape == "groups":
        assert chart["data"] == [{"legend": "Revenue", "values": [{"x": "Europe", "y": 30}, {"x": "Americas", "y": 20}]}]
    elif shape == "stacks":
        assert chart["data"][0] == {"title": "Europe", "data": [{"legend": "Revenue", "value": 30}]}
    elif shape == "slices":
        assert chart["data"][0] == {"legend": "Europe", "value": 30}
    else:
        assert chart["min"] == 0 and chart["max"] == 100 and chart["value"] == 50
        assert chart["segments"] == [{"legend": "Achieved", "value": 50}, {"legend": "Remaining", "value": 50}]
    assert any(element["type"] == "FactSet" for element in card["body"])


def test_line_months_use_native_iso_dates_and_all_series():
    request = ChartRequest(kind="line", query=QuerySpec(group_by="month", series="region"))
    rows = [ChartRow(x="2025-01", y=12, series="Europe"), ChartRow(x="2025-01", y=20, series="Americas")]
    card, supported, _ = adaptive_card(request, rows, "https://example.test/chart.png")
    assert supported
    assert len(card["body"][2]["data"]) == 2
    assert card["body"][2]["data"][0]["values"][0]["x"] == "2025-01-01"


@pytest.mark.parametrize("kind", ["bar", "grouped_bar", "line"])
def test_signed_native_charts_preserve_negative_values(kind):
    rows = [ChartRow(x="Europe", y=-5), ChartRow(x="Americas", y=20)]
    card, supported, reason = adaptive_card(
        ChartRequest(kind=kind), rows, "https://example.test/chart.png"
    )
    assert supported and reason is None
    chart = card["body"][2]
    values = chart["data"] if kind == "bar" else chart["data"][0]["values"]
    assert values[0]["y"] == -5


def test_stacked_chart_keeps_categories_and_every_series():
    request = ChartRequest(kind="stacked_bar", query=QuerySpec(series="channel"))
    rows = [
        ChartRow(x="Europe", series="Online", y=10),
        ChartRow(x="Europe", series="Retail", y=30),
        ChartRow(x="Americas", series="Online", y=20),
    ]
    card, supported, _ = adaptive_card(request, rows, "https://example.test/chart.png")
    assert supported
    assert card["body"][2]["data"] == [
        {"title": "Europe", "data": [{"legend": "Online", "value": 10}, {"legend": "Retail", "value": 30}]},
        {"title": "Americas", "data": [{"legend": "Online", "value": 20}]},
    ]
    assert build_spec(request, rows)["encoding"]["x"]["stack"] == "zero"


@pytest.mark.parametrize("value", [0, 100])
def test_gauge_boundary_values_render_without_clipping(value):
    request = ChartRequest(kind="gauge", target=100)
    rows = [ChartRow(x="Europe", y=value)]
    rendered = render_chart(request, rows)
    assert rendered.png and "<svg" in rendered.svg
    card, supported, _ = adaptive_card(request, rows, "https://example.test/chart.png")
    assert supported and card["body"][2]["value"] == value
    assert sum(segment["value"] for segment in card["body"][2]["segments"]) == 100


@pytest.mark.parametrize("kind", ["pie", "donut", "horizontal_bar", "stacked_bar", "gauge"])
def test_negative_values_use_explained_image_fallback(kind):
    rows = [ChartRow(x="Europe", y=-5), ChartRow(x="Americas", y=20)]
    request = ChartRequest(kind=kind)
    card, supported, reason = adaptive_card(request, rows, "https://example.test/chart.png")
    assert not supported and "negative" in reason
    assert card["body"][2]["type"] == "Image"
    rendered = render_chart(request, rows)
    assert "<svg" in rendered.svg and rendered.spec["data"]["values"][0]["y"] == -5


@pytest.mark.parametrize("kind", ["bar", "horizontal_bar", "pie", "donut", "gauge"])
def test_series_are_never_silently_dropped(kind):
    request = ChartRequest(kind=kind, query=QuerySpec(series="channel"))
    rows = [ChartRow(x="Europe", y=10, series="Online"), ChartRow(x="Europe", y=20, series="Retail")]
    card, supported, reason = adaptive_card(request, rows, "https://example.test/chart.png")
    assert not supported and "series" in reason
    assert card["body"][2]["type"] == "Image"
    assert build_spec(request, rows)["data"]["values"] == [row.model_dump() for row in rows]


def test_drill_down_submits_a_valid_filtered_request(client):
    request = ChartRequest(query=QuerySpec(series="country", filters={"channel": ["Online"]}))
    rows = [ChartRow(x="Europe", y=10)]
    card, _, _ = adaptive_card(request, rows, "https://example.test/chart.png")
    action = card["actions"][0]
    assert action["type"] == "Action.Submit" and action["data"]["action"] == "drill"
    drilled = ChartRequest.model_validate(action["data"]["request"])
    assert drilled.title == "Revenue by country - Europe"
    assert drilled.query.group_by == "country" and drilled.query.series is None
    assert drilled.query.filters == {"channel": ["Online"], "region": ["Europe"]}
    assert client.post("/query", json=drilled.query.model_dump()).status_code == 200
    assert request.query.group_by == "region" and "region" not in request.query.filters


@pytest.mark.parametrize("selected_value", ["Electronics", "Long category " + "x" * 150])
def test_drill_title_tracks_metric_and_dimension_within_contract_limit(selected_value):
    request = ChartRequest(
        title="Old category overview",
        query=QuerySpec(metric="profit", group_by="category"),
    )
    card, _, _ = adaptive_card(
        request, [ChartRow(x=selected_value, y=10)], "https://example.test/chart.png"
    )
    drilled = ChartRequest.model_validate(card["actions"][0]["data"]["request"])
    assert drilled.title == f"Profit by product - {selected_value}"[:120]
    assert len(drilled.title) <= 120
    assert drilled.query.group_by == "product"
    assert drilled.query.filters == {"category": [selected_value]}
    assert request.title == "Old category overview"


def test_static_kinds_empty_zero_gauge_and_sample_budgets():
    rows = [ChartRow(x="Europe", y=0)]
    for kind in ("area", "scatter", "heatmap", "pie", "donut"):
        _, supported, reason = adaptive_card(ChartRequest(kind=kind), rows, "https://example.test/chart.png")
        assert not supported and reason
    empty = render_chart(ChartRequest(), [])
    assert empty.spec["data"] == {"name": "table", "values": []}
    assert "No data" in empty.svg
    card, supported, reason = adaptive_card(ChartRequest(), [], "https://example.test/chart.png")
    assert not supported and "No matching" in reason
    _, supported, reason = adaptive_card(
        ChartRequest(kind="gauge", target=1), [ChartRow(x="Europe", y=2)], "https://example.test/chart.png"
    )
    assert not supported and "exceeds" in reason
    many = [ChartRow(x=str(index), y=index) for index in range(81)]
    _, supported, reason = adaptive_card(ChartRequest(), many, "https://example.test/chart.png")
    assert not supported and "sample" in reason and "platform" in reason
    huge_labels = [ChartRow(x=str(index) + "é" * 5000, y=index) for index in range(3)]
    card, supported, reason = adaptive_card(
        ChartRequest(query=QuerySpec(group_by="product")), huge_labels, "https://example.test/chart.png"
    )
    assert not supported and "20 KB" in reason
    assert len(json.dumps(card, ensure_ascii=False).encode()) < 20 * 1024
