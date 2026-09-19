import asyncio

import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse

from gateway.config import Settings


class EntraVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jwks = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{settings.tenant_id}/discovery/v2.0/keys",
            cache_jwk_set=True,
            lifespan=3600,
            timeout=10,
        )

    async def verify(self, token: str) -> dict:
        key = await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=self.settings.audience,
            issuer=self.settings.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "tid"]},
        )
        if claims["tid"] != self.settings.tenant_id:
            raise jwt.InvalidTokenError("Unexpected tenant")
        scopes = set(claims.get("scp", "").split())
        if not set(self.settings.required_scopes).issubset(scopes):
            raise PermissionError("Required delegated scopes are missing")
        return claims


class SecurityMiddleware:
    def __init__(self, app, settings: Settings, verifier: EntraVerifier | None = None):
        self.app = app
        self.settings = settings
        self.verifier = verifier or (None if settings.dev_mode else EntraVerifier(settings))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive=receive)
        path = request.url.path
        is_mcp = path == "/mcp" or path.startswith("/mcp/")
        protected = is_mcp or path.startswith("/api/")
        origin = request.headers.get("origin")
        allowed = self.settings.allowed_origins | set(self.settings.mcp_origins) if is_mcp else self.settings.allowed_origins
        if protected and origin and origin not in allowed:
            return await JSONResponse({"error": "Origin is not allowed."}, status_code=403)(scope, receive, send)
        if path.startswith("/api/") and request.method == "POST":
            if origin not in self.settings.allowed_origins:
                return await JSONResponse(
                    {"error": "A same-origin request is required."}, status_code=403
                )(scope, receive, send)
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return await JSONResponse(
                    {"error": "Content-Type must be application/json."}, status_code=415
                )(scope, receive, send)
        if protected and not self.settings.dev_mode:
            authorization = request.headers.get("authorization", "")
            scheme, _, token = authorization.partition(" ")
            status = 401
            error = "A valid bearer token is required."
            if scheme.lower() == "bearer" and token:
                try:
                    scope["auth_claims"] = await self.verifier.verify(token)
                except PermissionError:
                    status, error = 403, "The token does not include the required scopes."
                except (jwt.PyJWTError, jwt.PyJWKClientError):
                    pass
                else:
                    return await self.app(scope, receive, send)
            challenge = (
                f'Bearer resource_metadata="{self.settings.origin}/.well-known/oauth-protected-resource", '
                f'scope="{" ".join(self.settings.required_scopes)}"'
            )
            return await JSONResponse(
                {"error": error}, status_code=status, headers={"WWW-Authenticate": challenge}
            )(scope, receive, send)
        await self.app(scope, receive, send)
