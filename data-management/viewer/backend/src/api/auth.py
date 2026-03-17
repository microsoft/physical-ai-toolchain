"""Authentication dependencies for mutation endpoints.

Supports three providers selectable via DATAVIEWER_AUTH_PROVIDER:
  - ``apikey``   (default): validates the ``X-API-Key`` request header
  - ``azure_ad``: validates a Bearer JWT against Azure AD/Entra ID JWKS
  - ``auth0``:    validates a Bearer JWT against Auth0 JWKS

Set ``DATAVIEWER_AUTH_DISABLED=true`` to bypass authentication in local
development without modifying any route code.

Failed authentication attempts are logged with the client IP and requested
resource; credentials are never logged.
"""

import logging
import os
import secrets
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)

# ============================================================================
# Provider ABCs
# ============================================================================


class AuthProvider(ABC):
    """Abstract base for authentication providers."""

    @abstractmethod
    async def authenticate(self, request: Request) -> dict[str, Any] | None:
        """Return a user-info dict on success, or ``None`` on failure."""
        ...

    @property
    @abstractmethod
    def www_authenticate(self) -> str:
        """Value for the ``WWW-Authenticate`` response header."""
        ...


# ============================================================================
# API Key provider
# ============================================================================


class ApiKeyProvider(AuthProvider):
    """Validates the ``X-API-Key`` header against a configured secret."""

    def __init__(self, expected_key: str) -> None:
        self._expected_key = expected_key

    async def authenticate(self, request: Request) -> dict[str, Any] | None:
        key = request.headers.get("X-API-Key", "")
        if not key or not self._expected_key:
            return None
        if not secrets.compare_digest(key, self._expected_key):
            return None
        return {"sub": "api-key-client", "auth_method": "apikey"}

    @property
    def www_authenticate(self) -> str:
        return 'ApiKey realm="DataViewer API"'


# ============================================================================
# JWT provider (Azure AD / Auth0)
# ============================================================================


class JwtProvider(AuthProvider):
    """Validates Bearer JWTs using a JWKS endpoint (Azure AD or Auth0)."""

    def __init__(self, jwks_uri: str, audience: str, issuer: str) -> None:
        self._jwks_uri = jwks_uri
        self._audience = audience
        self._issuer = issuer
        self._jwks_client: Any = None

    def _get_jwks_client(self) -> Any:
        try:
            import jwt  # pyjwt[cryptography]
        except ImportError as exc:
            raise RuntimeError(
                "pyjwt[cryptography] is required for JWT auth. "
                "Install with: uv pip install 'lerobot-annotation-api[auth]'"
            ) from exc
        if self._jwks_client is None:
            self._jwks_client = jwt.PyJWKClient(self._jwks_uri)
        return self._jwks_client

    async def authenticate(self, request: Request) -> dict[str, Any] | None:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer ") :].strip()
        try:
            import jwt  # pyjwt[cryptography]
        except ImportError as exc:
            raise RuntimeError(
                "pyjwt[cryptography] is required for JWT auth. "
                "Install with: uv pip install 'lerobot-annotation-api[auth]'"
            ) from exc

        try:
            client = self._get_jwks_client()
            signing_key = client.get_signing_key_from_jwt(token)
            payload: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
            return payload
        except jwt.PyJWTError:
            return None

    @property
    def www_authenticate(self) -> str:
        return 'Bearer realm="DataViewer API"'


# ============================================================================
# Easy Auth provider (Azure Container Apps)
# ============================================================================


class EasyAuthProvider(AuthProvider):
    """Reads identity from Azure Container Apps Easy Auth X-MS-CLIENT-PRINCIPAL header."""

    async def authenticate(self, request: Request) -> dict[str, Any] | None:
        principal = request.headers.get("X-MS-CLIENT-PRINCIPAL", "")
        if not principal:
            return None
        import base64
        import json

        try:
            claims_json = json.loads(base64.b64decode(principal))
        except (ValueError, json.JSONDecodeError):
            return None

        claims = claims_json.get("claims", [])
        name_id = ""
        name = ""
        roles: list[str] = []
        for claim in claims:
            typ = claim.get("typ", "")
            val = claim.get("val", "")
            if "nameidentifier" in typ:
                name_id = val
            elif typ == "name":
                name = val
            elif typ == "roles":
                roles.append(val)

        return {
            "sub": name_id,
            "name": name,
            "roles": roles,
            "auth_method": "easy_auth",
        }

    @property
    def www_authenticate(self) -> str:
        return 'EasyAuth realm="DataViewer API"'


# ============================================================================
# Provider factory
# ============================================================================


def _build_provider() -> AuthProvider:
    provider_name = os.environ.get("DATAVIEWER_AUTH_PROVIDER", "apikey").lower()

    if provider_name == "apikey":
        key = os.environ.get("DATAVIEWER_API_KEY", "")
        if not key:
            logger.warning("DATAVIEWER_API_KEY is not set; all API-key auth will fail")
        return ApiKeyProvider(key)

    if provider_name == "azure_ad":
        tenant_id = os.environ.get("DATAVIEWER_AZURE_TENANT_ID", "")
        client_id = os.environ.get("DATAVIEWER_AZURE_CLIENT_ID", "")
        jwks_uri = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        return JwtProvider(jwks_uri=jwks_uri, audience=client_id, issuer=issuer)

    if provider_name == "auth0":
        domain = os.environ.get("DATAVIEWER_AUTH0_DOMAIN", "")
        audience = os.environ.get("DATAVIEWER_AUTH0_AUDIENCE", "")
        jwks_uri = f"https://{domain}/.well-known/jwks.json"
        issuer = f"https://{domain}/"
        return JwtProvider(jwks_uri=jwks_uri, audience=audience, issuer=issuer)

    if provider_name == "easy_auth":
        return EasyAuthProvider()

    logger.error("Unknown DATAVIEWER_AUTH_PROVIDER value: %s; falling back to API-key", provider_name)
    return ApiKeyProvider(os.environ.get("DATAVIEWER_API_KEY", ""))


# Module-level singleton; reset in tests via ``reset_auth_provider()``.
_provider: AuthProvider | None = None


def _get_provider() -> AuthProvider:
    global _provider  # module-level singleton
    if _provider is None:
        _provider = _build_provider()
    return _provider


def reset_auth_provider() -> None:
    """Reset the cached provider singleton (for use in tests)."""
    global _provider  # module-level singleton
    _provider = None


# ============================================================================
# FastAPI dependency
# ============================================================================


async def require_auth(request: Request) -> dict[str, Any] | None:
    """Require valid authentication credentials for the current request.

    Returns the decoded user-info dict on success.
    Raises ``HTTP 401`` with a ``WWW-Authenticate`` header on failure.
    Set ``DATAVIEWER_AUTH_DISABLED=true`` to bypass (local development only).
    """
    if os.environ.get("DATAVIEWER_AUTH_DISABLED", "false").lower() == "true":
        return None

    provider = _get_provider()
    user = await provider.authenticate(request)

    if user is None:
        logger.warning(
            "Authentication failed: method=%s path=%s client=%s",
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": provider.www_authenticate},
        )

    return user


def require_role(required_role: str) -> Callable:
    """FastAPI dependency that enforces an app role from JWT claims.

    When auth is disabled (``DATAVIEWER_AUTH_DISABLED=true``), this dependency
    passes through without checking roles.  When auth is enabled, the user's
    JWT ``roles`` claim must contain *required_role* or HTTP 403 is raised.
    """

    async def _check_role(
        user: dict[str, Any] | None = Depends(require_auth),
    ) -> dict[str, Any] | None:
        if user is None:
            return user
        roles: list[str] = user.get("roles", [])
        if required_role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _check_role
