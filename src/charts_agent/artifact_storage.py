import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import BlobSasPermissions, ContentSettings, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient


class ArtifactStore:
    def __init__(self) -> None:
        self.mode = os.getenv("ARTIFACT_MODE", "local")
        self.directory = Path(os.getenv("ARTIFACT_DIRECTORY", "artifacts")).resolve()
        self.base_url = os.getenv("ARTIFACT_BASE_URL", "http://localhost:8188/artifacts").rstrip("/")
        self.credential: DefaultAzureCredential | None = None
        self.client: BlobServiceClient | None = None
        if self.mode == "local":
            if os.getenv("CHARTS_DEV_MODE", "false").lower() != "true":
                raise ValueError("Local artifacts require CHARTS_DEV_MODE=true. Use blob storage when hosted.")
            self.directory.mkdir(parents=True, exist_ok=True)
        elif self.mode == "blob":
            account_url = os.environ["AZURE_STORAGE_ACCOUNT_URL"]
            if urlparse(account_url).scheme != "https":
                raise ValueError("AZURE_STORAGE_ACCOUNT_URL must use HTTPS")
            self.container = os.environ["AZURE_STORAGE_CONTAINER"]
            self.ttl = int(os.getenv("ARTIFACT_TTL_MINUTES", "60"))
            if not 1 <= self.ttl <= 1440:
                raise ValueError("ARTIFACT_TTL_MINUTES must be between 1 and 1440")
            self.credential = DefaultAzureCredential()
            self.client = BlobServiceClient(account_url, credential=self.credential)
        else:
            raise ValueError("ARTIFACT_MODE must be local or blob")

    async def save(self, chart_id: str, png: bytes, svg: str) -> dict[str, str]:
        files = {"png": (png, "image/png"), "svg": (svg.encode(), "image/svg+xml")}
        if self.client is None:
            for extension, (data, _) in files.items():
                await asyncio.to_thread((self.directory / f"{chart_id}.{extension}").write_bytes, data)
            return {ext: f"{self.base_url}/{chart_id}.{ext}" for ext in files}

        now = datetime.now(timezone.utc)
        start, expiry = now - timedelta(minutes=5), now + timedelta(minutes=self.ttl)
        key = await self.client.get_user_delegation_key(start, expiry)
        urls: dict[str, str] = {}
        for extension, (data, content_type) in files.items():
            blob = self.client.get_blob_client(self.container, f"{chart_id}.{extension}")
            await blob.upload_blob(data, overwrite=False, content_settings=ContentSettings(content_type=content_type))
            sas = generate_blob_sas(
                account_name=self.client.account_name,
                container_name=self.container,
                blob_name=blob.blob_name,
                user_delegation_key=key,
                permission=BlobSasPermissions(read=True),
                start=start,
                expiry=expiry,
                protocol="https",
            )
            urls[extension] = f"{blob.url}?{sas}"
        return urls

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
        if self.credential is not None:
            await self.credential.close()
