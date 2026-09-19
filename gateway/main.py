from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from src.charts_agent.contracts import ChartRequest
from gateway.auth import SecurityMiddleware
from gateway.client import AgentClient, AgentError
from gateway.config import Settings


RESOURCE_URI = "ui://charts/app.html"
RESOURCE_MIME = "text/html;profile=mcp-app"
UI_META = {"ui": {"resourceUri": RESOURCE_URI}}
RESOURCE_META = {
    "ui": {
        "prefersBorder": True,
        "csp": {"connectDomains": [], "resourceDomains": []},
    }
}
MAX_BODY_BYTES = 32_768


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    previous_response_id: str | None = Field(default=None, max_length=256)


class DrillBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: ChartRequest


async def read_body(request: Request, model: type[BaseModel]) -> BaseModel:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise AgentError("Request body is too large.", 413)
    try:
        return model.model_validate_json(body)
    except ValidationError as exc:
        # Do not echo input values: requests may inadvertently contain credentials.
        details = "; ".join(
            f'{".".join(map(str, error["loc"]))}: {error["msg"]}'
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise AgentError(f"Invalid request: {details}", 422) from exc


class BrowserHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (
                        b"content-security-policy",
                        b"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                        b"img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
                        b"object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
                    ),
                ])
                message["headers"] = headers
            await send(message)
        await self.app(scope, receive, secure_send)


class McpCorsMiddleware:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.mcp = CORSMiddleware(
            app,
            allow_origins=list(settings.allowed_origins | set(settings.mcp_origins)),
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "MCP-Protocol-Version", "Mcp-Session-Id", "Last-Event-ID"],
            expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
        )

    async def __call__(self, scope, receive, send):
        cors_paths = {"/mcp", "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"}
        target = self.mcp if scope["type"] == "http" and scope["path"] in cors_paths else self.app
        await target(scope, receive, send)


def create_app(settings: Settings | None = None, agent_client: AgentClient | None = None, verifier=None) -> Starlette:
    settings = settings or Settings.from_env()
    settings.validate()
    client = agent_client or AgentClient(settings)
    host = urlsplit(settings.origin).hostname
    allowed_hosts = [host] if not settings.dev_mode else ["localhost", "127.0.0.1", "[::1]"]
    mcp = FastMCP(
        "Foundry Charts",
        instructions="Query the hosted sales analyst. All data are fictional 2025 sales.",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{item}:*" for item in allowed_hosts] + allowed_hosts,
            allowed_origins=list(settings.allowed_origins | set(settings.mcp_origins)),
        ),
    )

    @mcp.tool(meta=UI_META, structured_output=True)
    async def get_chart(message: str, previous_response_id: str | None = None) -> dict[str, Any]:
        """Ask the hosted sales agent for charts. Follow up with previous_response_id for continuity."""
        try:
            body = ChatBody(message=message, previous_response_id=previous_response_id)
            return await client.chat(body.message, body.previous_response_id)
        except ValidationError as exc:
            raise ToolError("Invalid chat input.") from exc
        except AgentError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(meta=UI_META, structured_output=True)
    async def drill_chart(request: dict) -> dict[str, Any]:
        """Render a validated ChartRequest via the hosted agent; never runs a separate chart engine."""
        try:
            parsed = ChartRequest.model_validate(request)
            chart = await client.drill(parsed)
            return {"text": chart["summary"], "charts": [chart], "response_id": None}
        except ValidationError as exc:
            raise ToolError("Invalid chart request.") from exc
        except AgentError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.resource(RESOURCE_URI, mime_type=RESOURCE_MIME, meta=RESOURCE_META)
    def chart_app() -> str:
        """Self-contained interactive chart. No network access or external assets are required."""
        path = settings.web_dist / "mcp-app.html"
        if not path.is_file():
            raise FileNotFoundError("Build the web clients with npm --prefix web run build.")
        return path.read_text(encoding="utf-8")

    async def chat(request: Request):
        body = await read_body(request, ChatBody)
        return JSONResponse(await client.chat(body.message, body.previous_response_id))

    async def drill(request: Request):
        body = await read_body(request, DrillBody)
        return JSONResponse(await client.drill(body.request))

    async def health(request: Request):
        return JSONResponse({"status": "ok", "service": "charts-gateway", "mode": settings.mode})

    async def protected_resource(request: Request):
        if settings.dev_mode:
            return JSONResponse(
                {"error": "OAuth is not configured in explicit loopback development mode."}, status_code=404
            )
        return JSONResponse({
            "resource": settings.origin + "/mcp",
            "authorization_servers": [settings.issuer],
            "scopes_supported": list(settings.required_scopes),
            "bearer_methods_supported": ["header"],
            "resource_name": "Foundry Charts",
        })

    async def agent_error(request: Request, exc: AgentError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)

    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        await client.start()
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await client.close()

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health),
            Route("/api/chat", chat, methods=["POST"]),
            Route("/api/chart", drill, methods=["POST"]),
            Route("/.well-known/oauth-protected-resource", protected_resource),
            Route("/.well-known/oauth-protected-resource/mcp", protected_resource),
            # An exact mount preserves POST /mcp without a redirect, important for OAuth clients.
            Route("/mcp", endpoint=mcp_app),
            Mount("/", app=StaticFiles(directory=settings.web_dist, html=True, check_dir=False)),
        ],
        exception_handlers={AgentError: agent_error},
        middleware=[
            Middleware(BrowserHeaders),
            Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts),
            Middleware(McpCorsMiddleware, settings=settings),
            Middleware(SecurityMiddleware, settings=settings, verifier=verifier),
        ],
    )
    app.state.mcp = mcp
    app.state.agent_client = client
    return app


def main() -> None:
    import uvicorn

    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / "gateway" / ".env", override=False)
    load_dotenv(root / "src" / "charts_agent" / ".env", override=False)
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=False)


if __name__ == "__main__":
    main()
