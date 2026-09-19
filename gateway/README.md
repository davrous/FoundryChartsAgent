# Independent chart gateway

The Starlette server is an **adapter**, not another chart agent. Both MCP tools
and REST endpoints call the hosted agent's Responses API. Credentials remain
server-side. Chart fences are parsed and validated against `ChartBundle`.
`gateway/requirements.txt` references the repository's tested `requirements.lock`;
no separate or conflicting MCP dependency stack is maintained. The lock includes
MCP 1.30.0, Starlette, Uvicorn, httpx, PyJWT/cryptography, python-dotenv,
Pydantic, Azure Identity/Projects, and OpenAI.

Run from the repository root after the parent's Python dependency setup:

```bash
# Optional if the existing agent .env already provides local settings:
cp gateway/.env.example gateway/.env
npm --prefix web ci
npm --prefix web run build
src/charts_agent/.venv/bin/python -m gateway.main
```

Defaults follow the project launchers: agent `127.0.0.1:8088`, gateway
`127.0.0.1:8190`. Override `LOCAL_AGENT_URL` / `GATEWAY_PORT` to use another
endpoint/port. The upstream endpoint is never accepted from a browser request.
`gateway.main` loads `gateway/.env`, then fills missing settings from
`src/charts_agent/.env`. Existing process environment always wins. Copying the
gateway example is optional for native development when the agent's `.env`
already has `CHARTS_DEV_MODE=true`.
With that virtual environment activated, the root-level entry command is simply
`python -m gateway.main`; no `PYTHONPATH` override is required.

On Windows, use `src/charts_agent/.venv/Scripts/python.exe -m gateway.main`
from the repository root. The root `chartagent web` / `chartagent.ps1 web`
launchers and VS Code gateway task use the same entry point.

## Hosting distinction

The hosted agent and this gateway are **two independent services**. Foundry
exposes its supported hosted-agent protocols; it does not automatically publish
the gateway's `/`, `/api/*`, `/mcp`, or OAuth-discovery routes. Setting
`GATEWAY_MODE=foundry` changes only the gateway's upstream transport to
`AIProjectClient` / OpenAI Responses with an `agent_reference`; it does not deploy
the gateway, embed a website in Foundry, or open a hidden Foundry HTTP route.

For external MCP clients, host the gateway separately with its own HTTPS ingress,
Entra authentication, server identity, network access to the Foundry project,
and the built `web/dist` artifacts. Point the Copilot plugin to that gateway's
public `/mcp` URL, never to the Foundry project endpoint. Provisioning,
deployment, DNS, certificates, and OAuth registration are intentionally outside
this sample's build and packaging commands.

| Variable | Purpose |
|---|---|
| `GATEWAY_MODE` | `local` (default) or `foundry` |
| `LOCAL_AGENT_URL` | Server-configured local Responses service base URL |
| `FOUNDRY_PROJECT_ENDPOINT` | HTTPS Foundry project endpoint in deployed mode |
| `FOUNDRY_AGENT_NAME`, `FOUNDRY_AGENT_VERSION` | Foundry agent reference; version optional |
| `GATEWAY_HOST`, `GATEWAY_PORT` | Listener, default loopback / 8190 |
| `CHARTS_DEV_MODE` | Explicit `true` permits anonymous loopback development only |
| `GATEWAY_PUBLIC_ORIGIN` | Required production HTTPS origin |
| `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE` | Required production tenant UUID and access-token audience |
| `ENTRA_REQUIRED_SCOPES` | Space-separated delegated scopes; default `Charts.Read` |
| `GATEWAY_MCP_ORIGINS` | Optional space-separated exact HTTPS MCP host/widget origins for CORS |

## Contracts

- `POST /api/chat`: `{message, previous_response_id?}` → `{text, charts, response_id}`
- `POST /api/chart`: `{request: ChartRequest}` → `ChartBundle`
- `GET /health`: gateway liveness, not an upstream readiness probe.
- `/mcp`: Streamable HTTP, `mcp==1.30.0`, tools `get_chart` and `drill_chart`.
- `ui://charts/app.html`: bundled HTML, `text/html;profile=mcp-app`; tool metadata
  `_meta.ui.resourceUri`, resource-content metadata `_meta.ui.csp` with empty
  `connectDomains` and `resourceDomains`.

Every forwarded request selects `metadata.chart_output="interactive"`. A drill
uses input `"Render requested chart"` and
`metadata.chart_request=json.dumps(request, separators=(",", ":"))`. Values of
exactly 512 characters are accepted; longer values return HTTP 422 (or an MCP
tool error) **before** invoking the host. No truncation, prompt fallback, or
model-generated reconstruction is used. The host's normal static image-link
responses are not changed by the gateway.

REST POST requests require the exact configured `Origin` and JSON content type.
There is no wildcard CORS. The gateway rejects unconfigured production startup
and anonymous non-loopback startup. Production `/api/*` and `/mcp` require
RS256 Entra bearer tokens: signature/JWKS, issuer, audience, expiry, tenant,
and delegated scopes are checked. Discovery is at
`/.well-known/oauth-protected-resource` (also `/mcp` suffixed); challenges point
there and advertise the real Entra authorization server.
For Copilot, add the exact widget renderer origin for your MCP domain if the
host requires browser CORS. Derive it using Microsoft's widget host URL
generator; never use a wildcard. These additional origins apply only to MCP,
not web REST. Preflight is unauthenticated; actual MCP calls still require
the bearer token.

The included chat is a **local-development client**. A production web deployment
must add a trusted sign-in/BFF proxy that obtains and forwards an appropriate
user access token server-side; the sample intentionally has no token-entry box
or browser-held Foundry credentials. Do not disable gateway auth to work around
this requirement. A production MCP host uses its configured OAuth integration.

Tests use injected `httpx.MockTransport` and a fake Foundry Responses client;
they do not call a model or consume Azure resources.

```bash
src/charts_agent/.venv/bin/python -m pytest tests/test_gateway.py -q
npm --prefix web test
```
