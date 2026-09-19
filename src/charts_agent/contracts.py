from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Dimension = Literal["month", "region", "country", "category", "product", "channel"]
Metric = Literal["revenue", "profit", "units", "orders"]
ChartKind = Literal[
    "bar", "horizontal_bar", "grouped_bar", "stacked_bar", "line",
    "area", "pie", "donut", "scatter", "heatmap", "gauge",
]


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Metric = "revenue"
    group_by: Dimension = "region"
    series: Dimension | None = None
    filters: dict[Dimension, list[str]] = Field(default_factory=dict)
    start_month: str = Field(default="2025-01", pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    end_month: str = Field(default="2025-12", pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


class ChartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ChartKind = "bar"
    title: str = Field(default="Sales overview", min_length=1, max_length=120)
    query: QuerySpec = Field(default_factory=QuerySpec)
    target: float = Field(default=1_000_000, gt=0, allow_inf_nan=False)


class ChartRow(BaseModel):
    x: str
    y: float = Field(allow_inf_nan=False)
    series: str = "Revenue"
    units: float = Field(default=0, allow_inf_nan=False)


class ChartBundle(BaseModel):
    id: str
    request: ChartRequest
    title: str
    summary: str
    rows: list[ChartRow]
    vega_lite: dict
    images: dict[str, str]
    adaptive_card: dict
    adaptive_supported: bool
    fallback_reason: str | None
