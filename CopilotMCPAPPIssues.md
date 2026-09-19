# Foundry to Microsoft 365 Copilot: MCP App blockers and experience gaps

**Engineering handoff - 2026-09-19**

Scope: a Python Foundry hosted agent that queries synthetic sales data and
returns static charts, Adaptive Cards or interactive SVG. The objective is
to reuse that agent inside Microsoft 365 Copilot, without a Teams tab.
See the [integration walkthrough](docs/m365-copilot-mcp-app.md) and
[gateway provisioning runbook](infra/gateway/README.md).

## Executive summary

The charting agent works in Foundry, and the interactive MCP App works locally
in Claude Desktop. **The production Copilot MCP App path is not yet deployed
or verified.** Deployment stopped at Entra application registration, before
allocating paid gateway hosting.

| Question / issue | Conclusion and evidence level |
|---|---|
| Do we need app registrations? | **Yes for the selected Entra-protected design:** an API registration and a separate OAuth client, plus a Copilot token-store configuration. Two registrations are our separation-of-roles design, not a universal MCP requirement. |
| Is lack of tenant admin approval the blocker? | **More precisely: tenant governance blocked creation with `ServiceTreeValueMissing`.** The developer has no approved Service Tree ID. We need the internal application-onboarding/service-owner process; generic admin consent alone is not proven sufficient. |
| Must we host another gateway? | **Yes for this implementation.** The existing Foundry deployment exposes Responses/Activity, not this sample's `/mcp` server and HTML resource. This is an extra deployable service, not necessarily an inherent scaling bottleneck. |
| Are users and conversations isolated automatically? | **No.** Gateway sign-in is implemented; user-specific data authorization and ownership checks on supplied response IDs are not. This is acceptable only within the stated synthetic-data demonstration scope. |
| Are SAS images broken in Copilot? | **Reported in the user's tests:** they display in Teams but not Copilot. The exact cause is unconfirmed; no evidence establishes a blanket Copilot ban on SAS URLs. |
| Are Adaptive Card charts unsupported in Copilot? | **Cannot confirm that blanket explanation.** The current official `Chart.Line` and `Chart.VerticalBar` pages explicitly say **"Works in: Teams, Copilot."** The observed failure needs investigation of the particular custom-engine/Activity surface, client rollout or payload. |
| Does Teams lack multiple conversations? | **Qualify this as a personal-agent-chat UX gap observed in this sample.** Teams supports multiple chats and channel threads. That is different from independently managed analysis sessions within one personal bot chat. |

Evidence labels used below:

- **Observed:** actual deployment result, source inspection or the developer's
  reported client behavior. Client reports were not independently reproduced
  during this documentation pass.
- **Documented:** current first-party documentation, linked below.
- **Proposed / hypothesis:** an implementation suggestion or an explanation
  still requiring a controlled test; not an established platform defect.

## 1. Keep the two integration paths separate

```text
Existing custom-engine / Activity path:
Teams or Copilot -> Foundry Activity endpoint -> hosted charts-agent
                                             -> Adaptive Card Chart.* or PNG Image

Proposed interactive MCP App path:
Copilot declarative wrapper -> authenticated HTTPS MCP gateway
                            -> Foundry Responses + agent_reference
                            <- chart data/specification
Copilot widget             <- MCP HTML resource + tool result
                            -> SVG hover/filtering; tool-based drill-down
```

The image and native-chart failures reported here concern the existing
Teams/Copilot agent experience, **not a failed production test of the new MCP
App path**. MCP Apps use a separate widget renderer and protocol contract;
they do not turn an Activity Adaptive Card into an HTML application.

The existing `charts-agent:2` already accepts interactive output metadata.
No hosted-agent redeployment is expected to be needed to connect the new MCP
gateway to that agent.
Future changes to authorization or Activity state could require agent changes,
but that is separate from the current deployment blocker.

### Actual deployment state

- Prepared and validated the independent Bicep, gateway-only ZIP, Entra
  provisioning helper and M365 wrapper lifecycle. Local gateway/helper tests:
  **39 passed**; Bicep compilation and compiled-setting checks passed.
- Registered `Microsoft.Quota` and `Microsoft.Web` after deployment approval.
  North Central US B1 quota was verified: **10 limit, 0 used**.
- Entra creation then remained blocked by `ServiceTreeValueMissing`.
- **No new Entra apps, App Service plan/web app, Key Vault or Application
  Insights component were created. No production MCP URL exists yet.**
- Existing Foundry agent, model, storage and Activity integration are unchanged.
- The proposed one-instance pilot was approximately **$16/month incremental**,
  excluding existing Foundry/model/storage, M365 licensing and other usage.
  This is not a high-availability design or a cost estimate for scaled usage.

## 2. Blocker: organizational app-registration and OAuth onboarding

### What actually failed

The first API application creation through Microsoft Graph returned:

```text
HTTP 400
ServiceTreeValueMissing
Request ID: 7c18c927-a09c-4315-9e73-a5c18be7da26
Date: 2026-09-19
```

An exact-name lookup afterward confirmed that the rejected API registration
had not been created. The developer does not have a Service Tree service ID.
We stopped instead of inventing one, borrowing an unrelated registration or
turning off authentication.

**Confirmed:** this attempt cannot self-service the required app registration
in the Microsoft corporate tenant as currently configured.
**Not established:** that a generic administrator approval/consent click is
the only missing prerequisite, or that it would satisfy the Service Tree policy.
The error points to required service ownership/governance metadata.
Microsoft Graph documents `serviceManagementReference`, but its public
documentation does not define this tenant's internal Service Tree approval
process. [1][2]

Keep three authorization decisions distinct:

1. **Application creation/configuration:** tenant policy and application
   ownership requirements. Azure subscription Owner does not settle this.
2. **Delegated permission consent:** whether users may consent to `Charts.Read`
   or an administrator must grant consent. This stage was not reached.
3. **M365 distribution:** Copilot entitlement, custom app upload, token-store
   registration and organizational publishing policies. Also not yet tested.

### Practical unblock options

- **Preferred:** ask the Foundry engineering group's service owner/tenant
  onboarding team to sponsor approved API/OAuth registrations, with the right
  Service Tree association and least-privilege delegated scope.
- Use an existing **approved integration application only with its owner's
  permission**, after reviewing audience, scopes, redirects and blast radius.
  Do not repurpose the hosted runtime identity or Activity bot registration.
- Use an authorized development/demo tenant with the required Copilot
  licensing and upload rights. This is a different target requiring approval
  and identity/trust review; it is not a transparent way around corporate policy.
- A platform-managed Foundry-to-Copilot registration flow would remove much
  of this sample-by-sample onboarding work. It must still honor tenant policy.

Our chosen static OAuth flow also requires an Enterprise token-store auth
configuration, redirect URIs, consent and credential lifecycle management.
Entra does not support the MCP dynamic client registration path documented
for other providers. Microsoft currently warns that restricting the MCP OAuth
configuration to a specific Teams app can cause runtime 404s; its guidance is
**Any Teams app**, with tenant restriction and API validation still enforced.
These are documented integration hazards, not errors reproduced in this
deployment. [3]

The confidential-client secret/Key Vault is a choice of this implementation,
not an intrinsic requirement of every MCP App. Approved SSO or supported
public-client PKCE designs warrant investigation, but do not remove the need
for compliant application identity and tenant onboarding. No alternative
authentication design has been implemented or verified here.

## 3. Gateway scaling, user isolation and conversation routing

### What the sample currently does

| Area | Current implementation | Production implication |
|---|---|---|
| MCP transport | [FastMCP](gateway/main.py) uses `stateless_http=True`, JSON responses and a bundled HTML resource. | Requests need not be tied to one worker's MCP session, but stateless transport is not durable conversation management. |
| User authentication | [Middleware](gateway/auth.py) validates JWT signature, issuer, tenant, audience, expiry and delegated scope. | Proves who may enter the API; does not prove ownership of a conversation, chart or business-data row. |
| Foundry identity | [Agent client](gateway/client.py) invokes Foundry using the gateway's own managed identity. | Foundry sees the application identity, not delegated end-user authorization. No user-token forwarding or OBO is implemented. |
| Conversation continuation | `get_chart` and `/api/chat` accept `previous_response_id` and forward it upstream. | There is no gateway-side binding of that ID to the authenticated user. A response ID must not be treated as an authorization credential. |
| Drill-down | `drill_chart` accepts a structured chart request and makes a standalone agent call; its result has `response_id: null`. | Clicking a drill button does not advance the text conversation's response chain. |
| Local filters | The [widget](web/src/mcp.js) filters already-returned aggregate data and calls the drill tool through the host. | Efficient, but hidden data is still present in the browser. Client filtering is not data authorization. |
| Activity memory | [Activity bridge](src/charts_agent/activity_bridge.py) uses `MemoryStorage`, keeps up to 12 history entries and implements `/clear`. | Process-local sample state is not durable across restarts or guaranteed consistent across replicas. Scaling the MCP gateway does not fix this separate path. |

The gateway does **not** currently put all users into one global conversation.
It forwards explicit response chains. The missing guarantee is ownership and
lifecycle enforcement around those chains, not evidence of an observed
cross-user disclosure. Do not adapt this sample to private data unchanged.

### Proposed production design

1. **Derive the principal from verified tokens.** Use a stable tenant/user key,
   such as `(tid, oid)` when the selected token contract supplies `oid`, or an
   explicitly documented issuer/audience/subject mapping. Never accept user
   identity from prompt text or model-generated tool arguments.
2. **Issue an application analysis-session handle.** Bind it server-side to
   the authenticated owner, agent/version, data authorization and expiry.
   Map it to the upstream Foundry conversation/response chain. Check ownership
   on every chat, drill, export and history operation.
3. **Do not assume Copilot supplies a trusted conversation ID.** Confirm the
   host contract first. An MCP transport session ID, widget instance, Copilot
   chat and Foundry response ID are different things. The published capability
   table marks `openai/widgetSessionId` and `openai/subject` unsupported. If no
   trusted host conversation identifier is available, use the application
   handle plus explicit new/switch-session actions; reject ambiguous routing
   rather than defaulting to "the user's latest conversation." [4]
4. **Store mappings durably when continuity is required.** Use a shared
   transactional store with TTL/deletion policy and optimistic concurrency
   or per-conversation serialization. Independently stateless drill requests
   may not need conversation persistence, but still need authorization.
   Handle retries idempotently and prevent simultaneous turns from silently
   overwriting one another.
5. **Authorize data before rendering.** Enforce row/tenant entitlements in
   the trusted query layer and scope caches by principal/entitlement set,
   query, dataset version and freshness. The selected-region UI is not a
   security boundary. Delegated/OBO access is an alternative only where the
   actual downstream API supports it; otherwise explicitly enforce user
   authorization while using the service identity.
6. **Separate private and collaborative sessions.** A Teams channel/group
   analysis needs an explicit membership/ACL model, not a private user's key
   accidentally reused for everyone. Define who may reset, share or export it.
7. **Choose how widget state affects later prompts.** For example, after a
   drill, store the authorized query as session state and send a concise
   model-context update where supported. Otherwise clearly label drilling as
   a widget-local branch. Do not assume "now compare that" sees UI-only filters.
8. **Treat artifacts as protected resources.** Bind chart/export identifiers
   to the session owner and recheck authorization before issuing fresh links.
   A SAS URL is a transferable bearer capability until expiry, not an
   end-user-bound access check. Keep it out of telemetry and shared caches.

These are proposed changes, not capabilities already delivered by this sample.

### Scaling ideas and tradeoffs

- Keep the gateway thin: no duplicate chart-generation engine, no process-local
  ownership registry, pooled async upstream clients and bounded request sizes.
  The current design already reuses Foundry rendering and a bundled widget.
- Scale stateless gateway replicas behind a load balancer; keep required
  session mappings shared. Sticky sessions are not a replacement for durable
  state or authorization. Move beyond the single B1 instance when availability
  or measured load requires it.
- Add per-user/per-tenant concurrency and rate budgets, backpressure, bounded
  retries with jitter and explicit handling of Foundry 429/timeouts.
  Gateway capacity cannot increase model quota or hosted-agent capacity.
- Measure end-to-end latency, active upstream calls, payload sizes, memory,
  429/5xx rates and cost per successful chart/drill. Load-test concurrent
  independent sessions, retries, instance restart and scale-out.
- Keep mouse-over/filtering local; only actual drill queries should incur
  agent work. Consider smaller tool results: the current bundle includes
  multiple chart representations. UI-only data can use MCP `_meta` where
  appropriate, but that is context-efficiency design, not access control.
- Correlate host request, gateway call and Foundry response IDs without logging
  credentials, SAS queries or private chart rows. Expose failures in the UI.
  A healthy `/health` endpoint does not prove either auth hop works.

**Engineering request:** a managed Foundry MCP Apps publishing endpoint, with
host-compatible auth, resource serving, identity propagation, conversation
ownership and observability, would reduce custom middleware and operational
burden. An API gateway can centralize throttling/auth but does not itself
implement the MCP Apps resource or conversation-ownership contract.

### Minimum multi-user acceptance tests

| Test | Required outcome before private-data use |
|---|---|
| Two users, two simultaneous analyses per user | Four independent histories; resetting one does not alter any other. |
| Foreign, expired or fabricated session/response/chart ID | Rejected without returning another user's history, chart or artifact. |
| Concurrent turns, retry and an old widget clicked after reset | Defined ordering/idempotency; stale work cannot overwrite the active analysis. |
| Restart and requests alternating between gateway replicas | Authorized continuity survives; no reliance on one process's memory. |
| Text follow-up after a widget drill/filter | Uses the explicitly chosen session/branch semantics, not an accidental shared "latest chart." |
| Foundry throttling or unavailable auth/upstream | Bounded work, actionable failure and no false success; other users remain responsive. |

## 4. Client issues to track

### C-01: SAS-backed chart images appear in Teams, not Copilot

**Status:** developer-observed on the existing agent path; root cause open.
**Impact:** static charts and unsupported-native-chart fallbacks disappear.

- The hosted agent generates PNG and SVG in private Blob storage, with
  HTTPS/read-only user-delegation SAS. The code's default TTL is 60 minutes.
  Correct PNG text rendering was separately verified after the font fix.
- The [Activity manifest](m365sideloadmanifest/manifest.json) at the initial
  investigation (version 1.0.3) had `validDomains: []`. Version **1.0.4** now
  includes only `msdavrous.blob.core.windows.net` for a controlled retest;
  installation and the Copilot rendering outcome remain pending. See the
  [retest instructions](m365sideloadmanifest/README.md#retest-copilot-chart-images-with-version-104).
  Copilot's API-plugin/declarative-agent documentation
  explicitly requires image domains in `validDomains`. This is a concrete
  configuration lead, **not proof of the rule's applicability or root cause
  on this custom-engine Activity path**. Verify the actually installed package
  and test the exact storage hostname, without scheme/path/SAS, in its allowlist. [5]
- Other hypotheses: expired SAS on replay, URL encoding/rewrite, fetch-proxy
  behavior, content-type/format restrictions, or surface-specific policy.
  Do not default to a CORS explanation without observing the failing fetch.
- Copilot's documented custom-engine file-upload/download limitations are
  relevant context, but are not proof that a PNG `Image.url` is unsupported. [6]

**Minimal repro / acceptance:**

1. Generate a fresh, small PNG and use the exact same SAS URL and minimal
   Adaptive Card `Image` payload in Teams and standalone Copilot agent chat.
   Record client/version, entry point, package version and UTC times.
2. Confirm direct GET returns `200`, `image/png` and valid bytes during the test.
   Test PNG separately from SVG and image cards separately from Markdown links.
3. Capture sanitized renderer/network diagnostics and Blob request outcomes.
   Test an approved non-sensitive control image and the exact-domain allowlist
   change. Do not make the production container public.
4. Repeat after expiry and after reopening chat, distinguishing a fresh-image
   failure from expected expired-link behavior.

**Ask:** document Copilot's image-fetch contract for private-Blob SAS,
allowlisting, caching/replay and formats; surface a useful diagnostic instead
of a blank region. Do not solve this by issuing effectively permanent SAS.

### C-02: Native Adaptive Card charts render in Teams but not Copilot

**Status:** developer-observed; potential host/integration parity defect or
documentation/rollout mismatch, **not confirmed universal non-support**.

During this review, the live official [Chart.Line][7] and
[Chart.VerticalBar][8] documentation both displayed **"Works in" badges for
Teams and Copilot**. The [Teams chart guide][9] also documents these controls.
Therefore, "Copilot does not support the new charts yet" is too broad.

Conversely, Copilot's custom-engine known-issues page lists unsupported
"nonstandard elements" without a chart-by-chart, entry-point-specific support
matrix. A broad documentation badge does not prove support for this exact
Foundry Activity/custom-engine client configuration. [6]

The [renderer](src/charts_agent/rendering.py) emits card version 1.5 and an
element-level PNG fallback for each native chart. Version 1.5 alone does not
establish host-extension support. **If the chart is rejected and C-01 also
blocks its image fallback, both failures can look like one empty chart.**

**Minimal repro / acceptance:**

- Send a two-category `Chart.VerticalBar` with a plain-text element fallback
  and no remote image, actions or complex layout. Test the identical payload
  in Teams and the exact Copilot custom-engine entry point.
- Repeat for `Chart.Line`; then reintroduce the image fallback and drill
  actions separately. Capture delivered JSON, not only the generated JSON.
- Record whether the native element, fallback or neither renders, including
  desktop/web build and rollout details. Test direct agent chat versus
  `@mention` separately where available.

**Ask:** reconcile documentation with actual custom-engine host behavior,
publish a per-surface capability matrix and provide deterministic fallback
and unsupported-element diagnostics. An MCP widget is a separate rendering
option, not evidence that this Adaptive Card defect has been fixed.

### C-03: No independent analysis-thread UX in the tested Teams personal chat

**Status:** reported UX limitation in this sample; not "Teams has no threads."

The developer can start separate conversations in Copilot, but the tested
Teams personal bot chat keeps earlier analysis context unless reset. Teams
documentation distinguishes continuous chat from threaded channel discussions;
Teams can also host distinct group/channel conversations. These capabilities
should not be conflated with a personal agent's new/switch-analysis workflow. [10]

Our Activity bridge intentionally retains recent history and exposes `/clear`.
That clears this conversation's sample chart history; it does not create a
new Teams chat, delete visible messages or establish durable multi-session
management. Merely changing topic does not automatically reset history.

**Ideas:** add explicit New analysis / Switch analysis actions or commands,
with authorized logical session IDs and a visible active-session label,
without a Teams tab. Define channel-thread versus private-session behavior.
For scale, replace Activity `MemoryStorage` with a durable store.

**Ask:** provide first-class personal-agent conversation branching and
document stable conversation/thread identifiers and reset semantics across
Teams, Copilot, Activity and MCP. Copilot's own new-chat UI is not proof that
this MCP gateway automatically routes each chat to an isolated Foundry chain.

### C-04: MCP host capability parity remains an acceptance risk

**Status:** documented limitations; production Copilot widget not yet tested.

Copilot documents `app.ontoolresult`, `app.callServerTool` and resize support,
which fit our chart/drill flow. Its capability table marks `app.ontoolcancelled`,
`app.onteardown` and some host context/session metadata unsupported. The
[current widget](web/src/mcp.js) registers cancellation/teardown handlers;
do not assume those callbacks fire in Copilot just because they work elsewhere.
Local SVG download behavior also needs host-specific verification: Copilot
documents external-link handling through `app.openLink`, rather than ordinary
external anchors, and download-related Apps SDK APIs are not supported. [4][11]

**Ask:** test inline chart, hover, filter, drill, error, navigation, reopening,
cancellation and export separately. Report each unsupported feature honestly;
Claude success is valuable evidence, but not Copilot acceptance.

## 5. Suggested engineering priorities

| Priority | Work item | Suggested owning area |
|---|---|---|
| P0 - blocks this deployment | Approved Service Tree/app-registration onboarding for internal samples | Internal service owner / identity governance |
| P1 - blocks reliable visual output | Reproduce C-01; establish SAS-image/allowlist/replay contract | Copilot rendering + identity/content policy |
| P1 - parity/documentation | Reproduce C-02 against minimal native charts and explicit fallbacks | Copilot + Adaptive Cards + Foundry Activity integration |
| P1 - before private-data adoption | Enforce user/data/session ownership and durable state; test concurrent users and restarts | Sample/application team, with Foundry identity/session guidance |
| P2 - reduces integration burden | Managed MCP Apps exposure for hosted agents and coherent auth/session contract | Foundry + Copilot extensibility |
| P2 - user experience | Independent analysis-session lifecycle across personal Teams chat and Copilot | Teams + Copilot + application UX |

Priorities are proposed for triage, not official product severity assignments.
No product bug IDs have been filed by this task.

For each client issue, attach: sanitized payload, exact agent type/protocol,
host/build, direct-chat versus mention entry, manifest/package version,
expected/actual screenshots, UTC timestamps and correlation IDs. Keep live
SAS strings, OAuth secrets, tokens and customer data out of this report.
Re-test with synthetic data. Keep deployment paused until identity onboarding
has an approved path; do not weaken authentication as a workaround.

## References and verification boundaries

Documentation reviewed on **2026-09-19**; support and rollout can change.
The Adaptive Cards "Works in" badges were read in the live documentation UI,
not inferred from search snippets. Public documentation does not establish
Microsoft's internal Service Tree approval procedure.

[1]: https://learn.microsoft.com/entra/fundamentals/users-default-permissions
[2]: https://learn.microsoft.com/graph/api/resources/application?view=graph-rest-1.0#properties
[3]: https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-authentication-oauth
[4]: https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-mcp-apps#supported-mcp-apps-capabilities-in-copilot
[5]: https://learn.microsoft.com/microsoft-365/copilot/extensibility/api-plugin-adaptive-cards#add-domains-to-your-app-manifest
[6]: https://learn.microsoft.com/microsoft-365/copilot/extensibility/known-issues#custom-engine-agents
[7]: https://adaptivecards.microsoft.com/?topic=Chart.Line
[8]: https://adaptivecards.microsoft.com/?topic=Chart.VerticalBar
[9]: https://learn.microsoft.com/microsoftteams/platform/task-modules-and-cards/cards/charts-in-adaptive-cards
[10]: https://learn.microsoft.com/microsoftteams/navigate-teams
[11]: https://learn.microsoft.com/microsoft-365/copilot/extensibility/plugin-mcp-apps-troubleshooting
