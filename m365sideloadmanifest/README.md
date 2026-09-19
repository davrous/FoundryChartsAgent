# Foundry Charts sideload package

This is the **Activity/custom-engine agent** package for Teams and Microsoft 365
Copilot, not the separate declarative-agent/MCP example in
[appPackage](../appPackage/). It references the existing deployed bot; no Teams
tab or extra hosting is added.

The six natural-language starters are in `bots[].commandLists`, scoped to
personal, team, group chat and Copilot. Their descriptions contain the complete
prompts sent to the agent by M365; titles provide the user-facing labels.
The agent processes these prompts without a new command handler. Clients choose how many
starters to display and where to surface them. No unsupported
`conversation_starters` field is added to the custom-engine manifest.

## Icons

Both icons are original chart artwork. The editable SVGs in
[icon-sources](icon-sources/) are design sources only; the package uses PNGs.

| File | Requirements implemented |
|---|---|
| [default-color-icon.png](default-color-icon.png) | Exactly 192 x 192; fully opaque, flat indigo background; square corners; no outer border; centered mark wholly inside 96 x 96 |
| [default-outline-icon.png](default-outline-icon.png) | Exactly 32 x 32; white-only visible pixels with transparency; no added outer padding or colored/opaque background |

The color mark fits both the current 120 x 120 safe area and the more
conservative 96 x 96 balance guidance. Do not round the canvas corners; the host
applies its own mask. Outline antialiasing uses alpha, not gray pixels.
The manifest accent color matches the color icon's background.

Official references:

- [Microsoft 365 / Teams icon requirements](https://learn.microsoft.com/microsoftteams/platform/concepts/design/design-teams-app-icon-store-appbar)
- [Agent color and white/transparent outline requirements](https://learn.microsoft.com/microsoft-365/copilot/extensibility/agents-are-apps#app-icons)
- [App packaging requirements](https://learn.microsoft.com/microsoftteams/platform/concepts/build-and-test/apps-package)
- [Exact v1.29 manifest schema](https://developer.microsoft.com/json-schemas/teams/v1.29/MicrosoftTeams.schema.json)

## Regenerate and package

Run from the repository root after the normal `./chartagent setup`. The
[builder](build_package.py) uses the existing `vl-convert-python` renderer and
Python's standard library; no new image dependencies are needed. It normalizes
the outline's RGB values to pure white without changing alpha coverage, checks
the rendered pixels, and packages only the three required files.

```bash
src/charts_agent/.venv/bin/python -m m365sideloadmanifest.build_package
./chartagent test tests/test_m365_package.py -q
```

On Windows use `src/charts_agent/.venv/Scripts/python.exe -m m365sideloadmanifest.build_package` and
`./chartagent.ps1 test tests/test_m365_package.py -q`.
For editor analysis, select the same service-local Python environment. An
unrelated root `.venv` can produce Pylance `reportMissingImports` for `vl_convert`
even though this builder and its tests run successfully in the service environment.

Upload **`build/foundry-charts.zip`**, not a ZIP of this entire folder. Its root
must contain only the manifest and the two referenced PNGs, with no enclosing
directory, SVG sources, README or macOS metadata. Increment the manifest's app
version when distributing an update; preserve the app/bot IDs.

Validate against the exact schema and check the PNG dimensions, color-icon
opacity/safe area and white-only outline pixels after regenerating artwork.
Local schema and image checks do not guarantee Microsoft service-side or tenant
acceptance.

The existing identity, scopes and authentication settings are preserved.
`webApplicationInfo.resource` is still the supplied `api://example.com`;
confirm the actual registered Application ID URI before relying on SSO.
The supplied privacy and terms URLs both point to the publisher's homepage;
use real policy pages before a store submission. Do not invent identity or
policy URLs to make a validator appear successful.

## Retest Copilot chart images with version 1.0.4

The [manifest](manifest.json) now allows the deployed chart artifact hostname,
`msdavrous.blob.core.windows.net`, in `validDomains`. Only this exact hostname
is included: no wildcard, scheme, path or SAS query. Keep it aligned with the
artifact storage host if you deploy to another account. This does not make
the Blob container public or change SAS permissions or expiry.

Microsoft documents this allowlist requirement for images returned by
[API plugins and declarative agents](https://learn.microsoft.com/microsoft-365/copilot/extensibility/api-plugin-adaptive-cards#add-domains-to-your-app-manifest).
**Retest result (2026-09-19):** the developer reports that the image still does
not display in M365 Copilot after this change. The allowlist update alone did
not resolve the reported Activity/custom-engine issue. The installed package
and client network diagnostics have not been independently inspected.
The steps below remain useful for collecting a controlled reproduction.

1. Regenerate the package using the commands above, then upload
   [build/foundry-charts.zip](build/foundry-charts.zip) through the same custom
   app upload flow used for the existing agent. Update the existing app when
   offered; its app/bot IDs are unchanged.
2. Confirm the installed package is **1.0.4**, then refresh/reopen Copilot.
   A local manifest edit does not update an already installed package.
3. Start a new agent conversation and ask:
   **"Show a heatmap of 2025 revenue by month and region."**
   Heatmaps deliberately use a PNG image rather than a native chart control,
   so this isolates the image path from native-chart compatibility.
4. Check the fresh response in Copilot and Teams. Do not reuse an old chart
   message or SAS URL: old links can have expired.
5. If Copilot still shows no image, record its client/build and package
   version, a screenshot and sanitized diagnostics. Confirm that the fresh
   PNG URL works directly, but do not share its SAS query in logs or issues.

No hosted-agent redeployment, new app registration or MCP gateway deployment
is required for this manifest-only test.
