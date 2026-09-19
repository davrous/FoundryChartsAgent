# Coding Agent Instructions

This project is a **Microsoft Foundry hosted agent** — a containerized AI agent that runs in [Foundry Agent Service](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents). The platform handles containerization, hosting, security, scaling, and observability so you can focus on agent logic.

## Key files

- `README.md` — architecture, local experiences and production overview
- `docs/deployment.md` — canonical production deployment and verification runbook
- `azure.yaml` — existing-project binding, hosted protocols, forwarded runtime settings and remote code build
- `src/charts_agent/` — Python hosted agent, mock API, shared rendering and protocol adapters
- `src/charts_agent/.agentignore` — source ZIP exclusions; `.gitignore` is not a substitute
- `src/charts_agent/Dockerfile` — optional container definition
- `gateway/` — separate web/MCP gateway to the hosted agent
- `web/` — shared interactive SVG renderer, web chat and MCP Apps widget
- `tests/` — deterministic tests (no live model required)

## Development workflow

Use the repository launchers for local development and **Azure Developer CLI
(`azd`)** for hosted deployment:

```bash
./chartagent setup
./chartagent dev
./chartagent web
./chartagent playground
./chartagent test
```

Use ports 8088 (agent) and 8190 (gateway) by default.
Use `src/charts_agent/.venv` (Windows: `.venv/Scripts/` under that service), not
an unrelated root `.venv` or global Python. `./chartagent test` accepts pytest
selectors. Use the existing Ruff and web test/build commands from the README.

## Production deployment contract

Read [the deployment runbook](docs/deployment.md) before any deployment. Do not
infer deployment approval from a request to review documentation or code.
Never provision, deploy, assign roles, or run paid evaluations without approval.

1. Confirm the target and reuse approved existing resources. `azure.yaml` has
   the original author's project endpoint; it is not authorization to use that
   project. Keep the project service endpoint, `AZURE_AI_PROJECT_ENDPOINT`,
   `FOUNDRY_PROJECT_ENDPOINT` and project ARM ID consistent. Preserve the hosted
   agent architecture; do not create a prompt agent with the same name.
2. Use a separate production azd environment and pass `--environment` explicitly.
   Preserve local development `.env` and existing azd state. Only declared
   `environmentVariables` are explicitly forwarded; setting a local variable
   does not automatically configure production. In this workspace `charts-prod`
   is the existing production environment; verify current state rather than
   reinitializing it.
3. Keep `codeConfiguration` / Python 3.13 / `remote_build`. No local Docker/ACR
   build or provisioning is needed for a code-only update to this existing
   project. Use the installed Foundry/Azure deployment skills when available;
   complete their preparation/validation handoff before executing deployment.
4. Validate the affected code and inspect the exact source ZIP. Keep `.env*`,
   virtual environments, artifacts, logs, evaluation caches/configs and generated
   setup guides out of it. The remote build reads the service's `requirements.txt`;
   local tests use `requirements.lock`. Preserve their intended dependency
   alignment when changing packages.
5. On first deployment, wait for the platform to create the runtime identity,
   then read **`instance_identity.principal_id`** from `azd ai agent show`.
   Grant/verify container-scoped **Storage Blob Data Contributor** plus
   account-scoped **Storage Blob Delegator** before chart invocation. Never use
   the project/account managed identity or blueprint principal instead.
   Recheck the principal and effective scopes on redeploy. Allow RBAC
   propagation; do not broaden permissions to mask a transient failure.
6. Discover the actual active version and endpoints from `show`, run doctor,
   then invoke with `--protocol responses`, the explicit `--version`,
   `--new-session` and `--new-conversation`. Use `--output raw`, not `json`,
   if parsing invocation output; handle SSE events, not just plain JSON.
   Raw responses contain SAS credentials: capture privately, report only
   redacted facts, and remove temporary dumps after verification.
7. Require a real chart, not only HTTP success or `response.completed`. Download
   PNG/SVG and **visually inspect the PNG title, axes, ticks and legend**.
   Check MIME types, dimensions, private access and read-only HTTPS SAS.
   Preserve the shared generic `sans-serif`: Arial was absent in the hosted
   renderer and caused blank text despite a correct-looking browser SVG.
8. Old sessions and old artifact URLs can retain old behavior. Deployment
   creates a new immutable version; existing blobs are not regenerated.
   Request a fresh chart. Keep SAS encoding intact and never concatenate the
   two Markdown links when testing a URL. Inspector's **Load Remote Images**
   privacy approval is independent of Blob storage.
9. Treat Activity/M365 acceptance and web/MCP hosting as separate work. No Teams
   tab. Do not expose the demo web client or anonymous gateway publicly, or
   assume Foundry hosts the gateway's custom routes.
10. Preserve generated evaluation artifacts unless refresh is approved.
    Generation is not execution; synthetic `candidate_response` values are not
    verified outputs. Do not claim an evaluation run or tenant-side Teams
    installation based on a successful deployment.

Record version, validation results and any limitations without credentials.
The runbook's dated baseline is historical evidence, not live configuration.
An environment named production does not make the sample's synthetic data or
in-memory history suitable for real customer data without further work.

## Microsoft Foundry Skill

Install the **Microsoft Foundry Skill** for guided deployment, evaluation, and troubleshooting workflows.

Direct install (preferred, works with any coding agent):

```bash
npx skills add https://github.com/microsoft/azure-skills --skill microsoft-foundry
```

Or install the Azure Skills Plugin:

- **Copilot CLI**: `/plugin marketplace add microsoft/azure-skills` then `/plugin install azure@azure-skills`
- **Claude Code**: `/plugin install azure@claude-plugins-official`

Then ask naturally, e.g. `Use the Microsoft Foundry Skill to deploy this agent.`

## References

- [Hosted agents overview](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents)
- [Microsoft Foundry Skill](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/use-microsoft-foundry-skill)