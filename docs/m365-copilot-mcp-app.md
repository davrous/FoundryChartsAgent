# Using the MCP App in Microsoft 365 Copilot

[Back to the main README](../README.md#copilot-integration)

The production MCP App flow remains unverified and deployment is paused at
tenant application onboarding. See the
[gateway provisioning runbook](../infra/gateway/README.md) for prerequisites
and the deployment sequence.

## Where do I get the production MCP Server URL?

**For this sample's current deployment, use the separate gateway.** The
deployment completed here publishes Responses and Activity endpoints. Its MCP server is
[gateway/main.py](../gateway/main.py), a **separate service**. The local address
`http://127.0.0.1:8190/mcp` works with Claude Desktop on your computer, but
Microsoft 365 Copilot cannot reach that address.

For this implementation, host the gateway at a reachable HTTPS origin. Its MCP Server URL is
**that origin plus `/mcp`**. Step 3 below retrieves the actual Azure-assigned
hostname rather than guessing one. No production gateway was deployed by the
Foundry deployment described in this repository.

**Native Foundry alternative to investigate:** current
[Foundry documentation](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-agent#protocols)
also lists an agent-native MCP endpoint in preview. This is not yet enabled
or tested here, and custom MCP Apps tool metadata / HTML-resource support has
not been established. Confirm those capabilities before assuming a separately
hosted gateway is an unavoidable platform requirement.
The steps below remain the implemented separate-gateway path.

```text
M365 Copilot chat
  -> declarative agent + RemoteMCPServer plugin
  -> HTTPS gateway /mcp                  [new, separately hosted service]
  -> Foundry Responses + agent_reference [existing production charts-agent]
  <- ChartBundle + ui://charts/app.html
  -> interactive SVG widget inside the Copilot conversation
```

This reuses the **same MCP tools, widget, and production Python agent**.
The declarative agent is a Copilot-facing wrapper, not another deployment of
the Python analyst. No Teams tab, new model, or new Blob container is required.

| Address or package | Purpose | Use as MCP Server URL? |
|---|---|---|
| Foundry project endpoint ending in `/api/projects/proj-dav` | Gateway's server-side Foundry connection | No |
| Foundry Responses or Activity endpoint | Hosted-agent protocols | No |
| Blob PNG/SVG SAS URL | Individual chart download | No |
| `ui://charts/app.html` | Resource identifier fetched through MCP, not an HTTPS website | No |
| HTTPS origin of the deployed gateway, followed by `/mcp` | Authenticated Streamable HTTP MCP | **Yes** |
| [m365sideloadmanifest](../m365sideloadmanifest/README.md) | Existing Activity/custom-engine bot package | No; keep it unchanged |
| [appPackage](../appPackage/README.md) | Declarative-agent/MCP plugin reference files | Use this integration model |

The following walkthrough uses **Linux Azure App Service with Python 3.13**
as one concrete gateway hosting option. Other HTTPS hosts can implement the
same contract. These are instructions, not a claim that the gateway or M365
tenant integration has already been deployed or verified.

### Reproducible provisioning

The [production gateway infrastructure and helpers](../infra/gateway/README.md)
provide a pinned-AVM Bicep deployment, an allowlisted source ZIP, resumable Entra
registrations, secret-to-Key-Vault provisioning and a separate M365 Toolkit
project. Use that runbook for automated provisioning; the sections below
explain the protocol, identity and tenant-side configuration in detail.
Scaffolding these files does not create cloud resources or authorize deployment.

## 1. Check prerequisites and approve the new hosting

- Use a Microsoft 365 account with Copilot access and custom app upload enabled.
  In Microsoft 365 Agents Toolkit's **Accounts** pane, check **Copilot Access
  Enabled** and **Custom App Upload Enabled**. Tenant policy and licensing
  govern availability; ask the tenant administrator if either is unavailable.
- Install Microsoft 365 Agents Toolkit **6.12.0 or later** in VS Code.
- Confirm permission to create/configure an App Service and its paid hosting
  plan, register Entra applications, grant consent, and assign Foundry roles.
  Obtain approval before executing resource-changing steps.
- Use an approved tenant in which the intended Copilot users can sign in.
  The gateway currently validates a single tenant; cross-tenant distribution
  requires a separate identity design, not disabling tenant validation.
- Have Azure CLI available and signed in. Work from this repository's root in
  Bash for the shell examples. Complete the normal local setup first.

**Two authentication hops must work independently:**

1. **Copilot user -> gateway:** a delegated Entra access token for the gateway API.
2. **Gateway -> Foundry:** the gateway's own managed identity, using
   `DefaultAzureCredential`. The sample does not forward the user's token to
   Foundry or implement on-behalf-of impersonation.

The gateway managed identity, gateway API registration, OAuth client
registration, M365 app ID, and existing hosted-agent runtime identity are
different identities. Do not interchange their IDs.

## 2. Reuse the existing production agent

For the author's deployed environment, the existing target is:

| Setting | Existing value |
|---|---|
| Production azd environment | `charts-prod` |
| Foundry project endpoint | `https://davrousfoundry.services.ai.azure.com/api/projects/proj-dav` |
| Agent name | `charts-agent` |
| Last verified version | `2`, on 2026-09-19; confirm the active version before configuring the gateway |

Confirm the version using the existing environment, without redeploying:

```bash
AZURE_DEV_USER_AGENT=microsoft_foundry_skill \
  azd ai agent show charts-agent --environment charts-prod --output json
```

Use that verified version for `FOUNDRY_AGENT_VERSION`. Pinning makes the
integration reproducible; update it deliberately after testing a new version.
Another user of this repository must substitute their approved project and
agent rather than reuse the author's resources.

The existing agent and its Blob roles remain unchanged. The gateway selects
interactive output through Responses metadata, so normal Responses clients
still get PNG/SVG and Activity clients still get cards/image fallback.

## 3. Create the gateway host and retrieve its real URL

In the Azure portal, create a **separate Web App**:

1. Select the approved subscription, resource group, region, and hosting plan.
2. Choose **Code**, **Linux**, and **Python 3.13**, matching the sample runtime.
   Check availability with `az webapp list-runtimes --os linux` if necessary.
3. Enable HTTPS-only access. Use a plan suitable for the expected workload;
   enable Always On where supported to reduce cold starts.
4. Under **Identity**, enable the **system-assigned managed identity**.
5. Keep application authentication in this sample's JWT middleware. Do not
   put an App Service interactive-login redirect in front of MCP: clients
   need the gateway's bearer challenge, not a login HTML page. Configuring an
   additional platform authentication layer requires separate integration.

Record the actual resource names in your shell when prompted:

```bash
set -euo pipefail
read -r -p "Gateway subscription ID: " GATEWAY_SUBSCRIPTION_ID
read -r -p "Gateway resource group: " GATEWAY_RESOURCE_GROUP
read -r -p "Gateway Web App name: " GATEWAY_APP_NAME

GATEWAY_HOSTNAME="$(az webapp show \
  --subscription "$GATEWAY_SUBSCRIPTION_ID" \
  --resource-group "$GATEWAY_RESOURCE_GROUP" --name "$GATEWAY_APP_NAME" \
  --query defaultHostName --output tsv)"
test -n "$GATEWAY_HOSTNAME"
export GATEWAY_PUBLIC_ORIGIN="https://$GATEWAY_HOSTNAME"
export MCP_SERVER_URL="$GATEWAY_PUBLIC_ORIGIN/mcp"
printf 'MCP Server URL: %s\n' "$MCP_SERVER_URL"
```

**Save that printed URL for Agents Toolkit.** It is allocated now, but the MCP
service will not be ready until you deploy and verify it below. You can also
copy **Default domain** from the Web App's Overview page and add `https://`
and `/mcp`. Do not assume the domain is simply the app name; Azure may add a
unique suffix. If you use a custom domain, configure its TLS binding first,
then consistently use that origin for the gateway, OAuth registration and plugin.

This host must be reachable by Copilot over HTTPS, while the gateway must be
able to reach Entra and the Foundry project. A private-endpoint-only deployment
needs a supported connectivity design; localhost and private DNS alone do not
make a server reachable by Copilot.

## 4. Grant the gateway permission to invoke Foundry

On the **existing Foundry project**, have an administrator assign **Foundry
Agent Consumer** to the Web App's system-assigned managed identity. This is the
current least-privilege built-in role for agent endpoint invocation. Use the
project scope for this walkthrough; agent-scoped assignments are also supported.
See [hosted-agent interaction permissions](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions#agent-interaction).

Do not assign this permission to the OAuth client or the existing hosted-agent
identity by mistake. Azure Contributor alone does not provide Foundry data-plane
invocation rights. Allow RBAC propagation before diagnosing an immediate 403;
do not solve it by granting subscription Owner.

The gateway does not upload chart images itself and does **not** need the
hosted agent's Blob contributor/delegator roles. The hosted agent retains them.

## 5. Register the gateway API and Copilot OAuth client

Use **OAuth with static registration** for this walkthrough, matching the
sample's `OAuthPluginVault` plugin. Microsoft Entra does not support MCP dynamic
client registration. Entra SSO is another supported Copilot option, but has
different setup; do not mix its consent redirect with this OAuth flow.

In **Microsoft Entra ID > App registrations**:

1. Create a single-tenant **gateway API** registration. Record its Directory
   (tenant) ID and Application (client) ID.
2. Under **Expose an API**, use its Application ID URI and add a delegated
   scope named **`Charts.Read`**. With the default Application ID URI, the
   full scope is `api://<gateway-api-client-id>/Charts.Read`.
3. In the API registration's manifest, set
   **`api.requestedAccessTokenVersion` to `2`**. The gateway validates the
   tenant's v2 issuer and the API client-ID audience.
4. Create a separate **Copilot OAuth client** registration in the same tenant.
   Add the gateway's `Charts.Read` delegated permission under **API permissions**
   and obtain the required administrator consent.
5. Configure these **Web** redirect URIs on that OAuth client:
   - `https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect`
   - `https://vscode.dev/redirect`
6. Create the OAuth client's secret if using a confidential Web client, and
   keep it in the approved secret-management workflow. Enter it only through
   the Toolkit/token-store registration UI; never put it in the repository,
   gateway deployment ZIP, plugin manifest, or shell history.

Keep PKCE enabled. These OAuth fields will be needed in step 8:

| Field | Value to use |
|---|---|
| Client ID | **OAuth client** application ID, not the gateway API ID |
| Client secret | OAuth client secret, entered securely |
| Authorization endpoint | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/authorize` |
| Token and refresh endpoint | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token` |
| Requested scopes | Full gateway scope, followed by `offline_access` for refresh tokens |
| Gateway `ENTRA_AUDIENCE` | **Gateway API** application/client ID, matching the v2 access token's `aud` |
| Gateway `ENTRA_REQUIRED_SCOPES` | `Charts.Read`, matching the token's `scp` claim |

The angle-bracket values above describe the IDs you just registered; they are
not literal values to copy. The short `Charts.Read` claim checked by the gateway
is **not** a substitute for the full API scope in the OAuth authorization request.
Use an **access token for the gateway**, not an ID token or a Foundry/Graph token.

## 6. Configure the gateway and Copilot widget origin

Open Microsoft's [Widget Host URL Generator](https://aka.ms/mcpwidgeturlgenerator)
and enter the real MCP Server URL from step 3. Record the exact generated
`https://...widget-renderer.usercontent.microsoft.com` origin.

In the Web App's **Environment variables / App settings**, set:

| Setting | Value |
|---|---|
| `GATEWAY_MODE` | `foundry` |
| `FOUNDRY_PROJECT_ENDPOINT` | Existing project endpoint from step 2, not the agent-specific Responses URL |
| `FOUNDRY_AGENT_NAME` | `charts-agent` for the author's deployment |
| `FOUNDRY_AGENT_VERSION` | Active version confirmed in step 2 |
| `GATEWAY_HOST` | `0.0.0.0` inside App Service |
| `GATEWAY_PORT` | `8000` for this App Service startup |
| `GATEWAY_PUBLIC_ORIGIN` | Exact HTTPS origin from step 3, without `/mcp` or a trailing slash |
| `CHARTS_DEV_MODE` | `false` |
| `ENTRA_TENANT_ID` | Tenant from step 5 |
| `ENTRA_AUDIENCE` | Gateway API client ID from step 5 |
| `ENTRA_REQUIRED_SCOPES` | `Charts.Read` |
| `GATEWAY_MCP_ORIGINS` | Exact generated HTTPS widget origin; no wildcard or path |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` to install Python dependencies during ZIP deployment |

Set the Web App's **Startup Command** to **`python -m gateway.main`**.
For example, after recording the step 3 variables:

```bash
az webapp config set \
  --subscription "$GATEWAY_SUBSCRIPTION_ID" \
  --resource-group "$GATEWAY_RESOURCE_GROUP" --name "$GATEWAY_APP_NAME" \
  --startup-file "python -m gateway.main" --output none
```

Use the **system-assigned identity** for `DefaultAzureCredential`; do not set
`AZURE_CLIENT_ID` to the OAuth client or gateway API ID. No model key or Foundry
credential belongs in the M365 package. Do not copy either local `.env` to Azure.

The widget's empty MCP `connectDomains` / `resourceDomains` can stay unchanged:
its libraries are bundled and drill-down goes through `app.callServerTool`,
not direct browser fetches to Foundry. HTTP CORS on the gateway is a separate
configuration from the widget resource's sandbox CSP.

## 7. Build, deploy, and check the gateway

Build the existing web assets. If dependencies are not installed yet, use the
normal setup first; an existing setup only needs the build:

```bash
npm --prefix web run build
```

Create a **gateway-only source ZIP** with an explicit allowlist. This avoids
uploading `.env`, local virtual environments, evaluation artifacts, or the
entire hosted-agent project. The shared contracts module is needed for imports;
chart generation still runs in Foundry.

```bash
set -euo pipefail
GATEWAY_BUNDLE="$(mktemp -d)"
mkdir -p "$GATEWAY_BUNDLE/gateway" "$GATEWAY_BUNDLE/src/charts_agent" \
  "$GATEWAY_BUNDLE/web/dist"
cp requirements.lock "$GATEWAY_BUNDLE/requirements.txt"
cp gateway/__init__.py gateway/main.py gateway/auth.py gateway/client.py \
  gateway/config.py "$GATEWAY_BUNDLE/gateway/"
cp src/charts_agent/contracts.py "$GATEWAY_BUNDLE/src/charts_agent/"
cp web/dist/mcp-app.html "$GATEWAY_BUNDLE/web/dist/"

(cd "$GATEWAY_BUNDLE" && zip -X -r gateway.zip requirements.txt gateway src web)
unzip -l "$GATEWAY_BUNDLE/gateway.zip"

az webapp deploy \
  --subscription "$GATEWAY_SUBSCRIPTION_ID" \
  --resource-group "$GATEWAY_RESOURCE_GROUP" --name "$GATEWAY_APP_NAME" \
  --src-path "$GATEWAY_BUNDLE/gateway.zip" --type zip
```

The requirements file at the ZIP root uses the repository's tested lock.
Remote build installs it; the platform activates its environment before the
custom startup command. Preserve the `gateway/`, `src/charts_agent/`, and
`web/dist/` paths exactly. There is deliberately **no custom chat `index.html`**
in this deployment: the demo chat has no production sign-in implementation.
This deployment serves the MCP resource, not a publicly usable demo chat.
After deployment, remove only the specific temporary bundle directory printed
by `printf '%s\n' "$GATEWAY_BUNDLE"` when you no longer need it.

Do not use the hosted agent's Dockerfile or `azd deploy charts-agent` to deploy
this gateway; those start the other service.

Check the deployed host:

```bash
curl --fail --silent --show-error "$GATEWAY_PUBLIC_ORIGIN/health"
curl --fail --silent --show-error \
  "$GATEWAY_PUBLIC_ORIGIN/.well-known/oauth-protected-resource/mcp"
curl --include --silent --show-error "$MCP_SERVER_URL"
```

Expected results:

- Health reports `"service":"charts-gateway"` and **`"mode":"foundry"`**.
- OAuth discovery reports the gateway's `/mcp` resource and the configured Entra
  authorization server.
- An unauthenticated `/mcp` request returns **401 with a bearer challenge**,
  not a successful anonymous response, HTML login page, or redirect to `/mcp/`.

Health is liveness, not proof that managed identity/RBAC or the production
agent works. After sign-in in step 8, require successful MCP initialization,
`tools/list`, a `resources/read` for `ui://charts/app.html`, and a real chart
tool call. These are the upstream and widget acceptance checks.

## 8. Create the Copilot declarative agent and register OAuth

Use a **separate Agents Toolkit project directory** so its M365 lifecycle
does not overwrite this repository's Foundry deployment configuration.

1. In VS Code, open **Microsoft 365 Agents Toolkit > Create a New Agent/App**.
2. Select **Declarative Agent > Add an Action > Start with an MCP Server**.
3. Paste the **real `MCP_SERVER_URL` from step 3**. Do not paste a Foundry URL,
   localhost address, `ui://` resource, or Claude's `npx` configuration.
4. Choose **OAuth (with static registration)**, not **None** or dynamic
   registration.
5. Enter the OAuth client ID/secret from step 5 securely. For scopes, enter
   the full gateway API scope and `offline_access`; do not leave scopes empty.
6. Choose a new project directory and a distinct display name, such as
   **Foundry Charts Interactive**, to distinguish it from the existing
   Activity-based Foundry Charts app.
7. Reuse the **instructions** and **conversation_starters** from
   [declarativeAgent.json](../appPackage/declarativeAgent.json) in the generated
   declarative agent. Preserve the generated schema, action-file references
   and app identity. Its instructions must delegate chart requests to
   `get_chart` and structured drill-down to `drill_chart`.

Toolkit discovers OAuth endpoints from the server's discovery documents,
creates an authentication configuration in the Microsoft Enterprise token
store, and wires `OAuthPluginVault.reference_id` in the plugin. If discovery
or the generated registration needs correction, use **Teams developer portal >
Tools > OAuth client registration** and the endpoint/scope values from step 5.
The registration's Base URL must match the MCP URL used by the plugin.

Review the registration before provisioning:

- Restrict organization usage to **My organization only** for this single-tenant
  sample.
- Current Microsoft MCP guidance requires **Any Teams app** for the app
  restriction. Binding it to an existing Teams app ID can produce runtime
  **404** errors even after successful provisioning. In a generated
  `oauth/register` action, the equivalent is `applicableToApps: AnyApp`;
  retain its generated `appId` field. Have the tenant administrator review
  this setting; it does not replace gateway token/scope validation.
- Keep PKCE enabled, verify both redirect URIs, and verify consent to the API
  scope. Provisioning does not verify that consent will succeed.
- `reference_id` is the **auth configuration ID**, not either Entra client ID
  and not the M365 app ID. No secret belongs in the manifest.
- To change a previously provisioned auth configuration, update it in the
  portal or use `oauth/update`; rerunning `oauth/register` does not overwrite
  an existing registration.

The current Toolkit wizard defaults to **dynamic tool discovery**, which also
discovers MCP Apps. The repository's [ai-plugin.json](../appPackage/ai-plugin.json)
instead demonstrates **pinned tools**. To use the same curated two-tool surface:

1. Open the generated `.vscode/mcp.json` and select **Start**.
2. Complete sign-in when requested.
3. Select **ATK: Fetch action from MCP**, select the generated `ai-plugin.json`,
   and choose **both `get_chart` and `drill_chart`**.
4. Verify their definitions retain `_meta.ui.resourceUri` pointing at
   `ui://charts/app.html`. Reading that resource must return
   `text/html;profile=mcp-app` and the resource CSP metadata.

Do not omit `drill_chart`: it is needed when the widget asks the host to drill
down. Do not strip the `_meta` fields when copying pinned definitions.
The generated `RemoteMCPServer` runtime must still target the HTTPS `/mcp` URL.
The self-contained widget is fetched from the gateway; it is **not** a Teams
tab, a Blob image URL, or an HTML file to place in the M365 app ZIP.

Alternatively, [appPackage/README.md](../appPackage/README.md) explains resolving
the checked-in package templates with real app, origin, OAuth reference and
publisher values. That folder is not a complete Toolkit provisioning project;
its package script does not create hosting, OAuth registrations, or tenant consent.

## 9. Validate, sideload, and test in M365 Copilot

1. In the generated M365 app project, use the existing verified
   [color icon](../m365sideloadmanifest/default-color-icon.png) and
   [white/transparent outline icon](../m365sideloadmanifest/default-outline-icon.png).
   Copy them to the icon paths named by **that project's manifest** and use
   accent color `#2735A6`. Reuse the artwork, **not** the Activity manifest,
   bot ID, or app ID. Supply actual publisher, privacy and terms URLs.
2. Validate the generated app package with Agents Toolkit. The app manifest,
   referenced declarative-agent/plugin files, pinned tool file if used, and
   icons must be at the ZIP paths their manifests reference. No unresolved
   `${{...}}` expressions or client secrets may remain.
3. In Toolkit's **Accounts** pane, sign in to the approved Microsoft 365 tenant
   and confirm the two access/upload prerequisites from step 1.
4. In **Lifecycle**, choose **Provision**, review the changes, and confirm.
   Wait for completion. This registers/packages the M365 wrapper and its
   authentication configuration; it does **not** deploy the gateway or
   redeploy the Foundry hosted agent.
5. Open [Microsoft 365 Copilot chat](https://m365.cloud.microsoft/chat). Find the
   new agent in the sidebar or **All agents**; Toolkit may append `dev` to its
   display name. Select it and complete the sign-in/connection consent prompt.
6. Ask **"Show 2025 revenue by region and channel as a grouped bar chart."**
   Require an inline interactive chart, not just the tool's text or a PNG.
7. Hover over a bar, filter a series locally, then drill into **Europe**.
   Confirm that the updated chart shows countries within Europe. Filtering
   should not call the server; drill-down must successfully call `drill_chart`.
8. Ask **"Show a heatmap of 2025 revenue by month and region."** Verify the
   interactive SVG grid, hover details and filtering. Activity's heatmap
   image fallback does not apply to this MCP Apps route.
9. Confirm gateway health still says `foundry` and its pinned version is the
   intended production agent. A successful local Claude test alone is not
   verification of this production path.

For broader distribution, use the organization's approved app publishing and
admin approval workflow, with explicit user/group rollout. Increment app
versions for package changes, rotate OAuth secrets before expiry, and re-test
after updating the gateway, pinned tools, hosted-agent version, or domain.

## Troubleshooting and verification boundaries

| Symptom | Check |
|---|---|
| No MCP URL in Foundry | Expected: deploy the gateway separately and retrieve its actual hostname in step 3. |
| 502/startup failure at the gateway host | Inspect App Service build/startup logs; Python version, root requirements file, module paths, startup command, built HTML and required production settings must be present. |
| 400 invalid host | Requested domain must match `GATEWAY_PUBLIC_ORIGIN`; review custom-domain/proxy host forwarding. |
| 401 during MCP use | Check API access-token audience, v2 issuer/token version, tenant, expiry and correct OAuth reference. Anonymous 401 before sign-in is expected. |
| 403 from the gateway | Check delegated `Charts.Read` consent/scope and exact allowed widget Origin. |
| Gateway works, but chart calls report Foundry 403 | Check the **gateway managed identity's** Foundry role and propagation, not the user's gateway token or Blob SAS. |
| AADSTS redirect/scope error | Match the Web redirects exactly; request the full API scope and obtain administrator consent. Do not choose DCR for Entra. |
| Provision succeeds, all MCP calls return 404 | Check OAuth registration app restriction (`AnyApp`), Base URL and auth configuration ID. |
| Tools return text but no widget | Verify `_meta.ui.resourceUri`, `resources/read`, MIME/CSP metadata, built bundle and the actual Copilot host's MCP Apps support. |
| Chart loads but drill-down fails | Include `drill_chart`, inspect its tool error, and check the host bridge plus gateway/Foundry logs. |
| Gateway `/` returns 404 | Expected for the MCP-only ZIP: the local demo chat is deliberately not deployed. |

As of the 2026-09-19 verification, the production Foundry agent and the local
Claude Desktop MCP App had been exercised; **this separately hosted gateway
and M365 MCP Apps tenant flow had not**. Hosting/authentication configuration
and an actual Copilot chart/drill test are still required before claiming
end-to-end success. The documented eight-file gateway bundle passed a local
isolated import/health/MCP smoke check, including both tool definitions and the
bundled UI resource, without Azure or model calls. Both web builds also passed;
neither check substitutes for remote-build, managed-identity, OAuth or tenant
acceptance testing. Microsoft documents `app.ontoolresult`,
`app.callServerTool` and resizing support, but client capabilities can differ;
do not assume download/fullscreen behavior matches Claude or the custom chat.

This remains a synthetic-data sample: the gateway authenticates users but does
not implement tenant-specific business-data authorization or per-user ownership
checks for supplied conversation IDs. Add those controls, rate limits and
appropriate monitoring before adapting it to private customer data.
Never expose `CHARTS_DEV_MODE=true` through a public tunnel or switch plugin
authentication to `None` to make production sign-in work.

Official references (reviewed 2026-09-19):

- [MCP Apps requirements, widget origins and supported capabilities](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-mcp-apps)
- [Build, provision and sideload an MCP-based declarative agent](https://learn.microsoft.com/microsoft-365/copilot/extensibility/build-mcp-plugins)
- [OAuth registration, PKCE, Entra constraints and app restrictions](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-authentication-oauth)
- [Dynamic discovery and pinning tools with Toolkit](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-dynamic-tool-discovery)
- [Foundry hosted-agent invocation permissions](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions#agent-interaction)
- [App Service Python runtime, remote build and startup configuration](https://learn.microsoft.com/azure/app-service/configure-language-python)
- [Troubleshoot MCP Apps in Copilot](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-mcp-apps-troubleshooting)
