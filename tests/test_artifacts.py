import pytest

from artifact_storage import ArtifactStore


@pytest.mark.parametrize("base_url, expected_url", [
    (None, "http://localhost:8088/artifacts"),
    ("http://localhost:9091/artifacts", "http://localhost:9091/artifacts"),
])
async def test_local_artifact_bytes_and_urls(monkeypatch, tmp_path, base_url, expected_url):
    monkeypatch.setenv("CHARTS_DEV_MODE", "true")
    monkeypatch.setenv("ARTIFACT_MODE", "local")
    monkeypatch.setenv("ARTIFACT_DIRECTORY", str(tmp_path))
    if base_url is None:
        monkeypatch.delenv("ARTIFACT_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("ARTIFACT_BASE_URL", base_url)
    store = ArtifactStore()
    urls = await store.save("test", b"PNG", "<svg/>")
    assert (tmp_path / "test.png").read_bytes() == b"PNG"
    assert (tmp_path / "test.svg").read_text() == "<svg/>"
    assert urls["png"] == f"{expected_url}/test.png"
    assert urls["svg"] == f"{expected_url}/test.svg"
    await store.close()


def test_production_requires_blob(monkeypatch):
    monkeypatch.setenv("CHARTS_DEV_MODE", "false")
    monkeypatch.setenv("ARTIFACT_MODE", "local")
    with pytest.raises(ValueError, match="Use blob storage"):
        ArtifactStore()


async def test_blob_uses_private_uploads_and_readonly_delegation_sas(monkeypatch):
    import artifact_storage

    uploads, signing = [], []

    class Blob:
        def __init__(self, name):
            self.blob_name = name
            self.url = f"https://sample.blob.core.windows.net/charts/{name}"

        async def upload_blob(self, data, **kwargs):
            uploads.append((self.blob_name, data, kwargs))

    class Client:
        account_name = "sample"

        async def get_user_delegation_key(self, start, expiry):
            assert 60 < (expiry - start).total_seconds() / 60 <= 65
            return "delegation-key-test-double"

        def get_blob_client(self, container, name):
            assert container == "charts"
            return Blob(name)

        async def close(self):
            pass

    def sign(**kwargs):
        signing.append(kwargs)
        return "test-signature"

    monkeypatch.setenv("ARTIFACT_MODE", "blob")
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_URL", "https://sample.blob.core.windows.net")
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER", "charts")
    monkeypatch.setenv("ARTIFACT_TTL_MINUTES", "60")
    monkeypatch.setattr(artifact_storage, "BlobServiceClient", lambda *args, **kwargs: Client())
    monkeypatch.setattr(artifact_storage, "generate_blob_sas", sign)
    store = ArtifactStore()
    try:
        urls = await store.save("chart-123", b"PNG", "<svg/>")
        assert urls["png"].endswith("chart-123.png?test-signature")
        assert [u[2]["content_settings"].content_type for u in uploads] == ["image/png", "image/svg+xml"]
        assert all(u[2]["overwrite"] is False for u in uploads)
        assert all(s["permission"].read and not s["permission"].write for s in signing)
        assert all(s["protocol"] == "https" and "account_key" not in s for s in signing)
    finally:
        await store.close()
