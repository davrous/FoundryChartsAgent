# Browser MCP App smoke test (development only)

Run the normal hosted agent on **8188** and gateway on **8190**, then:

```bash
npm --prefix web run build
npm --prefix web run test:mcp-host
```

Open **http://127.0.0.1:8192**. The harness is bound to loopback, is not included
in either production build, and is never served by the normal gateway.
`MCP_SMOKE_PORT` can change the harness port; `MCP_SMOKE_GATEWAY` can select
another **loopback-only** gateway origin.

The iframe serves the unchanged `dist/mcp-app.html` with an actual HTTP CSP:

```text
default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline';
img-src data: blob:; connect-src 'none'; object-src 'none'; base-uri 'none';
form-action 'none'; frame-ancestors http://127.0.0.1:8192
```

There is **no `unsafe-eval`**, and no network/external resource permission.
Inline script/style are necessary because the production MCP App is single-file.
The parent is served on `127.0.0.1`, the widget on `localhost`: distinct browser
origins, both bound to the same loopback-only test server. The iframe uses
`sandbox="allow-scripts allow-same-origin allow-downloads"`; it retains its own
origin for same-document SVG references but cannot access the differently
originated host DOM. The host verifies both `event.source` and the exact widget
origin, and sends replies only to that origin. This also avoids WebKit's
same-document SVG fragment failures in opaque-origin frames.

The small JSON-RPC UI-host shim follows the installed ext-apps 2.0.0 generated
schemas: initialization replies include `protocolVersion`, `hostInfo`,
`hostCapabilities`, and `hostContext`, followed by one `ui/notifications/tool-result`.
The actual initial chart and app-requested `drill_chart` run against gateway
`/mcp`; the gateway forwards to the hosted agent. No model invocation is needed
for this deterministic test. App-requested calls receive **only their direct
JSON-RPC response**, deliberately testing that the widget does not depend on a
second tool-result notification.

Check both the ordinary chat and this iframe:

1. An SVG appears; browser console has no CSP/eval errors.
2. Hover a mark and see formatted values in the DOM tooltip.
3. Toggle a category and a series; counts/SVG change without server calls.
4. Reset restores all rows.
5. Drill into Europe and see the France/Germany chart.
6. In the harness, direct-call count increments while notification count stays **1**.
7. Network requests are limited to the local host and its gateway proxy; the
   widget makes no network requests of its own.

`npm --prefix web test` also tests SVG generation/data changes with the global
`Function` constructor blocked, independent of browser CSP.
The shared upstream dataset name is `table`. Local controls use
`view.change("table", changeset())` with cloned rows; they do not re-query the
server or mutate the original bundle rows, summary, or static representations.
