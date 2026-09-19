from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    mode: str = "local"
    host: str = "127.0.0.1"
    port: int = 8190
    dev_mode: bool = False
    local_agent_url: str = "http://127.0.0.1:8088"
    project_endpoint: str = ""
    agent_name: str = ""
    agent_version: str = ""
    tenant_id: str = ""
    audience: str = ""
    required_scopes: tuple[str, ...] = ("Charts.Read",)
    public_origin: str = ""
    mcp_origins: tuple[str, ...] = ()
    web_dist: Path = Path(__file__).resolve().parents[1] / "web" / "dist"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mode=os.getenv("GATEWAY_MODE", "local"),
            host=os.getenv("GATEWAY_HOST", "127.0.0.1"),
            port=int(os.getenv("GATEWAY_PORT", "8190")),
            dev_mode=os.getenv("CHARTS_DEV_MODE", "").lower() == "true",
            local_agent_url=os.getenv("LOCAL_AGENT_URL", "http://127.0.0.1:8088"),
            project_endpoint=os.getenv("FOUNDRY_PROJECT_ENDPOINT", ""),
            agent_name=os.getenv("FOUNDRY_AGENT_NAME", ""),
            agent_version=os.getenv("FOUNDRY_AGENT_VERSION", ""),
            tenant_id=os.getenv("ENTRA_TENANT_ID", ""),
            audience=os.getenv("ENTRA_AUDIENCE", ""),
            required_scopes=tuple(os.getenv("ENTRA_REQUIRED_SCOPES", "Charts.Read").split()),
            public_origin=os.getenv("GATEWAY_PUBLIC_ORIGIN", "").rstrip("/"),
            mcp_origins=tuple(os.getenv("GATEWAY_MCP_ORIGINS", "").split()),
        )

    @property
    def origin(self) -> str:
        return self.public_origin or f"http://{self.host}:{self.port}"

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def allowed_origins(self) -> set[str]:
        if self.dev_mode:
            return {self.origin, f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}"}
        return {self.origin}

    def validate(self) -> None:
        if self.mode not in {"local", "foundry"}:
            raise ValueError("GATEWAY_MODE must be local or foundry")
        for value in self.mcp_origins:
            origin = urlsplit(value)
            if origin.scheme != "https" or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username is not None:
                raise ValueError("GATEWAY_MCP_ORIGINS must contain exact HTTPS origins, not wildcards or paths")
            if "*" in value:
                raise ValueError("Wildcard MCP origins are not allowed")
        endpoint = urlsplit(self.local_agent_url)
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username is not None or endpoint.query or endpoint.fragment:
            raise ValueError("LOCAL_AGENT_URL must be a server-configured HTTP(S) endpoint")
        if self.mode == "foundry":
            project = urlsplit(self.project_endpoint)
            if project.scheme != "https" or not project.hostname or project.username is not None or project.query or project.fragment:
                raise ValueError("FOUNDRY_PROJECT_ENDPOINT must be an HTTPS project endpoint")
            if not self.agent_name:
                raise ValueError("FOUNDRY_AGENT_NAME is required")
        if self.dev_mode:
            if not is_loopback(self.host):
                raise ValueError("CHARTS_DEV_MODE is allowed only on a loopback interface")
            if self.public_origin and not is_loopback(urlsplit(self.public_origin).hostname or ""):
                raise ValueError("Development origin must be loopback")
            return
        if not self.tenant_id or not self.audience or not self.required_scopes:
            raise ValueError("Production requires ENTRA_TENANT_ID, ENTRA_AUDIENCE and ENTRA_REQUIRED_SCOPES")
        UUID(self.tenant_id)
        origin = urlsplit(self.public_origin)
        if origin.scheme != "https" or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username is not None:
            raise ValueError("Production requires an HTTPS GATEWAY_PUBLIC_ORIGIN without a path")
