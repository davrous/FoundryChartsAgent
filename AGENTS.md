# Coding Agent Instructions

This project is a **Microsoft Foundry hosted agent** — a containerized AI agent that runs in [Foundry Agent Service](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents). The platform handles containerization, hosting, security, scaling, and observability so you can focus on agent logic.

## Key files

- `src/charts_agent/` — Python hosted agent, mock API, shared rendering and protocol adapters
- `src/charts_agent/Dockerfile` — optional container definition
- `gateway/` — separate web/MCP gateway to the hosted agent
- `web/` — shared interactive SVG renderer, web chat and MCP Apps widget
- `tests/` — deterministic tests (no live model required)

## Development workflow

The **Azure Developer CLI (`azd`)** manages the full lifecycle:

```bash
./chartagent setup
./chartagent dev
./chartagent web
./chartagent playground
./chartagent test
```

Use ports 8088 (agent) and 8190 (gateway) by default.
Read README.md before a deployment: local artifacts and anonymous gateway
mode are development-only. Never provision, deploy, or change Azure resources
without the user's approval. Do not add a Teams tab.

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