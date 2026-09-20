import asyncio
import os
from uuid import uuid4

import httpx

from artifact_storage import ArtifactStore
from contracts import ChartBundle, ChartRequest, ChartRow
from mock_data import create_mock_app
from progress import report_progress
from rendering import adaptive_card, render_chart


class ChartService:
    def __init__(self, artifacts: ArtifactStore) -> None:
        self.artifacts = artifacts
        self.mock_app = create_mock_app()
        url = os.getenv("MOCK_API_URL")
        self.http = httpx.AsyncClient(
            base_url=url or "http://mock-db",
            transport=None if url else httpx.ASGITransport(app=self.mock_app),
            timeout=15,
        )
        self.render_slots = asyncio.Semaphore(2)

    async def schema(self) -> dict:
        await report_progress("Reading the available sample sales dimensions and filters...")
        response = await self.http.get("/schema")
        response.raise_for_status()
        return response.json()

    async def create(self, request: ChartRequest) -> ChartBundle:
        await report_progress("Querying the sample sales data...")
        response = await self.http.post("/query", json=request.query.model_dump())
        if response.status_code == 422:
            raise ValueError(f"Invalid data query: {response.text}")
        response.raise_for_status()
        payload = response.json()
        rows = [ChartRow.model_validate(row) for row in payload["rows"]]
        if not rows:
            raise ValueError("No mock sales match these filters. Choose values from get_data_schema.")
        async with self.render_slots:
            await report_progress("Rendering your chart...")
            rendered = await asyncio.to_thread(render_chart, request, rows)
        chart_id = uuid4().hex
        await report_progress("Saving the chart images and preparing the Adaptive Card...")
        images = await self.artifacts.save(chart_id, rendered.png, rendered.svg)
        card, supported, reason = adaptive_card(request, rows, images["png"])
        return ChartBundle(
            id=chart_id, request=request, title=request.title, summary=payload["summary"],
            rows=rows, vega_lite=rendered.spec, images=images,
            adaptive_card=card, adaptive_supported=supported, fallback_reason=reason,
        )

    async def close(self) -> None:
        await self.http.aclose()
        await self.artifacts.close()
        self.mock_app.state.database.close()
