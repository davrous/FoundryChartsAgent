import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from src.charts_agent.contracts import ChartBundle, ChartRequest
from gateway.config import Settings


CHART_BLOCK = re.compile(r"```chart\s*\n(.*?)\n```", re.DOTALL)


class AgentError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def parse_response(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("error") or payload.get("status") in {"failed", "cancelled", "incomplete"}:
        raise AgentError("The hosted agent could not complete the request.")
    text = payload.get("output_text")
    if not isinstance(text, str):
        text = "\n".join(
            content["text"]
            for item in payload.get("output", [])
            if item.get("type") == "message" and item.get("role", "assistant") == "assistant"
            for content in item.get("content", [])
            if content.get("type") == "output_text" and isinstance(content.get("text"), str)
        )
    charts = []
    try:
        for match in CHART_BLOCK.finditer(text):
            charts.append(ChartBundle.model_validate_json(match.group(1)).model_dump(mode="json"))
    except (ValidationError, ValueError) as exc:
        raise AgentError("The hosted agent returned an invalid chart payload.") from exc
    return {
        "text": CHART_BLOCK.sub("", text).strip(),
        "charts": charts,
        "response_id": payload.get("id"),
    }


class AgentClient:
    """Only transport and framing live here; chart generation belongs to the agent."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None, responses: Any = None):
        self.settings = settings
        self.http = http_client
        self.responses = responses
        self._owns_http = http_client is None
        self._project = None
        self._credential = None
        self._openai = None

    async def start(self) -> None:
        if self.settings.mode == "local" and self.http is None:
            self.http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), follow_redirects=False)
        elif self.settings.mode == "foundry" and self.responses is None:
            from azure.ai.projects.aio import AIProjectClient
            from azure.identity.aio import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
            self._project = AIProjectClient(
                endpoint=self.settings.project_endpoint, credential=self._credential
            )
            self._openai = self._project.get_openai_client(timeout=120, max_retries=1)
            self.responses = self._openai.responses

    async def close(self) -> None:
        if self._owns_http and self.http is not None:
            await self.http.aclose()
        if self._openai is not None:
            await self._openai.close()
        if self._project is not None:
            await self._project.close()
        if self._credential is not None:
            await self._credential.close()

    async def chat(self, message: str, previous_response_id: str | None = None) -> dict:
        return await self._send(message, {"chart_output": "interactive"}, previous_response_id)

    async def drill(self, request: ChartRequest) -> dict:
        serialized = json.dumps(request.model_dump(mode="json"), separators=(",", ":"))
        if len(serialized) > 512:
            raise AgentError("The drill-down request exceeds the 512-character metadata limit.", 422)
        result = await self._send(
            "Render requested chart",
            {"chart_output": "interactive", "chart_request": serialized},
        )
        if len(result["charts"]) != 1:
            raise AgentError("The hosted agent did not return exactly one requested chart.")
        return result["charts"][0]

    async def _send(self, message: str, metadata: dict, previous_response_id: str | None = None) -> dict:
        payload = {"input": message, "metadata": metadata, "stream": False}
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        if self.settings.mode == "local":
            if self.http is None:
                raise RuntimeError("AgentClient.start() must be called before invoking the agent")
            try:
                response = await self.http.post(
                    self.settings.local_agent_url.rstrip("/") + "/responses", json=payload
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise AgentError("The hosted agent timed out. Please retry.", 504) from exc
            except httpx.HTTPStatusError as exc:
                status = 429 if exc.response.status_code == 429 else 502
                raise AgentError(f"The hosted agent returned HTTP {exc.response.status_code}.", status) from exc
            except httpx.RequestError as exc:
                raise AgentError("The hosted agent is unavailable.", 502) from exc
            try:
                return parse_response(response.json())
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                raise AgentError("The hosted agent returned an invalid response.") from exc
        from azure.core.exceptions import ClientAuthenticationError
        from openai import APIConnectionError, APIStatusError, APITimeoutError

        reference = {"type": "agent_reference", "name": self.settings.agent_name}
        if self.settings.agent_version:
            reference["version"] = self.settings.agent_version
        try:
            response = await self.responses.create(**payload, extra_body={"agent": reference})
        except APITimeoutError as exc:
            raise AgentError("The hosted agent timed out. Please retry.", 504) from exc
        except APIStatusError as exc:
            raise AgentError(
                f"The hosted agent returned HTTP {exc.status_code}.",
                429 if exc.status_code == 429 else 502,
            ) from exc
        except APIConnectionError as exc:
            raise AgentError("The hosted agent is unavailable.") from exc
        except ClientAuthenticationError as exc:
            raise AgentError("The gateway's server-side Azure authentication is unavailable.", 503) from exc
        return parse_response(response.model_dump(mode="json"))
