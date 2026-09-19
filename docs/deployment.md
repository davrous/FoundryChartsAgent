# Deploying the charts agent to Foundry

This is the deployment runbook for humans and coding agents. It deploys only the
Python hosted agent to an **existing** Foundry project and model, using an
existing Storage account and a private chart container. Creating resources or
granting roles requires the resource owner's approval.

The web/MCP gateway is a separate service. This procedure does not deploy it,
implement browser sign-in, install a Teams app, or create a Teams tab.

## 1. Prerequisites and target approval

- Python 3.13 and Node.js 20.19+ for the repository's local setup and checks.
- Azure CLI and Azure Developer CLI (`azd`), authenticated to the intended tenant.
  Azure CLI credentials and `azd` authentication are separate checks.
- The Foundry azd extensions required by [azure.yaml](../azure.yaml). The verified
  deployment used azd 1.30.0 and `azure.ai.agents` 1.0.0-beta.9. Check `azd version`
  and `azd extension list`; consult the installed CLI's `--help` for syntax.
- An existing Foundry project in a region supporting hosted agents, an existing
  tool-calling model deployment, and enough capacity for the configured 1 CPU /
  2 GiB runtime. Do not deploy a second model just to use this sample.
- Deployment permission on the project, normally **Foundry Project Manager**
  (formerly Azure AI Project Manager). See the official
  [hosted-agent permissions reference](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions).
- Permission to create a private Blob container if needed, and an administrator
  able to assign the two narrowly scoped storage roles in step 5. Project
  deployment permission does not by itself grant authority over a separate
  Storage account.
- HTTPS network access from the hosted runtime to the model/project and Storage,
  and from intended image viewers to Blob Storage. SAS does not bypass a
  storage firewall or private endpoint.

Obtain approval for the subscription, tenant, region, project, existing model,
storage account/container, deployment, and live model smoke test. Do not treat
this repository's example resource names as permission to use those resources.

The checked-in `services.ai-project.endpoint` in
[azure.yaml](../azure.yaml) names the original author's project. For another
project, change that value to the approved endpoint **as well as** configuring
the environment below. Keep the `charts-agent` service key and its `name` aligned
if intentionally renaming the agent. Keep `deployments: []`: the sample reuses a
model rather than provisioning one.

## 2. Separate local and production configuration

Run commands from the repository root. These examples use Bash; in PowerShell,
adapt variable assignments and line continuations and use `chartagent.ps1` for
local commands.

The agent's ignored `src/charts_agent/.env` is for local development. It is
excluded from deployment and is **not** the source of production configuration.
Preserve its `CHARTS_DEV_MODE=true` / `ARTIFACT_MODE=local` values.

For an existing deployment, reuse its production azd environment; do not
recreate it or overwrite its values. For a first deployment, replace every
placeholder below and create a separate environment:

```bash
PROD_ENV="charts-prod"
SUBSCRIPTION_ID="YOUR-SUBSCRIPTION-ID"
TENANT_ID="YOUR-TENANT-ID"
REGION="YOUR-APPROVED-REGION"
RESOURCE_GROUP="YOUR-FOUNDRY-RESOURCE-GROUP"
PROJECT_ENDPOINT="https://YOUR-ACCOUNT.services.ai.azure.com/api/projects/YOUR-PROJECT"
PROJECT_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.CognitiveServices/accounts/YOUR-ACCOUNT/projects/YOUR-PROJECT"
MODEL_DEPLOYMENT="YOUR-EXISTING-MODEL-DEPLOYMENT"
STORAGE_ACCOUNT="YOUR-EXISTING-STORAGE-ACCOUNT"
STORAGE_RESOURCE_GROUP="YOUR-STORAGE-RESOURCE-GROUP"
CONTAINER="charts"

az login --tenant "$TENANT_ID"
azd auth login --tenant-id "$TENANT_ID"
azd env new "$PROD_ENV" --subscription "$SUBSCRIPTION_ID" --location "$REGION"

azd env set --environment "$PROD_ENV" \
  AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID" \
  AZURE_TENANT_ID="$TENANT_ID" \
  AZURE_LOCATION="$REGION" \
  AZURE_RESOURCE_GROUP="$RESOURCE_GROUP" \
  AZURE_AI_PROJECT_ID="$PROJECT_ID" \
  AZURE_AI_PROJECT_ENDPOINT="$PROJECT_ENDPOINT" \
  FOUNDRY_PROJECT_ENDPOINT="$PROJECT_ENDPOINT" \
  AZURE_AI_MODEL_DEPLOYMENT_NAME="$MODEL_DEPLOYMENT" \
  ARTIFACT_MODE=blob CHARTS_DEV_MODE=false \
  AZURE_STORAGE_ACCOUNT_URL="https://$STORAGE_ACCOUNT.blob.core.windows.net" \
  AZURE_STORAGE_CONTAINER="$CONTAINER"
```

`azd env new` selects the new environment as the default. Use
`--environment "$PROD_ENV"` explicitly on subsequent environment-scoped commands
instead of trusting the current default. When resuming in a new shell, restore
the shell variables from the approved target; do not rerun `env new`.

Review the selected environment privately with `azd env get-values --environment
"$PROD_ENV"`; do not paste full environment dumps into logs, issues or commits.
The two endpoint variables and the project service endpoint must agree.
`AZURE_AI_PROJECT_ID` is the project's ARM resource ID, not its HTTPS endpoint.

Only variables declared in the agent service's `environmentVariables` are
explicitly forwarded from azd to this application's runtime. For example,
`ARTIFACT_TTL_MINUTES` defaults to 60 in code and is **not currently forwarded**.
To customize it for hosting, add it to that list as well as setting its azd
value, then validate and redeploy. Setting it only in local `.env` or azd state
does not change the deployed app. The supported range is 1-1440 minutes.

Use an existing private container, or create one only with approval:

```bash
az storage container create --subscription "$SUBSCRIPTION_ID" \
  --account-name "$STORAGE_ACCOUNT" --name "$CONTAINER" \
  --auth-mode login --public-access off
```

This example assumes Storage is in the same subscription as the project.
For cross-subscription Storage, explicitly use its approved subscription in
every storage lookup and role command. Keep anonymous access and shared-key
access disabled; neither is needed for user-delegation SAS.

## 3. Validate code, configuration and the exact package

Use `./chartagent setup` once if dependencies are missing. It creates the Python
environment at `src/charts_agent/.venv`, installs `requirements.lock`, and builds
the web clients. A root `.venv` or global interpreter may have different packages.
Foundry's remote build instead reads
[the service's requirements.txt](../src/charts_agent/requirements.txt).

```bash
./chartagent test -q
src/charts_agent/.venv/bin/ruff check src gateway tests
npm --prefix web test
npm --prefix web run build
azd ai agent doctor --environment "$PROD_ENV"
```

On Windows use `./chartagent.ps1 test` and the executables under
`src/charts_agent/.venv/Scripts/`. For a narrowly scoped redeployment, run the
affected tests; for example, the font correction used
`./chartagent test tests/test_charts.py -q`.

Before a first deployment there cannot yet be an active agent; distinguish that
expected doctor result from authentication, missing configuration or project
access failures. Resolve those failures before deploying.

[azure.yaml](../azure.yaml) uses `codeConfiguration`, Python 3.13,
`remote_build`, and `main.py`: **no local Docker build or ACR is required**.
Do not switch to container deployment to work around a code-deploy error.
Inspect the actual source ZIP, not just `.gitignore`:

```bash
PACKAGE_DIR="$(mktemp -d)"
azd package charts-agent --environment "$PROD_ENV" \
  --output-path "$PACKAGE_DIR/charts-agent.zip" --no-prompt
unzip -l "$PACKAGE_DIR/charts-agent.zip"
```

The [service-root .agentignore](../src/charts_agent/.agentignore) must be present.
Check that the ZIP contains the runtime Python files and `requirements.txt`,
but **no** `.env*`, virtual environments, artifact images, caches, logs,
`.agent_configs/`, `.foundry/`, `eval.yaml` or `TEAMS_APP_SETUP.md`.
Stop and correct the ignore rules if any appear, then inspect a new package.
`azd deploy` packages the source again, so keep validated source/config unchanged.
Remove the temporary ZIP and its now-empty directory when finished.

For an existing project with no infrastructure changes, skip provisioning.
If a preview is needed, `azd provision --preview --environment "$PROD_ENV"
--no-prompt` should show the existing project and nothing to provision. Stop if
it proposes unexpected resources. Do not use `azd up` or `azd provision` as a
routine fix for missing Blob permissions.

## 4. Deploy, then resolve the runtime identity

Only after approval and successful validation:

```bash
azd deploy charts-agent --environment "$PROD_ENV" --no-prompt
azd ai agent show charts-agent --environment "$PROD_ENV" --output json
```

Wait for `status: active`. Record the actual `version`, `agent_endpoints`, and
**`instance_identity.principal_id`** returned by `show`. Every successful deploy
creates an immutable version; never assume the next version number.

On the **first** deployment, Foundry creates the dedicated runtime identity.
Therefore, its storage permissions can only be assigned **after deployment and
before the first chart request**, not as a pre-deployment step. A successful
deployment or health check does not prove the chart tool can access Blob Storage.

Do not substitute the project managed identity, Foundry account identity,
blueprint principal, application/client ID, or the deploying user's identity.
Recheck the runtime principal on every redeployment rather than assuming it is
unchanged. Do not create a separate prompt agent with this hosted agent's name.

## 5. Assign and verify least-privilege Blob roles

Read the principal from step 4 and obtain the existing Storage account's ARM ID:

```bash
RUNTIME_PRINCIPAL_ID="PASTE-instance_identity.principal_id-FROM-show"
STORAGE_ID="$(az storage account show --subscription "$SUBSCRIPTION_ID" \
  --resource-group "$STORAGE_RESOURCE_GROUP" --name "$STORAGE_ACCOUNT" \
  --query id --output tsv)"
CONTAINER_SCOPE="$STORAGE_ID/blobServices/default/containers/$CONTAINER"

az role assignment list --subscription "$SUBSCRIPTION_ID" \
  --assignee "$RUNTIME_PRINCIPAL_ID" --scope "$CONTAINER_SCOPE" \
  --include-inherited --query '[].{role:roleDefinitionName,scope:scope}' --output table
```

Only an authorized administrator should create **missing** assignments:

```bash
az role assignment create --subscription "$SUBSCRIPTION_ID" \
  --assignee-object-id "$RUNTIME_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Contributor" --scope "$CONTAINER_SCOPE"
az role assignment create --subscription "$SUBSCRIPTION_ID" \
  --assignee-object-id "$RUNTIME_PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Delegator" --scope "$STORAGE_ID"
```

| Role | Scope | Why |
|---|---|---|
| Storage Blob Data Contributor | Chart container only | Write/read chart PNG and SVG blobs |
| Storage Blob Delegator | Storage account | Request a user delegation key, an account-level operation |

Container-scoped Data Contributor alone cannot request the delegation key.
Do not broaden Data Contributor to the whole account to fix this. Re-run the
role listing and check both the principal and exact scopes.

New assignments can take time to propagate. The first production request in this
sample returned `AuthorizationPermissionMismatch`; waiting and retrying after
verifying the roles fixed it without adding permissions. If failures persist,
inspect identity, role scopes and network access rather than repeatedly granting
roles or enabling anonymous/shared-key access.

## 6. Verify real production output, including text

```bash
azd ai agent doctor --environment "$PROD_ENV"
```

Start a **new session and conversation**, explicitly targeting the version from
step 4. Otherwise the CLI can reuse a session backed by the previous version.
The invocation consumes model capacity. Its output contains signed URLs, so
capture it privately instead of sending it to shared logs:

```bash
AGENT_VERSION="PASTE-version-FROM-show"
umask 077
RAW_RESPONSE="$(mktemp)"
azd ai agent invoke charts-agent \
  "Create a bar chart titled Revenue by Region showing total 2025 revenue for all regions." \
  --environment "$PROD_ENV" --version "$AGENT_VERSION" --protocol responses \
  --new-session --new-conversation --timeout 300 --output raw > "$RAW_RESPONSE"
```

`invoke` supports `--output raw`, not `--output json`. Raw output can include
headers and SSE `data:` events: parse the `response.completed` response and its
`output[].content[]` text rather than treating the whole file as one JSON object.
Inspect locally; never paste the raw response or SAS query strings into shared
transcripts, documentation or source control.

Deployment is verified only when all of these checks pass:

- The response completes **with actual PNG Markdown and an SVG download link**,
  not merely a successful HTTP status or a prose apology after a tool failure.
  Default Responses must not return an interactive chart bundle or native card.
- Both signed URLs return HTTP 200 with `image/png` and `image/svg+xml`.
  Download the actual PNG and **view it**: title, numeric ticks, axis titles,
  category labels and legend text must be visible. MIME type/dimensions and a
  good-looking SVG alone missed the original font bug.
- For this prompt, the PNG is 960 x 540. The known synthetic 2025 regional totals
  are Americas 9,230,722.56, Asia Pacific 8,942,437.46 and Europe 9,044,376.91;
  total revenue is 27,217,536.93.
- The SVG's text uses generic `sans-serif`. Do not reintroduce a desktop-only
  font such as Arial: minimal hosted runtimes lack it, whereas `vl-convert`
  bundles a sans-serif fallback. A browser may hide the defect by substituting
  its own font for SVG display.
- URLs use HTTPS and read-only delegation SAS (`sp=r`, `spr=https`); `skoid`
  matches the actual runtime principal. Do not persist the complete query.
  Removing the query must **not** grant anonymous image access. This account
  returned HTTP 409 `PublicAccessNotPermitted`; 403/404 are also possible.

Retain only redacted verification facts (time, version, principal, HTTP/MIME
checks, dimensions and visual outcome) and any needed non-sensitive preview.
Delete the raw response and temporary package:

```bash
rm -- "$RAW_RESPONSE" "$PACKAGE_DIR/charts-agent.zip"
rmdir -- "$PACKAGE_DIR"
```

For a failing invocation, inspect that session's logs privately:

```bash
azd ai agent monitor charts-agent --environment "$PROD_ENV" --tail 100
```

It resolves the last invocation's session; use `--session-id` for another one.
Redact credentials and signed URLs before sharing log excerpts.

## 7. Separate acceptance checks and operational limits

- **Activity:** deployment creates an Azure Bot registration and generates
  `src/charts_agent/TEAMS_APP_SETUP.md`. App packaging, channel configuration,
  tenant approval and sideloading are separate. Responses smoke tests do not
  prove Teams acceptance. Test native charts and PNG fallback through the
  Activity client; `azd ai agent invoke` does not support Activity.
- **Web/MCP:** see [gateway hosting guidance](../gateway/README.md#hosting-distinction).
  Foundry does not expose the gateway's `/mcp` or web routes. Configure its
  independent hosting, HTTPS, Entra authentication and browser sign-in/BFF;
  never disable gateway auth to make the demo chat work publicly.
- **Evaluations:** preserve the existing generated dataset/evaluator/config
  when redeploying. Generation is not execution or a quality guarantee.
  The cached artifacts refer to the original project; do not assume they are
  registered in another project. Review synthetic `candidate_response` values
  and obtain approval before generation, refresh or a paid evaluation run.
  See [the optional evaluation suite](../README.md#optional-evaluation-suite).
- **Storage:** SAS defaults to 60 minutes; expired messages need a refresh
  strategy. Blobs are not automatically deleted. Define lifecycle/retention
  rules deliberately. Existing defective images are not overwritten by a new
  deployment: request a fresh chart and use its new URL.
- **Production environment vs. production-ready product:** data remains
  synthetic and conversation history is not promised durable across container
  replacement. Real business data needs tenant/user authorization at the query
  layer, durable scoped history, audit/retention policies, quotas and rate
  limits. Caller presentation metadata is not an authorization mechanism.

## Troubleshooting lessons from the production deployment

| Symptom | Check / resolution |
|---|---|
| Hosted startup rejects local artifacts/dev mode | Set production azd values and verify `environmentVariables`; do not package local `.env`. |
| Blob upload or delegation key fails | Verify `instance_identity.principal_id`, both roles/scopes, propagation and network access. |
| PNG lacks text but SVG looks correct | Use the shared `sans-serif` configuration; deploy and visually verify a new PNG. Do not change Blob authentication or install Arial as a workaround. |
| Old behavior after a successful redeploy | Verify the actual version; use both `--new-session` and `--new-conversation`, with explicit `--version`. |
| Inspector says "Remote image blocked" | Use HTTPS Blob URLs and approve **Load Remote Images**. Local HTTP/8088 images are ineligible; Blob storage does not bypass the privacy prompt. |
| Browser rejects a copied SAS URL | Copy just one complete URL, not `PNG_URL)` followed by the SVG Markdown link. Preserve percent encoding, especially `%2B` and `%3D`, and check expiry in UTC. The reported malformed `sv` error was caused by concatenated links, not the SDK's service version. |
| CLI reports success but there is no chart | Inspect the tool/session error. `response.completed` can contain an apology; require both actual image links and downloaded-image checks. |
| `vl_convert` is missing locally | Use `./chartagent test` / the service-local virtual environment, not an unrelated root `.venv` or global Python. |

## Verified baseline

On **2026-09-19**, `charts-agent` version **2** was deployed in the `charts-prod`
environment using the existing `gpt-5.4-mini` deployment. Doctor passed all 11
applicable checks. The font fix passed 58 targeted chart tests and a fontless
Linux rendering check; a fresh production PNG visibly included its title,
axes and legend, and all 21 SVG text elements used `sans-serif`.

This is a dated verification record, not a promise about the currently active
version. Discover current state with `show`; do not hard-code version 2, a prior
principal ID or a historical image URL into deployment scripts. No evaluation
run or tenant-side Teams installation is claimed by that record.

Official references:

- [Deploy a hosted agent from source code](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-code)
- [Deploy a hosted agent](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent)
- [Create a user delegation SAS](https://learn.microsoft.com/azure/storage/blobs/storage-blob-user-delegation-sas-create-python)
