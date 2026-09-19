# Copilot declarative agent + MCP plugin

This is **not a Teams tab**. `manifest.json` registers a Copilot declarative
agent; `ai-plugin.json` uses the v2.4 `RemoteMCPServer` runtime with pinned tools.
The MCP App resource is returned by the gateway, not bundled into a tab.
An MCP Apps-capable Copilot host is required for the interactive surface.
The gateway must be externally hosted as its **own HTTPS service** for remote
Copilot access. Foundry does not automatically expose its MCP, web-chat, or
OAuth-discovery routes. `GATEWAY_MODE=foundry` selects the upstream hosted agent;
it does not deploy the gateway.

For the complete production walkthrough, see
[M365 Copilot MCP App guide](../docs/m365-copilot-mcp-app.md):
gateway-only App Service deployment, retrieving the real MCP URL, managed
identity access to the existing Foundry agent, Entra OAuth, widget CORS,
Toolkit registration, sideloading and chart/drill-down acceptance checks.
The existing Activity package is a different integration; do not replace its
manifest or reuse its app identity for this declarative agent.

## Configure before packaging

No resources are created by these files. Supply real values for:

- `COPILOT_APP_ID`: the registered Microsoft 365 application UUID.
- `GATEWAY_PUBLIC_ORIGIN`: the HTTPS origin of your **authenticated gateway**,
  not the Foundry project or hosted agent endpoint.
- `COPILOT_OAUTH_REFERENCE_ID`: a configured OAuth Plugin Vault registration.
  Configure its Entra tenant, client, delegated scopes, consent, and redirect
  URI in the registration. Never put a client secret in a manifest.
- `PUBLISHER_NAME`, `WEBSITE_URL`, `PRIVACY_URL`, and `TERMS_URL`.

Align that registration with the gateway's `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE`,
and `ENTRA_REQUIRED_SCOPES`. Production uses actual Entra tokens; the sample
does not manufacture an OAuth authorization server or accept arbitrary tokens.
Do not expose `CHARTS_DEV_MODE=true` through a public tunnel.
If your Copilot host requires widget-origin CORS, add its exact generated
`https://<hash>.widget-renderer.usercontent.microsoft.com` origin to
`GATEWAY_MCP_ORIGINS`. Configure the documented Copilot/VS Code redirect URIs
in the OAuth registration. No wildcard origins are accepted.
Use v2 API access tokens and configure `ENTRA_AUDIENCE` with the gateway API
client ID. Request the fully qualified API scope in OAuth, while
`ENTRA_REQUIRED_SCOPES` contains the short `Charts.Read` token claim.
Use static OAuth registration for Entra; it does not implement MCP dynamic
client registration. Follow the current Microsoft guidance for the token-store
registration's `AnyApp` restriction and tenant restriction, as detailed in
the production guide.

Refresh pinned tool schemas after changing Python tool signatures:

```bash
src/charts_agent/.venv/bin/python -m gateway.export_tools
node appPackage/package.mjs
```

The second command resolves templates into `appPackage/build/` and generates
basic 192×192 color / 32×32 transparent outline PNG icons. Before submission,
replace the generated `build/color.png` and `build/outline.png` with the
[pixel-validated sideload artwork](../m365sideloadmanifest/README.md#icons)
and set the package manifest's `accentColor` to `#2735A6`. The package builder
does not run the sideload builder's icon-compliance checks. Package the
**contents** of `build/`, validate using Microsoft 365 Agents Toolkit, and
upload through your tenant's supported app workflow. Templates containing
`${{...}}` are deliberately not upload-ready; configuration/consent remain
administrator actions. Nothing is provisioned or uploaded by the script.

References:

- [MCP Apps in Copilot](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-mcp-apps)
- [Plugin manifest v2.4](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-manifest-2.4)
- [Pinned tools and dynamic discovery](https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-dynamic-tool-discovery)
