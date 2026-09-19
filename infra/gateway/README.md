# Production MCP gateway

This is a separate deployment from the Foundry hosted agent. It does not run
`azd deploy`, replace `azure.yaml`, or redeploy `charts-agent`.

The complete integration guide is [M365 Copilot and MCP Apps](../../docs/m365-copilot-mcp-app.md).
This directory adds reproducible gateway-only infrastructure and provisioning.
Run cloud-writing commands only after approving the target, cost and permissions.

## Identity and deployment order

1. Create the single-tenant Entra API and OAuth client before allocating paid
   hosting. Tenant restrictions can block these even when you own the Azure
   subscription.
2. Validate and deploy the Bicep. The gateway's managed identity calls the
   existing Foundry project; it does not forward the user's token.
3. Build and deploy the allowlisted gateway ZIP. Do not upload the whole
   repository, local environment files or the demo chat.
4. Create the OAuth credential directly into the dedicated Key Vault. Allow
   the vault's RBAC assignment to propagate before doing this.
5. Prepare the separate M365 project, sign in with the intended M365 tenant,
   and provision its OAuth token-store registration and declarative agent.
6. Generate the exact Copilot widget origin for the actual deployed `/mcp`
   URL, configure it in the gateway, then test authentication, charts and
   drill-down in Copilot. A successful health check is not end-to-end acceptance.

Use the repository's existing service virtual environment:

```bash
PYTHON=src/charts_agent/.venv/bin/python
STATE=infra/gateway/.state/prod.json
SUBSCRIPTION=480af19d-6138-460b-962c-e2a0b5aca925
TENANT=72f988bf-86f1-41af-91ab-2d7cd011db47
PREFIX=chartsmcp-prod-7c90
```

These are this workspace's approved target identifiers, not authorization to
deploy somebody else's copy into this subscription.

### Register Entra applications

```bash
"$PYTHON" infra/gateway/operations.py identity \
  --subscription "$SUBSCRIPTION" --tenant "$TENANT" --prefix "$PREFIX" \
  --state "$STATE" --apply
```

This creates two owned, tagged registrations and their service principals:

- API: `api://<actual-api-client-id>/Charts.Read`, with v2 access tokens.
- OAuth client: only that delegated API permission, with the Teams and VS Code
  redirect URLs. Tenant consent policy still applies.

State contains identifiers only, is target-bound, and is written with mode
`0600`. Reruns resume tagged registrations rather than creating duplicates.
Ambiguous or unrelated name collisions fail explicitly.

### Validate and deploy infrastructure

The checked-in parameters target the approved single-instance **Linux B1**
pilot in `davrousai-rg` / `northcentralus`. Review them before deployment.
The incremental estimate is approximately **$16/month**, excluding the existing
Foundry/model/storage and M365 costs. It is not a spending cap or a highly
available configuration.

Five pinned Azure Verified Modules provide the plan, site, app settings,
monitoring and vault. App settings are applied after site creation so
`GATEWAY_PUBLIC_ORIGIN` uses Azure's actual hostname. Startup, always-on,
Python 3.13, TLS, health checks and strict production authentication are
configured in Bicep, not patched manually afterward.

```bash
az bicep build --file infra/gateway/main.bicep \
  --outfile infra/gateway/.state/main.json
node infra/gateway/validate.mjs

API_CLIENT_ID="$(node -e \
  "console.log(JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8')).api.clientId)" \
  "$STATE")"

az deployment sub what-if \
  --subscription "$SUBSCRIPTION" --location northcentralus --name "$PREFIX" \
  --template-file infra/gateway/main.bicep \
  --parameters @infra/gateway/main.parameters.json gatewayApiClientId="$API_CLIENT_ID"
```

Supply the **real** API client ID from the identity helper; there is deliberately
no blank or invented audience in the parameters file. Review the what-if:
it must not replace the existing resource group, agent, model, storage, Activity
bot or Log Analytics workspace. The gateway receives only project-scoped
**Foundry Agent Consumer**. It has no Blob or Key Vault data-plane role.
The deploying user receives **Key Vault Secrets Officer** only on the new vault.
The vault retains the AVM default purge protection with seven-day soft-delete
retention; deleting it does not permit immediate purge/name reuse.

**Before provisioning:** verify regional B1 quota and tenant permission to
create the Entra applications. A successful what-if does not prove App Service
quota availability. The initial read-only quota check was blocked by an
unregistered `Microsoft.Quota` provider; resolve and recheck rather than
allocating resources as a quota experiment. Both `Microsoft.Quota` and
`Microsoft.Web` must be registered. After approved registration, this
workspace's North Central US B1 quota was verified at 10 instances with 0 used
on 2026-09-19.

Some organizational tenants also require a Service Tree association for new
Entra applications. This tenant returned `ServiceTreeValueMissing` before
creating the API registration. Obtain the approved service ID from the
application owner; do not invent one or reuse an unrelated service's ID.
Microsoft Graph exposes this association as `serviceManagementReference`.
Resolve this tenant requirement before creating paid hosting.

After explicit deployment approval and a satisfactory what-if:

```bash
az deployment sub create \
  --subscription "$SUBSCRIPTION" --location northcentralus --name "$PREFIX" \
  --template-file infra/gateway/main.bicep \
  --parameters @infra/gateway/main.parameters.json gatewayApiClientId="$API_CLIENT_ID" \
  --query properties.outputs -o json

GATEWAY_ORIGIN="$(az deployment sub show \
  --subscription "$SUBSCRIPTION" --name "$PREFIX" \
  --query properties.outputs.gatewayPublicOrigin.value -o tsv)"
printf 'MCP Server URL: %s/mcp\n' "$GATEWAY_ORIGIN"
```

Root outputs contain resource identifiers and URLs, not credentials. Discover
the widget origin using the official generator in the integration guide, then
repeat the same Bicep what-if/deployment with the additional parameter
`mcpOrigins="$MCP_WIDGET_ORIGIN"`. Persist that exact value in your local
deployment parameters for later redeployments. Do not use a wildcard or
overwrite an already-configured origin with the sample's initial empty value.

Application Insights uses the Linux `~3` extension. Local **telemetry ingestion**
authentication remains enabled because Python App Service autoinstrumentation
does not support Entra-authenticated ingestion; the gateway API still requires
delegated Entra tokens. Prompt/body/binary capture is disabled.

### Package only the gateway

```bash
npm --prefix web run build
"$PYTHON" infra/gateway/operations.py package \
  --output infra/gateway/.state/gateway-source.zip
```

The archive has exactly eight files: root `requirements.txt` from the existing
lock, five gateway modules, shared chart contracts, and the bundled MCP HTML.
The command verifies the archive and reports its SHA-256.

Deploy that ZIP with Azure authentication:

```bash
az webapp deploy \
  --subscription "$SUBSCRIPTION" --resource-group davrousai-rg \
  --name app-chartsmcp-prod-7c90 --type zip \
  --src-path infra/gateway/.state/gateway-source.zip
```

Both SCM and FTP basic authentication stay disabled. Do not substitute
`az webapp deployment source config-zip`, retrieve publishing credentials or
enable basic auth to bypass a deployment error.

After Oryx completes, verify `/health`, the protected-resource metadata,
anonymous `/mcp` returning 401, an authenticated MCP tool/resource request and
a real chart plus drill-down. `/health` is liveness only and does not prove
Foundry RBAC or Copilot rendering. The allowlisted ZIP excludes the demo chat.

### Create or rotate the OAuth credential

After the vault is deployed and its RBAC assignment is effective:

```bash
"$PYTHON" infra/gateway/operations.py credential \
  --subscription "$SUBSCRIPTION" --tenant "$TENANT" --prefix "$PREFIX" \
  --state "$STATE" --vault https://kv-chartsmcp-prod-7c90.vault.azure.net \
  --apply
```

The generated value moves directly from Entra to Key Vault in memory. It is not
printed, placed in command arguments or persisted in the state file.
Credentials expire after 90 days. A valid existing credential is reused.

To rotate, pass `--rotate`, then update the **existing** M365 OAuth configuration
using the Teams developer portal or Toolkit `oauth/update`. `oauth/register`
does not update an existing registration. Old Entra credentials are deliberately
not removed automatically: the existing token-store configuration may still use
them. Remove the old credential only after the updated configuration is tested.

### Prepare and provision the M365 wrapper

Set `GATEWAY_ORIGIN` to the actual HTTPS origin returned by the deployment,
without `/mcp`. Do not guess the Azure-generated hostname.

```bash
"$PYTHON" infra/gateway/operations.py prepare-copilot \
  --subscription "$SUBSCRIPTION" --tenant "$TENANT" --prefix "$PREFIX" \
  --state "$STATE" --origin "$GATEWAY_ORIGIN"

ATK_CLI_SKILL=true npx --yes --package @microsoft/m365agentstoolkit-cli@1.1.16 \
  atk auth login m365 --tenant "$TENANT" --telemetry false

"$PYTHON" infra/gateway/operations.py copilot \
  --subscription "$SUBSCRIPTION" --tenant "$TENANT" --prefix "$PREFIX" \
  --state "$STATE" --vault https://kv-chartsmcp-prod-7c90.vault.azure.net \
  --apply
```

Preparation reuses the main package templates and the previously validated
color/outline artwork. The new app is called **Foundry Charts Interactive**;
it has neither an Activity bot nor a Teams tab.

The [Toolkit lifecycle](copilot/m365agents.yml) uses static OAuth, PKCE,
`HomeTenant`, `AnyApp`, and the full API scope plus `offline_access`.
The helper injects the secret into the child process environment, never CLI
arguments, and redacts it from returned console output. Toolkit telemetry is
disabled for this invocation. Do not enable verbose credential logging.

The resulting ZIP is `copilot/appPackage/build/foundry-charts-interactive.zip`.
Registration/package validation is separate from tenant installation and from
actually seeing an interactive chart and successful drill-down.

## Validation

```bash
az bicep build --file infra/gateway/main.bicep --outfile infra/gateway/.state/main.json
node infra/gateway/validate.mjs
./chartagent test tests/test_gateway.py infra/gateway/tests/test_operations.py
src/charts_agent/.venv/bin/ruff check infra/gateway/operations.py infra/gateway/tests
```

Tests are local and mock cloud operations. They cover exact archive contents,
credential redaction, failed-write cleanup, safe rotation, registration scope
configuration, target-bound state and icon/template reuse. They do not prove
Azure permissions, tenant consent or Copilot acceptance.
