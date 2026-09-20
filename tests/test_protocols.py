import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from starlette.testclient import TestClient

from contracts import ChartBundle, ChartRequest, ChartRow


@pytest.mark.parametrize("dev_mode, port, expected_port", [
    ("true", None, "8088"),
    ("false", None, "8088"),
    ("true", "9091", "9091"),
])
def test_agent_port_default_and_override(dev_mode, port, expected_port):
    environment = {**os.environ, "CHARTS_DEV_MODE": dev_mode}
    environment.pop("PORT", None)
    if port is not None:
        environment["PORT"] = port
    result = subprocess.run(
        [sys.executable, "-c", (
            "import os\n"
            "from unittest.mock import patch\n"
            "with patch('dotenv.load_dotenv'):\n"
            "    import main\n"
            "print(os.environ['PORT'])\n"
        )],
        cwd=Path(__file__).resolve().parents[1] / "src" / "charts_agent",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected_port


def bundle(request: ChartRequest) -> ChartBundle:
    return ChartBundle(
        id="example", request=request, title=request.title, summary=f"Synthetic data: {request.title}",
        rows=[ChartRow(x="Europe", y=10)], vega_lite={"data": {"values": [{"x": "Europe", "y": 10}]}},
        images={"png": "https://example.invalid/test.png", "svg": "https://example.invalid/test.svg"},
        adaptive_card={"type": "AdaptiveCard", "version": "1.5", "body": [
            {"type": "Chart.VerticalBar", "data": [{"x": "Europe", "y": 10}],
             "fallback": {"type": "Image", "url": "https://example.invalid/test.png"}},
        ]},
        adaptive_supported=True, fallback_reason=None,
    )


@pytest.fixture
def host(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTS_DEV_MODE", "true")
    monkeypatch.setenv("ARTIFACT_MODE", "local")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/test")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "test")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    from chart_service import ChartService
    from main import create_app

    async def create(self, request):
        return bundle(request)

    monkeypatch.setattr(ChartService, "create", create)
    return create_app()


def request_body(mode="static", title="By region", stream=False):
    return {
        "input": "Render requested chart",
        "stream": stream,
        "metadata": {
            "chart_output": mode,
            "chart_request": ChartRequest(title=title).model_dump_json(),
        },
    }


def output_text(payload):
    return "\n".join(
        content.get("text", "")
        for item in payload["output"]
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    )


def test_default_responses_static_image(host):
    body = request_body()
    del body["metadata"]["chart_output"]
    with TestClient(host) as client:
        response = client.post("/responses", json=body)
    assert response.status_code == 200, response.text
    text = output_text(response.json())
    assert "![Chart](https://example.invalid/test.png)" in text
    assert "```chart" not in text
    assert "Chart.VerticalBar" not in text


def test_explicit_interactive_payload(host):
    with TestClient(host) as client:
        response = client.post("/responses", json=request_body("interactive"))
    assert response.status_code == 200, response.text
    text = output_text(response.json())
    payload = json.loads(text.split("```chart\n")[1].split("\n```")[0])
    assert ChartBundle.model_validate(payload).request.title == "By region"


def test_responses_stream_contains_completed_chart(host):
    with TestClient(host) as client:
        response = client.post("/responses", json=request_body(stream=True))
    assert response.status_code == 200
    assert "response.completed" in response.text
    assert "https://example.invalid/test.png" in response.text
    assert "response.failed" not in response.text


@pytest.mark.parametrize("metadata", [
    {"chart_output": "html"},
    {"chart_request": "not json"},
    {"chart_request": '{"kind":"execute_code"}'},
    ["not-an-object"],
])
def test_invalid_caller_metadata_rejected(host, metadata):
    with TestClient(host) as client:
        response = client.post("/responses", json={"input": "chart", "metadata": metadata})
    assert response.status_code == 422
    assert "error" in response.json()


async def test_request_context_isolated(host):
    import asyncio

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=host), base_url="http://local") as client:
        responses = await asyncio.gather(
            client.post("/responses", json=request_body("static", "Private A")),
            client.post("/responses", json=request_body("interactive", "Private B")),
        )
    a, b = (output_text(r.json()) for r in responses)
    assert "Private A" in a and "Private B" not in a and "```chart" not in a
    assert "Private B" in b and "Private A" not in b and "```chart" in b


def test_activity_drill_returns_native_card(host):
    with TestClient(host) as client:
        response = client.post("/api/messages", json={
            "type": "message", "id": "test-message", "channelId": "emulator",
            "serviceUrl": "http://localhost:56150/_connector",
            "from": {"id": "user"}, "recipient": {"id": "bot"},
            "conversation": {"id": "test-conversation"},
            "deliveryMode": "expectReplies",
            "value": {"action": "drill", "request": ChartRequest().model_dump()},
        })
    assert response.status_code == 200, response.text
    activities = response.json()["activities"]
    messages = [activity for activity in activities if activity["type"] == "message"]
    assert len(messages) == 1
    assert messages[0]["text"] == "Synthetic data: Revenue by region"
    assert messages[0]["attachments"]
    attachments = [a for activity in activities for a in activity.get("attachments", [])]
    assert attachments[0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachments[0]["content"]["body"][0]["type"] == "Chart.VerticalBar"


async def test_framework_tool_revalidates_nested_model(host):
    from agent import CALLER, Caller, create_agent
    from artifact_storage import ArtifactStore
    from chart_service import ChartService

    service = ChartService(ArtifactStore())
    agent = create_agent(service, "https://example.services.ai.azure.com/api/projects/test", "test")
    chart_tool = agent.default_options["tools"][1]
    caller = Caller()
    token = CALLER.set(caller)
    try:
        await chart_tool.invoke(arguments={"request": {"kind": "bar", "title": "Tool chart"}})
        assert caller.bundles[0].request.title == "Tool chart"
    finally:
        CALLER.reset(token)
        await service.close()
