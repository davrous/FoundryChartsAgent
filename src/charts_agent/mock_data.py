"""Deterministic synthetic sales and a deliberately restricted, read-only query API."""

from contextlib import asynccontextmanager
import json
import random
import sqlite3
from typing import Any, AsyncIterator

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from contracts import ChartRow, QuerySpec

MAX_QUERY_ROWS = 256
MAX_BODY_BYTES = 16_384
REGIONS = {
    "Americas": ("Canada", "United States"),
    "Europe": ("France", "Germany"),
    "Asia Pacific": ("Australia", "Japan"),
}
PRODUCTS = {
    "Electronics": ("Laptop", "Headphones"),
    "Home": ("Coffee Maker", "Desk Lamp"),
    "Outdoor": ("Backpack", "Tent"),
}
DIMENSIONS = {
    "month": tuple(f"2025-{month:02d}" for month in range(1, 13)),
    "region": tuple(REGIONS),
    "country": tuple(country for countries in REGIONS.values() for country in countries),
    "category": tuple(PRODUCTS),
    "product": tuple(product for products in PRODUCTS.values() for product in products),
    "channel": ("Online", "Retail"),
}
METRICS = ("revenue", "profit", "units", "orders")


def schema() -> dict[str, Any]:
    """Return JSON-compatible metadata, including all legal filter values."""
    return {
        "dataset": "synthetic_sales_2025",
        "description": "Seeded fictional sales; not real customer or financial data.",
        "dimensions": {key: list(values) for key, values in DIMENSIONS.items()},
        "metrics": {
            "revenue": {"aggregation": "sum", "unit": "USD"},
            "profit": {"aggregation": "sum", "unit": "USD"},
            "units": {"aggregation": "sum", "unit": "items"},
            "orders": {"aggregation": "sum", "unit": "orders"},
        },
        "date_range": {"start_month": "2025-01", "end_month": "2025-12"},
        "drill_down": {"region": "country", "category": "product"},
        "sample_limits": {"max_query_rows": MAX_QUERY_ROWS, "max_body_bytes": MAX_BODY_BYTES},
        "query": QuerySpec.model_json_schema(),
    }


def _database() -> sqlite3.Connection:
    database = sqlite3.connect(":memory:", check_same_thread=False)
    database.row_factory = sqlite3.Row
    database.execute(
        "CREATE TABLE sales (month TEXT, region TEXT, country TEXT, category TEXT, "
        "product TEXT, channel TEXT, revenue REAL, profit REAL, units INTEGER, orders INTEGER)"
    )
    rng = random.Random(2025)
    prices = (890, 120, 95, 55, 85, 240)
    records = []
    for month_index, month in enumerate(DIMENSIONS["month"]):
        for region, countries in REGIONS.items():
            for country in countries:
                for category, products in PRODUCTS.items():
                    for product in products:
                        price = prices[DIMENSIONS["product"].index(product)]
                        for channel in DIMENSIONS["channel"]:
                            units = int(rng.randint(35, 180) * (1 + month_index * 0.035))
                            revenue = round(units * price * rng.uniform(0.88, 1.12), 2)
                            profit = round(revenue * rng.uniform(0.12, 0.34), 2)
                            orders = max(1, int(units / rng.uniform(1.2, 3.4)))
                            records.append(
                                (month, region, country, category, product, channel,
                                 revenue, profit, units, orders)
                            )
    database.executemany("INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records)
    database.commit()
    database.execute("PRAGMA query_only = ON")
    return database


def _validate_query(query: QuerySpec) -> None:
    if query.start_month > query.end_month:
        raise ValueError("start_month must not be after end_month.")
    if query.start_month < "2025-01" or query.end_month > "2025-12":
        raise ValueError("The sample dataset supports date ranges within 2025 only.")
    if query.series == query.group_by:
        raise ValueError("series and group_by must be different dimensions.")
    for dimension, values in query.filters.items():
        if not values:
            raise ValueError(f"Filter {dimension!r} must contain at least one value.")
        if len(values) > len(DIMENSIONS[dimension]):
            raise ValueError(f"Too many values for filter {dimension!r}.")
        if any(value not in DIMENSIONS[dimension] for value in values):
            raise ValueError(f"Unknown value for filter {dimension!r}; consult GET /schema.")


def _query(database: sqlite3.Connection, query: QuerySpec) -> list[ChartRow]:
    _validate_query(query)
    # Identifiers come exclusively from the validated Literal allowlists.
    columns = [query.group_by] + ([query.series] if query.series else [])
    selections = [f'"{query.group_by}" AS x']
    if query.series:
        selections.append(f'"{query.series}" AS series')
    selections.extend([f'SUM("{query.metric}") AS y', 'SUM("units") AS units'])
    where = ['"month" BETWEEN ? AND ?']
    parameters: list[Any] = [query.start_month, query.end_month]
    for dimension, values in sorted(query.filters.items()):
        where.append(f'"{dimension}" IN ({", ".join("?" for _ in values)})')
        parameters.extend(values)
    grouping = ", ".join(f'"{column}"' for column in columns)
    sql = (
        f'SELECT {", ".join(selections)} FROM sales WHERE {" AND ".join(where)} '
        f"GROUP BY {grouping} ORDER BY {grouping} LIMIT ?"
    )
    parameters.append(MAX_QUERY_ROWS + 1)
    records = database.execute(sql, parameters).fetchall()
    if len(records) > MAX_QUERY_ROWS:
        raise ValueError("Result exceeds the sample row budget; narrow the filters.")
    return [
        ChartRow(
            x=record["x"],
            series=record["series"] if query.series else query.metric.title(),
            y=round(record["y"], 2),
            units=record["units"],
        )
        for record in records
    ]


def create_mock_app() -> Starlette:
    """Create an isolated seeded API; POST /query accepts a QuerySpec, never SQL."""
    database = _database()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            database.close()

    async def get_schema(request: Request) -> JSONResponse:
        return JSONResponse(schema())

    async def query_data(request: Request) -> JSONResponse:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "Request exceeds the sample body budget."}, status_code=422)
        try:
            query = QuerySpec.model_validate_json(body)
            rows = _query(database, query)
        except (ValidationError, ValueError, json.JSONDecodeError) as error:
            detail = str(error)
            return JSONResponse({"detail": detail}, status_code=422)
        total = sum(row.y for row in rows)
        summary = (
            f"Synthetic 2025 sales: {query.metric} totals {total:,.2f} across "
            f"{len(rows)} groups, {query.start_month} through {query.end_month}."
        )
        if not rows:
            summary = "No synthetic sales match the requested filters and date range."
        return JSONResponse({"rows": [row.model_dump() for row in rows], "summary": summary})

    app = Starlette(
        routes=[Route("/schema", get_schema), Route("/query", query_data, methods=["POST"])],
        lifespan=lifespan,
    )
    app.state.database = database
    return app
