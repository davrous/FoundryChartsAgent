from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest
from starlette.testclient import TestClient

from src.charts_agent.contracts import ChartBundle, ChartRequest, ChartRow
from gateway.auth import EntraVerifier
from gateway.client import AgentClient, AgentError, parse_response
from gateway.config import Settings
from gateway.main import create_app, RESOURCE_MIME, RESOURCE_URI


def test_gateway_entry_point_imports_from_root_without_pythonpath():
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", "from gateway.main import main; assert callable(main)"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_entry_point_loads_gateway_then_agent_env_without_overriding_process_settings():
    from gateway.main import main

    root = Path(__file__).resolve().parents[1]
    with (
        patch("gateway.main.load_dotenv") as load,
        patch("gateway.main.Settings.from_env", return_value=Settings(dev_mode=True)),
        patch("uvicorn.run") as run,
    ):
        main()
    assert load.call_args_list[0].args == (root / "gateway" / ".env",)
    assert load.call_args_list[1].args == (root / "src" / "charts_agent" / ".env",)
    assert all(call.kwargs == {"override": False} for call in load.call_args_list)
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert run.call_args.kwargs["port"] == 8190


def test_local_agent_url_default_and_override(monkeypatch):
    monkeypatch.delenv("LOCAL_AGENT_URL", raising=False)
    assert Settings().local_agent_url == "http://127.0.0.1:8088"
    assert Settings.from_env().local_agent_url == "http://127.0.0.1:8088"
    monkeypatch.setenv("LOCAL_AGENT_URL", "http://127.0.0.1:9091")
    assert Settings.from_env().local_agent_url == "http://127.0.0.1:9091"


@pytest.fixture
def bundle():
    request = ChartRequest()
    return ChartBundle(
        id="test-chart", request=request, title=request.title, summary="Fictional revenue.",
        rows=[ChartRow(x="Europe", y=100)], vega_lite={"data": {"values": []}, "mark": "bar"},
        images={}, adaptive_card={}, adaptive_supported=True, fallback_reason=None,
    ).model_dump(mode="json")


def response_for(bundle):
    return {
        "id": "resp_123", "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{
            "type": "output_text", "text": f"Sales analysis.\n```chart\n{json.dumps(bundle)}\n```\nAll data are synthetic.",
        }]}],
    }


def mock_gateway(bundle, handler=None):
    requests = []

    def default_handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_for(bundle))

    settings = Settings(dev_mode=True)
    client = AgentClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler or default_handler)))
    return create_app(settings, client), client, requests


def test_extracts_chart_blocks_and_preserves_prose(bundle):
    result = parse_response(response_for(bundle))
    assert result["charts"] == [bundle]
    assert "```chart" not in result["text"]
    assert "Sales analysis." in result["text"]
    assert "All data are synthetic." in result["text"]
    assert result["response_id"] == "resp_123"


def test_bad_chart_and_failed_response_are_explicit_errors():
    with pytest.raises(AgentError, match="invalid chart"):
        parse_response({"output_text": "```chart\n{}\n```"})
    with pytest.raises(AgentError, match="could not complete"):
        parse_response({"status": "failed", "error": {"message": "secret"}})


def test_chat_and_drill_forward_to_the_hosted_responses_endpoint(bundle):
    app, _, requests = mock_gateway(bundle)
    headers = {"Origin": "http://localhost:8190"}
    with TestClient(app, base_url="http://localhost:8190") as client:
        response = client.post("/api/chat", json={"message": "Revenue?", "previous_response_id": "resp_old"}, headers=headers)
        assert response.status_code == 200
        assert response.json()["charts"] == [bundle]
        forwarded = json.loads(requests[0].content)
        assert str(requests[0].url) == "http://127.0.0.1:8088/responses"
        assert forwarded == {
            "input": "Revenue?", "stream": False, "previous_response_id": "resp_old",
            "metadata": {"chart_output": "interactive"},
        }
        response = client.post("/api/chart", json={"request": bundle["request"]}, headers=headers)
        assert response.status_code == 200
        assert response.json() == bundle
        forwarded = json.loads(requests[1].content)
        assert forwarded["input"] == "Render requested chart"
        assert forwarded["metadata"]["chart_output"] == "interactive"
        assert json.loads(forwarded["metadata"]["chart_request"]) == bundle["request"]
        assert "authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_foundry_uses_agent_reference_and_server_side_responses(bundle):
    settings = Settings(dev_mode=True, mode="foundry", project_endpoint="https://sample.services.ai.azure.com/api/projects/demo", agent_name="charts", agent_version="7")
    responses = SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(model_dump=lambda **_: response_for(bundle))))
    client = AgentClient(settings, responses=responses)
    result = await client.chat("Revenue?", "resp_previous")
    assert result["charts"] == [bundle]
    sent = responses.create.call_args.kwargs
    assert sent["extra_body"] == {"agent": {"type": "agent_reference", "name": "charts", "version": "7"}}
    assert sent["metadata"] == {"chart_output": "interactive"}
    assert sent["previous_response_id"] == "resp_previous"
    assert "project_endpoint" not in result
    await client.drill(ChartRequest.model_validate(bundle["request"]))
    assert responses.create.call_args.kwargs["metadata"]["chart_request"]


@pytest.mark.asyncio
async def test_drill_metadata_accepts_exactly_512_characters_and_rejects_513(bundle):
    _, agent, requests = mock_gateway(bundle)
    request = ChartRequest(query={"filters": {"region": ["A"]}})
    serialized = json.dumps(request.model_dump(mode="json"), separators=(",", ":"))
    request.query.filters["region"] = ["A" * (513 - len(serialized))]
    serialized = json.dumps(request.model_dump(mode="json"), separators=(",", ":"))
    assert len(serialized) == 512
    await agent.drill(request)
    assert json.loads(requests[0].content)["metadata"] == {
        "chart_output": "interactive", "chart_request": serialized,
    }
    request.query.filters["region"][0] += "A"
    with pytest.raises(AgentError, match="512-character metadata limit") as error:
        await agent.drill(request)
    assert error.value.status_code == 422
    assert len(requests) == 1
    await agent.http.aclose()


def test_oversized_drill_is_rejected_before_any_upstream_request(bundle):
    app, _, requests = mock_gateway(bundle)
    request = ChartRequest(query={"filters": {"product": ["LongProductName"] * 40}})
    with TestClient(app, base_url="http://localhost:8190") as client:
        response = client.post(
            "/api/chart", json={"request": request.model_dump(mode="json")},
            headers={"Origin": "http://localhost:8190"},
        )
        assert response.status_code == 422
        assert "512-character metadata limit" in response.json()["error"]
        assert requests == []


def test_remote_errors_are_propagated_without_credentials(bundle):
    def unavailable(request):
        return httpx.Response(401, json={"error": "credential=do-not-expose"})

    app, _, _ = mock_gateway(bundle, unavailable)
    with TestClient(app, base_url="http://localhost:8190") as client:
        response = client.post("/api/chat", json={"message": "Hello"}, headers={"Origin": "http://localhost:8190"})
        assert response.status_code == 502
        assert response.json()["error"] == "The hosted agent returned HTTP 401."
        assert "do-not-expose" not in response.text


def test_validation_origin_and_host_defenses(bundle):
    app, _, requests = mock_gateway(bundle)
    with TestClient(app, base_url="http://localhost:8190") as client:
        assert client.post("/api/chat", json={"message": "Hi"}).status_code == 403
        assert client.post("/api/chat", json={"message": "Hi"}, headers={"Origin": "https://attacker.example"}).status_code == 403
        response = client.post("/api/chart", json={"request": {"kind": "execute_code"}}, headers={"Origin": "http://localhost:8190"})
        assert response.status_code == 422
        assert "access-control-allow-origin" not in response.headers
        response = client.post("/api/chat", json={"message": "Hi", "endpoint": "http://attacker.example"}, headers={"Origin": "http://localhost:8190"})
        assert response.status_code == 422
        assert client.get("/health", headers={"Host": "attacker.example"}).status_code == 400
        assert client.get("/.well-known/oauth-protected-resource").status_code == 404
        assert requests == []


@pytest.mark.asyncio
async def test_mcp_tool_and_resource_metadata_and_direct_drill(bundle):
    app, agent, requests = mock_gateway(bundle)
    mcp = app.state.mcp
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {"get_chart", "drill_chart"}
    assert all(tool.meta["ui"]["resourceUri"] == RESOURCE_URI for tool in tools)
    resources = await mcp.list_resources()
    assert resources[0].mimeType == RESOURCE_MIME
    assert resources[0].meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
    if (Settings().web_dist / "mcp-app.html").is_file():
        content = list(await mcp.read_resource(RESOURCE_URI))[0]
        assert content.mime_type == RESOURCE_MIME
        assert content.meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": []}
        assert "<html" in content.content
    await mcp.call_tool("drill_chart", {"request": bundle["request"]})
    assert json.loads(requests[0].content)["input"] == "Render requested chart"
    await agent.close()


def test_streamable_http_path_and_structured_tool_results(bundle):
    app, _, requests = mock_gateway(bundle)
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-03-26"}
    with TestClient(app, base_url="http://localhost:8190") as client:
        response = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_chart", "arguments": {"message": "Revenue?"}},
        })
        assert response.status_code == 200
        assert response.json()["result"]["structuredContent"]["charts"] == [bundle]
        assert len(requests) == 1


def production_settings():
    return Settings(
        host="0.0.0.0", tenant_id="11111111-1111-1111-1111-111111111111",
        audience="api://charts", public_origin="https://charts.example", required_scopes=("Charts.Read",),
    )


def test_startup_fails_closed_without_auth():
    with pytest.raises(ValueError, match="Production requires"):
        Settings(host="0.0.0.0").validate()
    with pytest.raises(ValueError, match="loopback"):
        Settings(host="0.0.0.0", dev_mode=True).validate()
    with pytest.raises(ValueError, match="Production requires"):
        Settings().validate()
    production_settings().validate()


def test_bearer_challenge_and_real_entra_metadata(bundle):
    settings = production_settings()
    agent = AgentClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_for(bundle)))))
    verifier = SimpleNamespace(verify=AsyncMock(return_value={"sub": "user"}))
    with TestClient(create_app(settings, agent, verifier), base_url=settings.origin) as client:
        response = client.post("/mcp", json={})
        assert response.status_code == 401
        assert 'resource_metadata="https://charts.example/.well-known/oauth-protected-resource"' in response.headers["WWW-Authenticate"]
        metadata = client.get("/.well-known/oauth-protected-resource").json()
        assert metadata["authorization_servers"] == [settings.issuer]
        assert metadata["resource"] == "https://charts.example/mcp"
        assert metadata["scopes_supported"] == ["Charts.Read"]
        response = client.post("/api/chat", headers={"Origin": settings.origin, "Authorization": "Bearer end-user-token"}, json={"message": "Hi"})
        assert response.status_code == 200
        verifier.verify.assert_awaited_once_with("end-user-token")


def test_mcp_cors_is_explicit_and_does_not_allow_cross_origin_rest(bundle):
    widget = "https://exact-widget.example"
    settings = replace(production_settings(), mcp_origins=(widget,))
    agent = AgentClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_for(bundle)))))
    with TestClient(create_app(settings, agent), base_url=settings.origin) as client:
        headers = {
            "Origin": widget, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,mcp-protocol-version",
        }
        response = client.options("/mcp", headers=headers)
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == widget
        challenge = client.post("/mcp", headers={"Origin": widget}, json={})
        assert challenge.status_code == 401
        assert challenge.headers["Access-Control-Allow-Origin"] == widget
        assert "WWW-Authenticate" in challenge.headers["Access-Control-Expose-Headers"]
        assert client.post("/api/chat", headers={"Origin": widget}, json={"message": "Hi"}).status_code == 403
        headers["Origin"] = "https://attacker.example"
        assert client.options("/mcp", headers=headers).status_code == 400
    with pytest.raises(ValueError, match="Wildcard"):
        replace(settings, mcp_origins=("https://*.example",)).validate()


@pytest.mark.asyncio
async def test_entra_verification_validates_signature_issuer_audience_expiry_tenant_and_scope():
    settings = production_settings()
    verifier = EntraVerifier(settings)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier.jwks.get_signing_key_from_jwt = lambda _: SimpleNamespace(key=key.public_key())
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "user", "iss": settings.issuer, "aud": settings.audience,
        "tid": settings.tenant_id, "scp": "Charts.Read", "iat": now, "exp": now + timedelta(minutes=5),
    }
    assert (await verifier.verify(jwt.encode(claims, key, algorithm="RS256")))["sub"] == "user"
    for field, value in [
        ("iss", "https://attacker.example"), ("aud", "wrong"), ("tid", "wrong"),
        ("exp", now - timedelta(minutes=1)),
    ]:
        with pytest.raises(jwt.InvalidTokenError):
            await verifier.verify(jwt.encode({**claims, field: value}, key, algorithm="RS256"))
    with pytest.raises(PermissionError):
        await verifier.verify(jwt.encode({**claims, "scp": "Other.Read"}, key, algorithm="RS256"))
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.InvalidTokenError):
        await verifier.verify(jwt.encode(claims, other_key, algorithm="RS256"))


def test_built_mcp_html_has_no_external_assets_or_credentials():
    path = Settings().web_dist / "mcp-app.html"
    if not path.is_file():
        pytest.skip("Run npm --prefix web run build first")
    html = path.read_text()
    assert '<script type="module"' in html
    assert '<script src=' not in html
    assert 'src="https://' not in html
    assert 'href="https://' not in html
    assert "FOUNDRY_PROJECT_ENDPOINT" not in html
    assert "DefaultAzureCredential" not in html
