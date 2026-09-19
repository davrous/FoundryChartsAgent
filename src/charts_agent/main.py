import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

load_dotenv(Path(__file__).with_name(".env"))
os.environ.setdefault("PORT", "8088")
os.environ.setdefault("OTEL_EXPERIMENTAL_RESOURCE_DETECTORS", "otel,host,os,process,service_instance")

from activity_bridge import build_host  # noqa: E402
from agent import CALLER, Caller, create_agent  # noqa: E402
from artifact_storage import ArtifactStore  # noqa: E402
from chart_service import ChartService  # noqa: E402
from contracts import ChartRequest  # noqa: E402


class CallerMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].rstrip("/") not in {"/responses", "/v1/responses"}:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body.extend(event.get("body", b""))
            if len(body) > 128_000:
                await JSONResponse({"error": "Request exceeds sample's 128KB budget"}, 413)(scope, receive, send)
                return
            if not event.get("more_body"):
                break
        try:
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Request must be a JSON object")
            metadata = payload.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be an object")
            mode = metadata.get("chart_output", "static")
            if mode not in {"static", "interactive"}:
                raise ValueError("chart_output must be static or interactive")
            structured = metadata.get("chart_request")
            request = ChartRequest.model_validate_json(structured) if structured else None
            if structured and len(structured) > 512:
                raise ValueError("chart_request exceeds the Responses metadata value limit of 512 characters")
            caller = Caller(output=mode, request=request)
        except (ValueError, TypeError, ValidationError) as error:
            await JSONResponse({"error": str(error)}, 422)(scope, receive, send)
            return
        replayed = False

        async def replay() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        token = CALLER.set(caller)
        try:
            await self.app(scope, replay, send)
        finally:
            CALLER.reset(token)


def create_app():
    artifacts = ArtifactStore()
    service = ChartService(artifacts)
    agent = create_agent(service, os.environ["FOUNDRY_PROJECT_ENDPOINT"], os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"])
    host = build_host(agent)
    if host.config.is_hosted and (artifacts.mode != "blob" or os.getenv("CHARTS_DEV_MODE", "").lower() == "true"):
        raise ValueError("Foundry hosting requires ARTIFACT_MODE=blob and CHARTS_DEV_MODE=false")
    host.add_middleware(CallerMiddleware)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "protocols": ["responses", "activity"], "data": "synthetic"})

    host.router.routes.append(Route("/health", health))
    if artifacts.mode == "local":
        host.router.routes.extend([
            Mount("/artifacts", app=StaticFiles(directory=artifacts.directory)),
            Mount("/mock", app=service.mock_app),
        ])
    original_lifespan = host.router.lifespan_context

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        try:
            async with original_lifespan(app):
                yield
        finally:
            await service.close()

    host.router.lifespan_context = lifespan
    return host


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    dev = os.getenv("CHARTS_DEV_MODE", "false").lower() == "true"
    create_app().run(host=os.getenv("HOST", "127.0.0.1" if dev else "0.0.0.0"))
