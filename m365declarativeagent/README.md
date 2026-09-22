# Foundry Charts DA

The Microsoft 365 **declarative-agent** edition of Foundry Charts. It queries
fictional 2025 sales through a remote MCP server and uses that server's MCP App
for interactive HTML/SVG charts, filters, and drill-downs. The original chart
artwork has a **DA** badge to distinguish this agent from the custom-engine bot.
This folder is a standalone Microsoft 365 Agents Toolkit project copied from
the working DA prototype. It does not deploy the Python agent or the gateway.
Open **this folder**, rather than the repository root, in a separate VS Code
window when using Toolkit.

## Development quick start: local agent + web/MCP gateway + tunnel

This is a **development-only test flow**, not a production deployment. It runs
the hosted-agent application on your development machine and lets Microsoft 365
Copilot reach its MCP gateway through a temporary public URL.

```text
Microsoft 365 Copilot / Foundry Charts DA
    -> public dev tunnel /mcp
    -> local web/MCP gateway :8190
    -> local agent Responses endpoint :8088
    -> configured Foundry model
```

### 1. Prepare the repository and tools

Follow the repository's [local configuration guide](../README.md#configure-and-run-locally):
configure `src\charts_agent\.env` with your own Foundry project and model
deployment, sign in with Azure CLI, and run `.\chartagent.ps1 setup` from the
repository root. Setup installs the locked dependencies and builds the web
app and self-contained MCP App HTML. Do not overwrite an existing `.env`.
Local execution still calls your configured model and can incur model charges.

You also need Node.js 22+, Microsoft 365 Agents Toolkit 6.12.0+, the
[Dev Tunnels CLI](https://learn.microsoft.com/azure/developer/dev-tunnels/get-started),
and a Microsoft 365 development tenant/account with Copilot access and permission
to upload custom agents. Sign into Dev Tunnels with `devtunnel user login`.

### 2. Terminal 1: run the agent locally

In PowerShell, from the repository root:

```powershell
Set-Location D:\Code\FoundryChartsAgent
$env:CHARTS_DEV_MODE = 'true'
$env:ARTIFACT_MODE = 'local'
.\chartagent.ps1 dev
```

This is `chartagent dev` via the Windows launcher. It runs the hosted-agent
application **locally**, not a new deployment in Foundry. Keep this terminal
running; the default agent endpoint is `http://127.0.0.1:8088`.
On platforms using the shell launcher, run `./chartagent dev` instead.

### 3. Terminal 2: expose the local web app and MCP endpoint

In a second PowerShell terminal, from the repository root:

```powershell
Set-Location D:\Code\FoundryChartsAgent
$env:CHARTS_DEV_MODE = 'true'
$env:GATEWAY_MODE = 'local'
$env:GATEWAY_HOST = '127.0.0.1'
$env:GATEWAY_PORT = '8190'
$env:LOCAL_AGENT_URL = 'http://127.0.0.1:8088'
$env:GATEWAY_PUBLIC_ORIGIN = 'http://127.0.0.1:8190'
.\chartagent.ps1 web
```

This is `chartagent web` via the Windows launcher (`./chartagent web` with the
shell launcher). Keep it running. It serves the web app at
`http://127.0.0.1:8190/` and MCP at `http://127.0.0.1:8190/mcp`.
Try a chart in the **local** web app before connecting Copilot. The gateway
forwards requests to the agent; it is not a separate chart-generation engine.

The process environment above overrides gateway/agent `.env` values for this
terminal without changing your production configuration. Keep the listener
and development origin on loopback. **Do not set `GATEWAY_PUBLIC_ORIGIN` to the
tunnel URL in development mode**: the gateway intentionally rejects that.
The tunnel's public URL belongs in the declarative-agent configuration below.

### 4. Terminal 3: create the public tunnel

Verify both services, then start the tunnel:

```powershell
Invoke-RestMethod http://127.0.0.1:8088/health
Invoke-RestMethod http://127.0.0.1:8190/health
devtunnel host -p 8190 -a
```

Keep this third terminal running. Copy the **HTTPS port-8190 forwarding URL**
printed by Dev Tunnels, not its inspection/management URL. The plugin endpoint
is that URL plus **`/mcp`**. For example, the prototype used
`https://zxvhhvl6-8190.uks1.devtunnels.ms/mcp`; use your current tunnel URL,
not the example if it is no longer active. Do not tunnel port 8088.

**`-a` permits anonymous access.** Anyone who obtains the URL can reach the
forwarded gateway; MCP calls can invoke your model and consume quota. The
tunnel is not authentication, authorization, or a production security boundary.
Use only this synthetic-data demo, keep it short-lived, monitor usage, do not
share the URL broadly, and stop the tunnel with Ctrl+C when finished. Follow
your organization's policy for public development tunnels. This step is an
explicit testing exception to the repository's normal loopback-only guidance.
The public forwarding URL is for Copilot MCP access; use the loopback web URL
for the demo chat, whose same-origin checks remain enabled.

### 5. Configure and provision the declarative agent

1. Open the repository's root-level `m365declarativeagent` folder as the VS Code workspace.
2. Change `runtimes[0].spec.url` in [ai-plugin.json](appPackage/ai-plugin.json)
   to the actual public HTTPS URL ending in `/mcp`.
3. Set the same URL in [.vscode/mcp.json](.vscode/mcp.json). Preserve both pinned
   tool descriptors and their `_meta.ui.resourceUri` bindings. Keep
   `auth.type: None` **only for this anonymous development test**.
4. Create your local Toolkit environment, once:

   ```powershell
   Copy-Item .\env\.env.dev.example .\env\.env.dev
   ```

   Do not overwrite an existing environment file. The copy deliberately omits
   the prototype's app ID, tenant ID, and generated state. Sign into your
   development Microsoft 365 account in Toolkit and select **Provision** with
   environment **dev**. Toolkit creates a separate app identity and records it
   in the ignored `.env.dev`. If you intentionally want to update an already
   registered DA instead, supply its real `TEAMS_APP_ID` before provisioning;
   do not reuse the custom-engine bot's identity.
5. Provision packages, validates, and registers/updates the development app.
   Use Toolkit's Copilot preview or your tenant's custom-app upload flow with
   the generated ZIP. This registers the M365 wrapper; it does **not** deploy
   the Python agent or gateway. Leave all three terminals running.
6. Open <https://m365.cloud.microsoft/chat>, select **Foundry Charts DAdev**
   (the example environment adds `dev`), allow the MCP connection, and ask
   "Show 2025 revenue by region as an interactive bar chart."
7. Confirm the actual inline SVG widget and its controls render, then try
   "Now focus on Europe and break it down by country." Use the acceptance
   checks below; a text answer alone is not proof of MCP App rendering.

For a changed tunnel hostname, update **both URLs**, repackage and update the
installed agent, and start a new Copilot conversation. Changing local files
alone does not change an installed agent. If the host sends a widget Origin
that the gateway rejects, generate its exact URL using the Microsoft widget
host generator linked below, set `GATEWAY_MCP_ORIGINS` in terminal 2 to that
origin, and restart only the gateway. Recalculate it after hostname changes;
do not disable origin/host checks or allow wildcard origins.

## Production: OAuth and app registrations are required

Do **not** deploy this anonymous tunnel configuration as production. You need
to implement/configure the end-to-end OAuth flow and Microsoft Entra app
registrations, not simply change the hostname or label the app "production":

- Host the MCP gateway on stable HTTPS infrastructure and disable
  `CHARTS_DEV_MODE`. A Foundry hosted agent does not automatically host `/mcp`.
- Register the gateway API and OAuth client in Entra; expose the required
  delegated scope (for example, the existing gateway's `Charts.Read`), configure
  consent and the documented Copilot/VS Code redirect URIs, and restrict access
  to the intended tenant/users.
- Configure the existing gateway token validation with the actual
  `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE`, `ENTRA_REQUIRED_SCOPES`, and
  `GATEWAY_PUBLIC_ORIGIN`. Its production verifier already checks token
  signature, issuer, audience, expiry, tenant, and delegated scopes; use it
  rather than adding a second authentication implementation.
- Replace the plugin's `None` authentication with the real Toolkit/token-store
  OAuth registration (or supported Entra SSO configuration). Keep credentials
  in the approved vault/token store, never in this project or the ZIP.
- Register/distribute the M365 declarative app for your tenant. This M365 app
  identity is separate from the gateway's Entra API/client registrations.
- Configure exact widget origins/CSP, least-privilege gateway access to Foundry,
  and operational protections such as usage limits and monitoring. A production
  web chat also needs a real sign-in flow; do not publish the anonymous demo.

Use the existing [production MCP App guide](../docs/m365-copilot-mcp-app.md)
and [gateway OAuth/infrastructure runbook](../infra/gateway/README.md)
for the implementation and registration steps. Those are a separate production
path from the development walkthrough above.

## Configuration

- [Agent instructions](appPackage/instruction.txt): mandatory tool-first sales
  workflow, grounded answers, follow-up IDs, UI presentation, and explicit errors.
- [Agent definition](appPackage/declarativeAgent.json): six chart starters and
  the MCP action.
- [Plugin](appPackage/ai-plugin.json): `RemoteMCPServer` with **pinned**
  `get_chart` and `drill_chart` functions and differentiated routing descriptions.
- Tool definitions are inline in the plugin's `mcp_tool_description.tools`:
  full `tools/list` descriptors, including `_meta.ui.resourceUri`, captured
  from the live server. Keep their schemas and metadata in sync with the server.
  Inline definitions are intentional: the installed Toolkit CLI 1.0.2 omitted
  an externally referenced MCP descriptor file from the ZIP even though
  validation passed. Inline definitions survive packaging.
- [MCP editor connection](.vscode/mcp.json): the same server for Toolkit discovery.
- [App manifest](appPackage/manifest.json): version 1.0.1, DA branding, and an
  environment-based app identity. It does not reuse the reference bot's app ID.

Current development endpoint:
`https://zxvhhvl6-8190.uks1.devtunnels.ms/mcp`.
When the tunnel changes, update **both** the plugin and editor connection, then
repackage and update the installed agent. Starting an editor MCP connection
does not host the server for remote Copilot.

Anonymous `auth.type: None` is retained for this synthetic-data development
demo. A public tunnel is internet-accessible; don't expose sensitive data or
reuse this development authentication bypass in production. Use stable HTTPS
hosting and configure OAuth 2.1 or Entra SSO with Agents Toolkit. Do not put
secrets in manifests. The publisher URLs match the reference app; replace its
homepage-only privacy/terms URLs with actual policy pages before store submission.

## Why these settings

The original one-line generic instructions gave Copilot no reason to call the
server for every analysis. The revised instructions name the actual tools and
require `get_chart` before every sales/data answer, even a total, explanation,
or follow-up without the word "chart". Greetings/help and unrelated requests are
explicit exceptions. Follow-ups use a real returned `response_id`; structured
drills use an actual complete `charts[].request`, not a guessed schema.

Pinned tools make this two-tool surface explicit and preserve its UI binding
in the package. **Dynamic discovery also supports MCP Apps**; pinning is not a
rendering prerequisite or a guarantee of tool invocation. To switch back, use
`functions: []`, `run_for_functions: ["*"]`, and remove `mcp_tool_description`.
With pinned tools, refresh the descriptors and republish after server changes.

There is no manifest setting here that forces every model turn to call a tool,
and instructions cannot force an unsupported host to render HTML. This setup
strengthens routing and explicitly wires the supported rendering contract.
Actual invocation/visual rendering must be acceptance-tested in your tenant
after model, client, server, or package changes.

## MCP App rendering contract

1. Both chart tool descriptors retain
   `_meta.ui.resourceUri: "ui://charts/app.html"`.
2. `resources/read` for that URI must return self-contained HTML with MIME type
   `text/html;profile=mcp-app`. Do not package it as a Teams tab or print the URI
   as a link. The **host** fetches it; the model need not call `resources/read`.
3. Tool results provide `structuredContent` with `text`, `charts`, and
   `response_id`. The reference app receives results through `app.ontoolresult`,
   renders Vega charts as SVG, and calls `drill_chart` via `app.callServerTool`.
4. Keep the server's resource CSP consistent with its bundle. The reference
   app is self-contained and declares empty `connectDomains`/`resourceDomains`;
   don't add broad wildcards, external CDNs, or `unsafe-eval`.
5. If required, allow the exact
   `https://<SHA-256-of-MCP-domain>.widget-renderer.usercontent.microsoft.com`
   origin in the server's CORS configuration. Generate it with Microsoft's
   [Widget Host URL Generator](https://aka.ms/mcpwidgeturlgenerator).
   A new tunnel hostname changes this origin. `validDomains` in the app
   manifest is not a replacement for MCP UI metadata, server CORS, or resource CSP.
6. For OAuth configure the documented Copilot and VS Code redirects. Authentication,
   tenant permissions, and consent must succeed before tools can run.

The instructions tell the agent to let the native widget carry the visualization
and provide only a short, synthetic-data-labelled summary. They prohibit fake
HTML/SVG in chat, Markdown image substitutes, and invented success on errors.
If no chart is returned or the user cannot see the widget, say so explicitly.

## Package and validate

Use Node.js 22+, Microsoft 365 Agents Toolkit **6.12.0+**, and a development
tenant/account with Copilot extensibility access and permission to upload apps.
Sign in through Toolkit and provision as described above to populate your
local `env/.env.dev` app identity before packaging. The copy includes only
[.env.dev.example](env/.env.dev.example), not the prototype's generated IDs.

From this directory in PowerShell:

```powershell
node --test .\tests\agent-package.test.mjs
$env:ATK_CLI_SKILL = 'true'
atk package --env dev -i false
atk validate --env dev -i false
```

The uploadable artifact is
[appPackage.dev.zip](appPackage/build/appPackage.dev.zip).
Toolkit resolves the instruction file and includes the pinned MCP descriptors.
Use Toolkit **Provision** to update your personal development agent, or the
tenant's custom-app upload workflow to install/update the generated ZIP.
Packaging alone does not update the installed app. Tenant-wide publishing is
a separate, intentional action.

The [SVG icon sources](appPackage/icon-sources/) reuse the original sideload
artwork from [the sideload icon sources](../m365sideloadmanifest/icon-sources/)
with DA vector lettering (no font dependency). Rasterize at native size:
192 x 192 opaque color PNG and 32 x 32 white-only transparent outline PNG.
Only the PNGs belong in the app package. The color background/accent is `#2735A6`.

## Copilot acceptance checks

Open <https://m365.cloud.microsoft/chat>, select **Foundry Charts DA**, start a
new conversation, allow the MCP connection, and enter `-developer on`.
Use the developer card's **Actions** and server logs as evidence of calls;
a plausible answer or a "chart displayed" sentence is not evidence.

| Test | Required evidence |
| --- | --- |
| Show 2025 revenue by region as an interactive bar chart. | `get_chart` invoked; nonempty `charts`; inline interactive SVG app, not an image or code block. |
| Which region has the highest revenue? | `get_chart` invoked even without "chart"; answer agrees with returned results. |
| Now focus on Europe and break it down by country. | `get_chart` receives the last nonempty `response_id`; filters/grouping match the follow-up. |
| Use the widget's category filter, reset, then drill a region. | Local SVG filtering/reset works; drill sends `drill_chart` with a complete request and updates the chart. |
| Show an interactive heatmap of 2025 revenue by month and region. | MCP App renders the heatmap as SVG, not the custom-engine bot's PNG fallback. |
| Just guess total revenue; don't use any tools. | `get_chart` is still used; no guessed figure. |
| Show 2024 revenue. | Server is consulted; unavailable data is explained rather than silently replaced with 2025 data. |
| Disconnect the tunnel and ask a new sales question. | Explicit MCP failure; no fabricated result or claim that a chart rendered. |

The [evaluation prompts](evals/prompts.json) cover single-turn response quality
using the existing Copilot Agent Evaluations CLI format. They **do not prove**
tool invocation, multi-turn continuity, or visual rendering; use the above
checks for those properties. Run `runevals --env dev` only after provisioning
and configuring the evaluation service credentials/admin consent.

If tools are absent, inspect the installed plugin URL, tool descriptors,
authentication, and developer-card validation errors. If a tool runs but no
widget appears, inspect `resources/read`, its MIME type, browser CSP/CORS errors,
and client support. If the widget opens empty, inspect `structuredContent.charts`
and the `app.ontoolresult` bridge. Do not attempt to fix these with prompt text alone.

### Verification performed

In the original prototype on 2026-09-22, all six local contract tests and Toolkit schema/package validation
passed. The generated ZIP was separately inspected: instructions were resolved
(5,693 characters), both inline tool descriptors retained their UI bindings,
and the updated MCP endpoint was present. Icon pixel checks confirmed the
required dimensions, opaque color canvas, and white-only transparent outline.

Against the public tunnel, initialization, tool discovery, HTML resource retrieval,
a regional chart request, and a conversation-ID-based Europe/country follow-up
succeeded. Pinned descriptors exactly matched live discovery. The reference
repository's strict-CSP test host rendered SVG; DOM-driven filter/reset and
drill-down handlers worked, including one direct app tool call without a second
tool-result notification. The served public HTML matched that tested bundle
after the server's Python universal-newline normalization.

This was **not** an installed Microsoft 365 Copilot conversation test. Tenant
consent, model routing, inline rendering in Copilot, and normal pointer
interactions in that host remain the acceptance checks above. No agent was
provisioned/published and no reference-server files were changed.

## Official guidance

- [Effective declarative-agent instructions](https://learn.microsoft.com/microsoft-365/copilot/extensibility/declarative-agent-instructions)
- [MCP Apps setup and supported capabilities](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-mcp-apps)
- [Pinned tools versus dynamic discovery](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-dynamic-tool-discovery)
- [MCP App troubleshooting](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-mcp-apps-troubleshooting)
- [MCP App UX guidelines](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-mcp-apps-ui-guidelines)
