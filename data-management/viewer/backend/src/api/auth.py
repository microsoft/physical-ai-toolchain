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

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Literal, cast

from fastapi import Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from .validation import SAFE_CAMERA_NAME_PATTERN, SAFE_DATASET_ID_PATTERN

logger = logging.getLogger(__name__)

AuthMode = Literal["azure_ad", "auth0", "easy_auth", "apikey", "local"]
MEDIA_COOKIE_NAME = "__Secure-dataviewer-media"
MEDIA_COOKIE_TTL_SECONDS = 300
_MEDIA_PATH = re.compile(
    rf"/api/datasets/{SAFE_DATASET_ID_PATTERN.removeprefix('^').removesuffix('$')}"
    rf"/episodes/[0-9]+/(?:frames/[0-9]+|video/{SAFE_CAMERA_NAME_PATTERN.removeprefix('^').removesuffix('$')})"
)


class PrincipalContext(BaseModel):
    """Opaque browser-safe identity for ownership and draft isolation."""

    scope_id: str
    auth_mode: AuthMode


class MediaSession(BaseModel):
    """Lifetime of the native-media cookie, without exposing credentials."""

    expires_in: int


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

    def __init__(
        self,
        jwks_uri: str,
        audience: str,
        issuer: str,
        auth_method: Literal["azure_ad", "auth0"] = "azure_ad",
    ) -> None:
        self._jwks_uri = jwks_uri
        self._audience = audience
        self._issuer = issuer
        self._auth_method = auth_method
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
        if "Authorization" in request.headers:
            if not auth_header.startswith("Bearer "):
                return None
            token = auth_header[len("Bearer ") :].strip()
        elif (
            request.method in {"GET", "HEAD"}
            and _MEDIA_PATH.fullmatch(request.url.path)
            and request.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"}
        ):
            token = request.cookies.get(MEDIA_COOKIE_NAME, "")
        else:
            return None
        if not token:
            return None
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
                options={"require": ["exp"]},
            )
            payload["auth_method"] = self._auth_method
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
    """Validate an Easy Auth principal bound to the trusted frontend proxy."""

    def __init__(self, expected_proxy_key: str) -> None:
        if not expected_proxy_key:
            raise ValueError("DATAVIEWER_PROXY_KEY is required for easy_auth")
        try:
            self._expected_proxy_key = expected_proxy_key.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("DATAVIEWER_PROXY_KEY must contain only ASCII characters") from exc

    async def authenticate(self, request: Request) -> dict[str, Any] | None:
        proxy_key = request.headers.get("X-Dataviewer-Proxy-Key", "")
        principal = request.headers.get("X-MS-CLIENT-PRINCIPAL", "")
        if not proxy_key or not principal:
            return None

        try:
            supplied_proxy_key = proxy_key.encode("ascii")
        except UnicodeEncodeError:
            return None
        if not secrets.compare_digest(supplied_proxy_key, self._expected_proxy_key):
            return None

        try:
            decoded = base64.b64decode(principal, validate=True)
            principal_document = json.loads(decoded)
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(principal_document, dict):
            return None

        claims = principal_document.get("claims")
        if not isinstance(claims, list):
            return None

        subject = ""
        name = ""
        roles: list[str] = []
        for claim in claims:
            if not isinstance(claim, dict):
                return None
            claim_type = claim.get("typ")
            claim_value = claim.get("val")
            if not isinstance(claim_type, str) or not isinstance(claim_value, str):
                return None
            if "nameidentifier" in claim_type:
                subject = claim_value.strip()
            elif claim_type == "name":
                name = claim_value
            elif claim_type == "roles" and claim_value:
                roles.append(claim_value)

        if not subject:
            return None
        return {
            "sub": subject,
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
        return JwtProvider(jwks_uri=jwks_uri, audience=client_id, issuer=issuer, auth_method="azure_ad")

    if provider_name == "auth0":
        domain = os.environ.get("DATAVIEWER_AUTH0_DOMAIN", "")
        audience = os.environ.get("DATAVIEWER_AUTH0_AUDIENCE", "")
        jwks_uri = f"https://{domain}/.well-known/jwks.json"
        issuer = f"https://{domain}/"
        return JwtProvider(jwks_uri=jwks_uri, audience=audience, issuer=issuer, auth_method="auth0")

    if provider_name == "easy_auth":
        return EasyAuthProvider(os.environ.get("DATAVIEWER_PROXY_KEY", ""))

    logger.error("Unsupported DATAVIEWER_AUTH_PROVIDER value: %s", provider_name)
    raise ValueError(f"Unsupported DATAVIEWER_AUTH_PROVIDER: {provider_name}")


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


def issue_media_session(request: Request, response: Response, user: dict[str, Any] | None) -> MediaSession:
    """Copy an already verified JWT to a short-lived, read-only media cookie."""
    expiration = user.get("exp") if user is not None else None
    authorization = request.headers.get("Authorization", "")
    if (
        user is None
        or user.get("auth_method") not in {"azure_ad", "auth0"}
        or not authorization.startswith("Bearer ")
        or not isinstance(expiration, int)
        or isinstance(expiration, bool)
    ):
        logger.warning("Media session rejected: a verified expiring JWT is required")
        raise HTTPException(status_code=401, detail="JWT authentication required for native media")
    lifetime = min(MEDIA_COOKIE_TTL_SECONDS, expiration - int(time.time()))
    if lifetime <= 0:
        logger.warning("Media session rejected: credential has expired")
        raise HTTPException(status_code=401, detail="Authentication expired")
    token = authorization[len("Bearer ") :].strip()
    if len(token.encode("utf-8")) > 3800:
        logger.warning("Media session rejected: credential exceeds browser cookie capacity")
        raise HTTPException(status_code=400, detail="Authentication token exceeds native media cookie capacity")
    response.set_cookie(
        key=MEDIA_COOKIE_NAME,
        value=token,
        max_age=lifetime,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/api/datasets/",
    )
    response.headers["Cache-Control"] = "no-store"
    return MediaSession(expires_in=lifetime)


def resolve_principal_context(user: dict[str, Any] | None) -> PrincipalContext:
    """Resolve authenticated claims to an opaque stable ownership scope."""
    if user is None:
        auth_mode: AuthMode = "local"
        subject = "disabled-auth-session"
    else:
        raw_auth_mode = str(user.get("auth_method", ""))
        if raw_auth_mode not in {"azure_ad", "auth0", "easy_auth", "apikey"}:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unsupported authentication identity")
        auth_mode = cast(AuthMode, raw_auth_mode)
        subject = str(user.get("sub", "")).strip()
        if not subject:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication subject is required")

    digest = hashlib.sha256(f"{auth_mode}\0{subject}".encode()).hexdigest()
    return PrincipalContext(scope_id=f"principal-{digest}", auth_mode=auth_mode)


def require_principal_context(
    user: dict[str, Any] | None = Depends(require_auth),
) -> PrincipalContext:
    """Return the server-authoritative principal context for the request."""
    return resolve_principal_context(user)


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
