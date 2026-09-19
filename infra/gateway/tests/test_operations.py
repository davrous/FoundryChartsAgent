from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import httpx
import pytest

from infra.gateway.operations import (
    Cloud, CloudError, PACKAGE_FILES, ROOT, SECRET_NAME, ensure_application,
    get_secret, load_state, package_gateway, provision_credential, provision_identity,
    prepare_copilot, provision_copilot, save_state, vault_url,
)


SUBSCRIPTION = "480af19d-6138-460b-962c-e2a0b5aca925"
TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"
PREFIX = "chartsmcp-prod-7c90"
VAULT = "https://kv-chartsmcp-prod-7c90.vault.azure.net"


def cloud_error(status=403, code="Forbidden"):
    return CloudError(httpx.Response(
        status, json={"error": {"code": code, "message": "Do not log service bodies."}},
        request=httpx.Request("GET", VAULT),
    ))


def test_package_contains_exact_allowlist_and_matching_bytes(tmp_path):
    result = package_gateway(ROOT, tmp_path / "gateway.zip")
    assert result["files"] == 8
    assert len(result["sha256"]) == 64
    with ZipFile(result["path"]) as archive:
        assert set(archive.namelist()) == set(PACKAGE_FILES.values())
        for source, destination in PACKAGE_FILES.items():
            assert archive.read(destination) == (ROOT / source).read_bytes()
        assert "web/dist/index.html" not in archive.namelist()
        assert not any(".env" in name for name in archive.namelist())


def test_package_missing_build_fails_before_writing(tmp_path):
    output = tmp_path / "missing.zip"
    with pytest.raises(ValueError, match="Required regular file"):
        package_gateway(tmp_path, output)
    assert not output.exists()


def test_package_rejects_symlinks(tmp_path):
    (tmp_path / "requirements.lock").symlink_to(ROOT / "requirements.lock")
    with pytest.raises(ValueError, match="Required regular file"):
        package_gateway(tmp_path, tmp_path / "gateway.zip")


def test_state_is_private_and_target_bound(tmp_path):
    path = tmp_path / "state.json"
    state = load_state(path, SUBSCRIPTION, TENANT, PREFIX)
    save_state(path, state)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_state(path, SUBSCRIPTION, TENANT, PREFIX) == state
    with pytest.raises(ValueError, match="different target"):
        load_state(path, SUBSCRIPTION, TENANT, "unrelated")
    assert list(tmp_path.iterdir()) == [path]


def test_reuse_requires_ownership_marker():
    cloud = Mock(spec=Cloud)
    cloud.graph.return_value = {"value": [{"id": "existing", "tags": []}]}
    with pytest.raises(RuntimeError, match="unrelated application"):
        ensure_application(cloud, "Existing app", "our-marker")
    assert cloud.graph.call_count == 1


def test_application_name_collision_fails():
    cloud = Mock(spec=Cloud)
    cloud.graph.return_value = {"value": [{"id": "one"}, {"id": "two"}]}
    with pytest.raises(RuntimeError, match="Multiple applications"):
        ensure_application(cloud, "Duplicated app", "marker")


def test_identity_uses_v2_scope_two_apps_and_both_principals(tmp_path):
    state = load_state(tmp_path / "state.json", SUBSCRIPTION, TENANT, PREFIX)
    cloud = Mock(spec=Cloud)
    writes = []

    def graph(method, path, body=None, params=None):
        if method == "GET":
            return {"value": []}
        writes.append((method, path, body))
        if path == "/applications":
            is_api = "MCP API" in body["displayName"]
            return {"id": "api-object" if is_api else "oauth-object", "appId": "api-id" if is_api else "oauth-id"}
        if path == "/servicePrincipals":
            return {"id": f"principal-{body['appId']}"}
        return {}

    cloud.graph.side_effect = graph
    provision_identity(cloud, state, tmp_path / "state.json")
    assert state["api"]["principalId"] == "principal-api-id"
    assert state["oauth"]["principalId"] == "principal-oauth-id"
    api_patch = next(body for _, path, body in writes if path == "/applications/api-object")
    assert api_patch["api"]["requestedAccessTokenVersion"] == 2
    assert api_patch["identifierUris"] == ["api://api-id"]
    assert api_patch["api"]["oauth2PermissionScopes"][0]["value"] == "Charts.Read"
    oauth_patch = next(body for _, path, body in writes if path == "/applications/oauth-object")
    assert len(oauth_patch["web"]["redirectUris"]) == 2
    assert oauth_patch["requiredResourceAccess"] == [{
        "resourceAppId": "api-id",
        "resourceAccess": [{"id": state["api"]["scopeId"], "type": "Scope"}],
    }]


@pytest.mark.parametrize("url", [
    "http://unsafe.vault.azure.net",
    "https://example.com",
    "https://vault.azure.net.evil.example",
    "https://user:password@unsafe.vault.azure.net",
    "https://unsafe.vault.azure.net/path",
    "https://unsafe.vault.azure.net?key=value",
])
def test_vault_url_rejects_non_vault_endpoints(url):
    with pytest.raises(ValueError):
        vault_url(url)


def test_secret_lookup_does_not_hide_authorization_failures():
    cloud = Mock(spec=Cloud)
    cloud.request.side_effect = cloud_error()
    with pytest.raises(CloudError, match="403"):
        get_secret(cloud, VAULT)
    cloud.request.side_effect = cloud_error(404, "SecretNotFound")
    assert get_secret(cloud, VAULT) is None
    cloud.request.side_effect = cloud_error(404, "VaultNotFound")
    with pytest.raises(CloudError):
        get_secret(cloud, VAULT)


def test_credential_value_goes_only_to_vault_not_state_or_output(tmp_path, capsys):
    path = tmp_path / "state.json"
    state = {"prefix": PREFIX, "oauth": {"clientId": "client-id", "objectId": "oauth-id"}}
    cloud = Mock(spec=Cloud)
    cloud.request.side_effect = [cloud_error(404, "SecretNotFound"), {"id": f"{VAULT}/secrets/{SECRET_NAME}/v1"}]
    cloud.graph.return_value = {"keyId": "key-id", "secretText": "sensitive-test-value"}
    provision_credential(cloud, state, path, VAULT)
    assert "sensitive-test-value" not in path.read_text()
    assert "sensitive-test-value" not in capsys.readouterr().out
    put = cloud.request.call_args
    assert put.args[0] == "PUT"
    assert put.args[3]["value"] == "sensitive-test-value"
    assert put.args[3]["tags"]["credential-key-id"] == "key-id"
    assert state["credential"]["keyId"] == "key-id"
    expiry = datetime.fromisoformat(state["credential"]["expiresOn"])
    assert timedelta(days=89) < expiry - datetime.now(UTC) <= timedelta(days=90)


def test_failed_vault_write_removes_only_new_credential(tmp_path):
    cloud = Mock(spec=Cloud)
    cloud.request.side_effect = [cloud_error(404, "SecretNotFound"), cloud_error()]
    cloud.graph.side_effect = [{"keyId": "new-key", "secretText": "never-log"}, {}]
    state = {"prefix": PREFIX, "oauth": {"clientId": "client-id", "objectId": "oauth-id"}}
    with pytest.raises(RuntimeError, match="new credential was removed") as failure:
        provision_credential(cloud, state, tmp_path / "state.json", VAULT)
    assert "never-log" not in str(failure.value)
    cloud.graph.assert_called_with("POST", "/applications/oauth-id/removePassword", {"keyId": "new-key"})
    assert "credential" not in state


def test_rotation_keeps_old_token_store_credential(tmp_path):
    expires = int((datetime.now(UTC) + timedelta(days=40)).timestamp())
    existing = {
        "id": f"{VAULT}/secrets/{SECRET_NAME}/old",
        "tags": {"client-id": "client-id", "application-object-id": "oauth-id", "credential-key-id": "old-key"},
        "attributes": {"exp": expires},
    }
    cloud = Mock(spec=Cloud)
    cloud.request.side_effect = [existing, {"id": f"{VAULT}/secrets/{SECRET_NAME}/new"}]
    cloud.graph.return_value = {"keyId": "new-key", "secretText": "new-secret"}
    state = {"prefix": PREFIX, "oauth": {"clientId": "client-id", "objectId": "oauth-id"}}
    provision_credential(cloud, state, tmp_path / "state.json", VAULT, rotate=True)
    assert cloud.graph.call_count == 1
    assert cloud.graph.call_args.args[1].endswith("/addPassword")


def test_reuse_checks_secret_application_and_credential(tmp_path):
    expires = int((datetime.now(UTC) + timedelta(days=40)).timestamp())
    existing = {
        "id": f"{VAULT}/secrets/{SECRET_NAME}/v1",
        "tags": {"client-id": "client-id", "application-object-id": "oauth-id", "credential-key-id": "key-id"},
        "attributes": {"exp": expires},
    }
    cloud = Mock(spec=Cloud)
    cloud.request.return_value = existing
    cloud.graph.return_value = {"passwordCredentials": [{"keyId": "key-id"}]}
    state = {"prefix": PREFIX, "oauth": {"clientId": "client-id", "objectId": "oauth-id"}}
    provision_credential(cloud, state, tmp_path / "state.json", VAULT)
    assert cloud.request.call_count == 1
    assert state["credential"]["keyId"] == "key-id"
    assert json.loads((tmp_path / "state.json").read_text()) == state


def test_cloud_error_omits_response_bodies():
    error = cloud_error()
    assert "Do not log service bodies" not in str(error)
    assert error.code == "Forbidden"


def test_copilot_project_reuses_templates_and_validated_icons(tmp_path):
    from dotenv import dotenv_values

    state = {
        "tenantId": TENANT, "api": {"clientId": SUBSCRIPTION}, "oauth": {"clientId": TENANT},
    }
    origin = "https://sample-gateway.azurewebsites.net"
    prepare_copilot(ROOT, tmp_path, state, origin)
    package = tmp_path / "appPackage"
    manifest = json.loads((package / "manifest.json").read_text())
    assert manifest["name"]["short"] == "Foundry Charts Interactive"
    assert "bots" not in manifest and "staticTabs" not in manifest
    assert manifest["accentColor"] == "#2735A6"
    assert (package / "color.png").read_bytes() == (
        ROOT / "m365sideloadmanifest/default-color-icon.png"
    ).read_bytes()
    assert (package / "outline.png").read_bytes() == (
        ROOT / "m365sideloadmanifest/default-outline-icon.png"
    ).read_bytes()
    env_path = tmp_path / "env/.env.prod"
    with env_path.open("a") as stream:
        stream.write('COPILOT_APP_ID="existing-registration"\n')
    prepare_copilot(ROOT, tmp_path, state, origin)
    values = dotenv_values(env_path)
    assert values["COPILOT_APP_ID"] == "existing-registration"
    assert values["GATEWAY_PUBLIC_ORIGIN"] == origin
    assert not any(key.startswith("SECRET_") for key in values)


def test_copilot_secret_is_not_passed_in_command_arguments_or_printed(tmp_path, monkeypatch, capsys):
    cloud = Mock(spec=Cloud)
    cloud.request.return_value = {
        "value": "private-oauth-secret",
        "tags": {"client-id": "oauth-id"},
        "attributes": {"exp": int((datetime.now(UTC) + timedelta(days=30)).timestamp())},
    }
    run = Mock(return_value=SimpleNamespace(
        returncode=0, stdout="value=private-oauth-secret\n", stderr="private-oauth-secret\n",
    ))
    monkeypatch.setattr("infra.gateway.operations.subprocess.run", run)
    provision_copilot(cloud, {"oauth": {"clientId": "oauth-id"}}, tmp_path, VAULT)
    assert "private-oauth-secret" not in repr(run.call_args.args)
    assert run.call_args.kwargs["env"]["SECRET_COPILOT_OAUTH_CLIENT_SECRET"] == "private-oauth-secret"
    output = capsys.readouterr()
    assert "private-oauth-secret" not in output.out + output.err
    assert "[REDACTED]" in output.out
