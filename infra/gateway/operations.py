"""Gateway-only packaging and Entra provisioning; never deploys the hosted agent."""

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[2]
GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_RESOURCE = "https://graph.microsoft.com/"
SECRET_NAME = "copilot-oauth-client-secret"
TOOLKIT = ["npx", "--yes", "--package", "@microsoft/m365agentstoolkit-cli@1.1.16", "atk"]
REDIRECTS = [
    "https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect",
    "https://vscode.dev/redirect",
]
PACKAGE_FILES = {
    "requirements.lock": "requirements.txt",
    "gateway/__init__.py": "gateway/__init__.py",
    "gateway/main.py": "gateway/main.py",
    "gateway/auth.py": "gateway/auth.py",
    "gateway/client.py": "gateway/client.py",
    "gateway/config.py": "gateway/config.py",
    "src/charts_agent/contracts.py": "src/charts_agent/contracts.py",
    "web/dist/mcp-app.html": "web/dist/mcp-app.html",
}


def cli_json(*args: str) -> dict[str, Any]:
    result = subprocess.run(
        ["az", *args, "--output", "json", "--only-show-errors"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Azure CLI {args[0]} failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError("Expected an Azure CLI JSON object.")
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.chmod(temporary, 0o600)
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        except (OSError, TypeError, ValueError):
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_state(path: Path, subscription: str, tenant: str, prefix: str) -> dict[str, Any]:
    UUID(subscription)
    UUID(tenant)
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,45}", prefix):
        raise ValueError("Use a lowercase, alphanumeric/hyphen resource prefix (3-46 characters).")
    expected = {"subscriptionId": subscription, "tenantId": tenant, "prefix": prefix}
    if not path.exists():
        return {"schemaVersion": 1, **expected}
    state = json.loads(path.read_text())
    if state.get("schemaVersion") != 1 or any(state.get(key) != value for key, value in expected.items()):
        raise ValueError("Saved state belongs to a different target or unsupported schema; use another state file.")
    return state


class CloudError(RuntimeError):
    def __init__(self, response: httpx.Response):
        self.status = response.status_code
        try:
            error = response.json().get("error", {})
            self.code = error.get("code", "HTTPError") if isinstance(error, dict) else str(error)
        except ValueError:
            self.code = "NonJsonHTTPError"
        request_id = response.headers.get("request-id") or response.headers.get("x-ms-request-id", "")
        super().__init__(f"{response.request.url.host}: HTTP {self.status}, {self.code}, request {request_id}")


class Cloud:
    def __init__(self, tenant: str):
        self.tenant = tenant
        self.tokens: dict[str, tuple[str, int]] = {}
        self.client = httpx.Client(timeout=60, follow_redirects=False)

    def token(self, resource: str) -> str:
        cached = self.tokens.get(resource)
        if cached and cached[1] > time.time() + 120:
            return cached[0]
        result = cli_json("account", "get-access-token", "--tenant", self.tenant, "--resource", resource)
        token, expires = result["accessToken"], int(result["expires_on"])
        self.tokens[resource] = (token, expires)
        return token

    def request(
        self, method: str, url: str, resource: str, body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = self.client.request(
            method, url, params=params, json=body,
            headers={"Authorization": f"Bearer {self.token(resource)}"},
        )
        if not response.is_success:
            raise CloudError(response)
        if response.status_code == 204:
            return {}
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Expected a cloud API JSON object.")
        return data

    def graph(
        self, method: str, path: str, body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.request(method, GRAPH + path, GRAPH_RESOURCE, body, params)


def ensure_application(cloud: Cloud, name: str, marker: str) -> dict[str, Any]:
    matches = cloud.graph("GET", "/applications", params={"$filter": f"displayName eq '{name}'"})["value"]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple applications named {name}; resolve ambiguity before continuing.")
    if matches:
        app = matches[0]
        if marker not in app.get("tags", []):
            raise RuntimeError(f"Refusing to modify unrelated application {name}.")
        return app
    return cloud.graph("POST", "/applications", {
        "displayName": name, "signInAudience": "AzureADMyOrg", "tags": [marker],
    })


def ensure_principal(cloud: Cloud, app_id: str) -> str:
    matches = cloud.graph("GET", "/servicePrincipals", params={"$filter": f"appId eq '{app_id}'"})["value"]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple service principals for application {app_id}.")
    principal = matches[0] if matches else cloud.graph("POST", "/servicePrincipals", {"appId": app_id})
    return principal["id"]


def provision_identity(cloud: Cloud, state: dict[str, Any], path: Path) -> None:
    prefix = state["prefix"]
    marker = f"FoundryChartsGateway:{prefix}"
    api = ensure_application(cloud, f"Foundry Charts MCP API {prefix}", marker)
    api_settings = api.get("api") or {}
    scopes = api_settings.get("oauth2PermissionScopes") or []
    matching = [scope for scope in scopes if scope["value"] == "Charts.Read"]
    if len(matching) > 1:
        raise RuntimeError("Multiple Charts.Read scopes; resolve the API registration before continuing.")
    scope_id = matching[0]["id"] if matching else str(uuid4())
    state["api"] = {"objectId": api["id"], "clientId": api["appId"], "scopeId": scope_id}
    save_state(path, state)
    scope = {
        "id": scope_id, "value": "Charts.Read", "type": "User", "isEnabled": True,
        "adminConsentDisplayName": "Query the Foundry Charts sample",
        "adminConsentDescription": "Allow the application to query fictional sales and render charts.",
        "userConsentDisplayName": "Explore sample sales charts",
        "userConsentDescription": "Ask the Foundry Charts sample about fictional sales data.",
    }
    cloud.graph("PATCH", f"/applications/{api['id']}", {
        "identifierUris": [f"api://{api['appId']}"],
        "api": {
            **api_settings,
            "requestedAccessTokenVersion": 2,
            "oauth2PermissionScopes": [item for item in scopes if item["value"] != "Charts.Read"] + [scope],
        },
    })
    state["api"]["principalId"] = ensure_principal(cloud, api["appId"])
    save_state(path, state)
    oauth = ensure_application(cloud, f"Foundry Charts Copilot OAuth {prefix}", marker)
    state["oauth"] = {"objectId": oauth["id"], "clientId": oauth["appId"]}
    save_state(path, state)
    cloud.graph("PATCH", f"/applications/{oauth['id']}", {
        "web": {"redirectUris": REDIRECTS},
        "requiredResourceAccess": [{
            "resourceAppId": api["appId"],
            "resourceAccess": [{"id": scope_id, "type": "Scope"}],
        }],
    })
    state["oauth"]["principalId"] = ensure_principal(cloud, oauth["appId"])
    save_state(path, state)


def vault_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".vault.azure.net")
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.port
    ):
        raise ValueError("Expected a public-Azure HTTPS Key Vault origin.")
    return value.rstrip("/")


def get_secret(cloud: Cloud, vault: str) -> dict[str, Any] | None:
    try:
        return cloud.request(
            "GET", f"{vault_url(vault)}/secrets/{SECRET_NAME}",
            "https://vault.azure.net", params={"api-version": "7.5"},
        )
    except CloudError as error:
        if error.status == 404 and error.code == "SecretNotFound":
            return None
        raise


def provision_credential(
    cloud: Cloud, state: dict[str, Any], path: Path, vault: str, *, rotate: bool = False,
) -> None:
    existing = get_secret(cloud, vault)
    oauth = state["oauth"]
    if existing:
        tags = existing.get("tags", {})
        if tags.get("client-id") != oauth["clientId"] or tags.get("application-object-id") != oauth["objectId"]:
            raise RuntimeError("Existing vault secret belongs to a different OAuth application.")
        if not rotate:
            if existing["attributes"].get("exp", 0) <= time.time() + 7 * 86400:
                raise RuntimeError("OAuth credential expires within seven days; rotate it and update the M365 token store.")
            app = cloud.graph("GET", f"/applications/{oauth['objectId']}")
            if not any(item["keyId"] == tags.get("credential-key-id") for item in app["passwordCredentials"]):
                raise RuntimeError("Vault secret has no matching Entra credential; rotate it explicitly.")
            state["credential"] = {
                "keyId": tags["credential-key-id"], "secretUri": existing["id"],
                "expiresOn": datetime.fromtimestamp(existing["attributes"]["exp"], UTC).isoformat(),
            }
            save_state(path, state)
            return
    expires = datetime.now(UTC) + timedelta(days=90)
    credential = cloud.graph("POST", f"/applications/{oauth['objectId']}/addPassword", {
        "passwordCredential": {
            "displayName": f"Copilot token store {state['prefix']}",
            "endDateTime": expires.isoformat(),
        },
    })
    key_id = credential["keyId"]
    try:
        stored = cloud.request(
            "PUT", f"{vault_url(vault)}/secrets/{SECRET_NAME}", "https://vault.azure.net",
            {
                "value": credential["secretText"],
                "attributes": {"enabled": True, "exp": int(expires.timestamp())},
                "contentType": "Microsoft Entra OAuth client secret",
                "tags": {
                    "client-id": oauth["clientId"], "application-object-id": oauth["objectId"],
                    "credential-key-id": key_id,
                },
            },
            {"api-version": "7.5"},
        )
    except (CloudError, httpx.HTTPError, ValueError) as store_error:
        try:
            cloud.graph("POST", f"/applications/{oauth['objectId']}/removePassword", {"keyId": key_id})
        except (CloudError, httpx.HTTPError, ValueError) as cleanup_error:
            raise RuntimeError(
                f"Vault storage failed ({store_error}); cleanup of new credential {key_id} also failed "
                f"({cleanup_error}). Remove that credential explicitly before retrying."
            ) from None
        raise RuntimeError(f"Vault storage failed ({store_error}); the new credential was removed.") from None
    state["credential"] = {"keyId": key_id, "secretUri": stored["id"], "expiresOn": expires.isoformat()}
    save_state(path, state)


def package_gateway(root: Path, output: Path) -> dict[str, Any]:
    root = root.resolve()
    if output.suffix != ".zip":
        raise ValueError("The package output must have a .zip extension.")
    files = []
    for source, destination in PACKAGE_FILES.items():
        path = root / source
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Required regular file missing or outside the repository: {source}")
        files.append((path, destination))
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path, destination in files:
            archive.write(path, destination)
    with ZipFile(output) as archive:
        if set(archive.namelist()) != set(PACKAGE_FILES.values()) or archive.testzip() is not None:
            raise RuntimeError("Gateway ZIP verification failed.")
    return {
        "path": str(output.resolve()), "files": len(files),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


def prepare_copilot(root: Path, project: Path, state: dict[str, Any], origin: str) -> None:
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}
        or parsed.query or parsed.fragment or parsed.username
        or any(character.isspace() for character in origin)
    ):
        raise ValueError("GATEWAY_PUBLIC_ORIGIN must be an exact HTTPS origin.")
    origin = origin.rstrip("/")
    package = project / "appPackage"
    package.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "declarativeAgent.json", "ai-plugin.json", "mcp-tools.json"):
        source = json.loads((root / "appPackage" / name).read_text())
        if name == "manifest.json":
            source["name"] = {
                "short": "Foundry Charts Interactive", "full": "Foundry Charts - Interactive Sales Explorer",
            }
            source["accentColor"] = "#2735A6"
        elif name == "declarativeAgent.json":
            source["name"] = "Foundry Charts Interactive"
        (package / name).write_text(json.dumps(source, indent=2) + "\n")
    for source, destination in (
        ("default-color-icon.png", "color.png"), ("default-outline-icon.png", "outline.png"),
    ):
        shutil.copyfile(root / "m365sideloadmanifest" / source, package / destination)
    developer = json.loads((root / "m365sideloadmanifest" / "manifest.json").read_text())["developer"]
    env_path = project / "env" / ".env.prod"
    values = dict(dotenv_values(env_path, interpolate=False)) if env_path.exists() else {}
    values.update({
        "TEAMSFX_ENV": "prod",
        "ENTRA_TENANT_ID": state["tenantId"],
        "GATEWAY_API_CLIENT_ID": state["api"]["clientId"],
        "COPILOT_OAUTH_CLIENT_ID": state["oauth"]["clientId"],
        "GATEWAY_PUBLIC_ORIGIN": origin,
        "GATEWAY_HOSTNAME": parsed.hostname,
        "PUBLISHER_NAME": developer["name"],
        "WEBSITE_URL": developer["websiteUrl"],
        "PRIVACY_URL": developer["privacyUrl"],
        "TERMS_URL": developer["termsOfUseUrl"],
    })
    env_path.parent.mkdir(parents=True, exist_ok=True)
    with env_path.open("w", encoding="utf-8") as stream:
        os.chmod(env_path, 0o600)
        for key, value in values.items():
            if value is not None:
                stream.write(f"{key}={json.dumps(value)}\n")


def provision_copilot(cloud: Cloud, state: dict[str, Any], project: Path, vault: str) -> None:
    secret = get_secret(cloud, vault)
    if not secret or secret.get("tags", {}).get("client-id") != state["oauth"]["clientId"]:
        raise RuntimeError("The OAuth client's credential is not present in the expected vault.")
    if secret["attributes"].get("exp", 0) <= time.time():
        raise RuntimeError("The OAuth client's credential has expired.")
    environment = {
        **os.environ, "ATK_CLI_SKILL": "true",
        "SECRET_COPILOT_OAUTH_CLIENT_SECRET": secret["value"],
    }
    result = subprocess.run(
        [*TOOLKIT, "provision", "--env", "prod", "-f", str(project), "-i", "false",
         "--telemetry", "false"],
        env=environment, capture_output=True, text=True, check=False,
    )
    for output, stream in ((result.stdout, sys.stdout), (result.stderr, sys.stderr)):
        print(output.replace(secret["value"], "[REDACTED]"), file=stream, end="")
    if result.returncode:
        raise RuntimeError("M365 provisioning failed; inspect the redacted output and complete tenant sign-in/consent.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    package = subparsers.add_parser("package")
    package.add_argument("--output", type=Path, required=True)
    for command in ("identity", "credential", "prepare-copilot", "copilot"):
        action = subparsers.add_parser(command)
        action.add_argument("--subscription", required=True)
        action.add_argument("--tenant", required=True)
        action.add_argument("--prefix", required=True)
        action.add_argument("--state", type=Path, required=True)
        action.add_argument("--apply", action="store_true", help="Required acknowledgement of cloud writes.")
        if command == "credential":
            action.add_argument("--vault", required=True)
            action.add_argument("--rotate", action="store_true")
        if command == "prepare-copilot":
            action.add_argument("--origin", required=True)
        if command == "copilot":
            action.add_argument("--vault", required=True)
    args = parser.parse_args()
    if args.command == "package":
        print(json.dumps(package_gateway(ROOT, args.output), indent=2))
        return
    if args.command != "prepare-copilot" and not args.apply:
        parser.error("Cloud changes require --apply after deployment approval.")
    state = load_state(args.state, args.subscription, args.tenant, args.prefix)
    if args.command == "prepare-copilot":
        prepare_copilot(ROOT, Path(__file__).resolve().parent / "copilot", state, args.origin)
        print("Prepared the separate Copilot project; no cloud resources were changed.")
        return
    account = cli_json("account", "show", "--subscription", args.subscription)
    if account["tenantId"] != args.tenant:
        raise ValueError("Subscription tenant does not match the explicitly supplied tenant.")
    cloud = Cloud(args.tenant)
    try:
        if args.command == "identity":
            provision_identity(cloud, state, args.state)
        elif args.command == "credential":
            provision_credential(cloud, state, args.state, args.vault, rotate=args.rotate)
        else:
            provision_copilot(cloud, state, Path(__file__).resolve().parent / "copilot", args.vault)
    finally:
        cloud.client.close()
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError, httpx.HTTPError) as error:
        print(f"Gateway provisioning failed: {error}", file=sys.stderr)
        sys.exit(1)
