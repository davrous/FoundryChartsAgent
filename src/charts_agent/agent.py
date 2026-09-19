import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from agent_framework import (
    Agent, AgentContext, AgentMiddleware, AgentResponse, AgentResponseUpdate,
    Content, Message, ResponseStream, tool,
)
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

from chart_service import ChartService
from contracts import ChartBundle, ChartRequest


@dataclass
class Caller:
    output: Literal["static", "interactive", "activity"] = "static"
    request: ChartRequest | None = None
    bundles: list[ChartBundle] = field(default_factory=list)


CALLER: ContextVar[Caller | None] = ContextVar("chart_caller", default=None)


def chart_text(bundles: list[ChartBundle], output: str) -> str:
    sections = []
    for bundle in bundles:
        if output == "interactive":
            sections.append(f"{bundle.summary}\n\n```chart\n{bundle.model_dump_json()}\n```")
        elif output == "activity":
            sections.append(bundle.summary)
        else:
            sections.append(
                f"{bundle.summary}\n\n![Chart]({bundle.images['png']})\n\n"
                f"[Download scalable SVG]({bundle.images['svg']})"
            )
    return "\n\n".join(sections)


class ChartOutputMiddleware(AgentMiddleware):
    """Keep rendering payloads out of the model and emit them without model rewriting."""

    def __init__(self, service: ChartService) -> None:
        self.service = service

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        caller = CALLER.get() or Caller()
        if context.stream:
            upstream = None
            if not caller.request:
                await call_next()
                if not isinstance(context.result, ResponseStream):
                    raise TypeError("Expected an Agent Framework response stream")
                upstream = context.result

            async def stream():
                token = CALLER.set(caller)
                try:
                    buffered = []
                    if caller.request:
                        caller.bundles.append(await self.service.create(caller.request))
                    elif upstream is not None:
                        async for update in upstream:
                            buffered.extend(c.text for c in update.contents if c.type == "text" and c.text)
                            update.contents = [c for c in update.contents if c.type != "text"]
                            if update.contents:
                                yield update
                    text = chart_text(caller.bundles, caller.output) if caller.bundles else "".join(buffered)
                    yield AgentResponseUpdate(
                        role="assistant", contents=[Content.from_text(text)],
                        message_id=f"chart-{uuid4().hex}",
                    )
                finally:
                    CALLER.reset(token)

            context.result = ResponseStream(stream(), finalizer=AgentResponse.from_updates)
            return

        token = CALLER.set(caller)
        try:
            if caller.request:
                caller.bundles.append(await self.service.create(caller.request))
                context.result = AgentResponse(messages=[])
            else:
                await call_next()
            if caller.bundles:
                result = context.result
                if not isinstance(result, AgentResponse):
                    raise TypeError("Expected an Agent Framework response")
                # Retain tool calls/results for follow-up queries, but not the model's rendering prose.
                for message in result.messages:
                    message.contents = [c for c in message.contents if c.type != "text"]
                result.messages = [m for m in result.messages if m.contents]
                result.messages.append(Message("assistant", [Content.from_text(chart_text(caller.bundles, caller.output))]))
        finally:
            CALLER.reset(token)


def create_agent(service: ChartService, endpoint: str, model: str) -> Agent:
    @tool(approval_mode="never_require")
    async def get_data_schema() -> str:
        """Get available fake sales dimensions, metrics, dates and filter values before querying."""
        return json.dumps(await service.schema())

    @tool(approval_mode="never_require")
    async def create_chart(request: ChartRequest) -> str:
        """Query the mock sales API and render a chart. Use filters to drill down; never invent data."""
        # This SDK builds the tool schema from the model, but invokes nested arguments as dicts.
        request = ChartRequest.model_validate(request)
        caller = CALLER.get()
        if caller is None:
            raise RuntimeError("create_chart requires a caller context")
        if len(caller.bundles) >= 3:
            raise ValueError("This sample permits at most three charts per turn")
        bundle = await service.create(request)
        caller.bundles.append(bundle)
        return json.dumps({"id": bundle.id, "summary": bundle.summary, "request": request.model_dump()})

    return Agent(
        client=FoundryChatClient(
            project_endpoint=endpoint, model=model, credential=DefaultAzureCredential(),
        ),
        tools=[get_data_schema, create_chart],
        middleware=[ChartOutputMiddleware(service)],
        default_options={"store": False, "allow_multiple_tool_calls": False},
        instructions=(
            "You analyze a FICTIONAL sales database for 2025. First use get_data_schema to discover values. "
            "For every visualization or data drill-down call create_chart; never invent or estimate data. "
            "Map user requests to ChartRequest. Follow-ups retain previous filters unless explicitly changed. "
            "Use region->country->product for drilling, month for trends, region/category as optional series. "
            "Use grouped_bar or stacked_bar for multiple-series bars; scatter uses units on x and metric on y; "
            "heatmap needs a series dimension. Gauge sums the metric against a target, default 1000000. "
            "Supported kinds: bar, horizontal_bar, grouped_bar, stacked_bar, line, area, pie, donut, scatter, "
            "heatmap, gauge. Dates must be YYYY-MM within 2025. Explain tool errors; do not claim a chart exists "
            "after a failed tool. No SQL or executable code. Rendering and channel selection are handled by "
            "the application; do not output image URLs, HTML, JSON fences or fabricated chart markup yourself. "
            "Keep non-chart answers brief and clearly label all data synthetic."
        ),
    )
