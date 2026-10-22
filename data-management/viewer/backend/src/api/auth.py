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

import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import re
import secrets
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

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
            and request.headers.get("Sec-Fetch-Site") == "same-origin"
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


CALLER_TOKEN_HEADER = "X-Dataviewer-Caller-Token"
MAX_CALLER_TOKEN_BYTES = 8192
MAX_PRINCIPAL_ENCODED_BYTES = 8192
MAX_PRINCIPAL_DECODED_BYTES = 6144
MAX_PRINCIPAL_CLAIMS = 64
MAX_PRINCIPAL_VALUE_BYTES = 2048
_SUBJECT_CLAIMS = {"http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "sub"}


def _single_header(request: Request, name: str, limit: int) -> str | None:
    values = [value for key, value in request.scope["headers"] if key.lower() == name.lower().encode("ascii")]
    if len(values) != 1 or not values[0] or len(values[0]) > limit:
        return None
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    return value if "," not in value and not any(character.isspace() for character in value) else None


class CallerVerifier:
    """Validate app-only v2 proof against one configured tenant and frontend ACL."""

    def __init__(self, tenant_id: str, audience: str, object_id: str, client_id: str) -> None:
        import jwt

        for value in (tenant_id, audience, object_id, client_id):
            try:
                if str(UUID(value)) != value:
                    raise ValueError
            except (ValueError, AttributeError):
                raise ValueError(
                    "Hosted authentication requires canonical tenant, API and caller UUID settings"
                ) from None
        self._tenant_id = tenant_id
        self._audience = audience
        self._object_id = object_id
        self._client_id = client_id
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._jwks_client = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
            cache_keys=False,
            lifespan=300,
            timeout=5,
        )
        self._lock = asyncio.Lock()

    def _decode(self, token: str) -> dict[str, Any]:
        import jwt

        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self._audience,
            issuer=self._issuer,
            options={
                "require": ["ver", "tid", "idtyp", "oid", "sub", "azp", "exp", "nbf", "aud", "iss"],
                "strict_aud": True,
            },
        )

    async def authorize(self, request: Request) -> None:
        import jwt

        token = _single_header(request, CALLER_TOKEN_HEADER, MAX_CALLER_TOKEN_BYTES)
        invalid = HTTPException(
            status_code=401,
            detail="Caller authentication required",
            headers={"WWW-Authenticate": 'EasyAuth realm="DataViewer API"'},
        )
        if token is None:
            raise invalid
        try:
            async with asyncio.timeout(6):
                async with self._lock:
                    claims = await asyncio.to_thread(self._decode, token)
            if (
                claims["ver"] != "2.0"
                or claims["tid"] != self._tenant_id
                or claims["idtyp"] != "app"
                or "scp" in claims
                or any(not isinstance(claims[key], str) or not claims[key].strip() for key in ("oid", "sub", "azp"))
                or any(
                    isinstance(claims[key], bool)
                    or not isinstance(claims[key], (int, float))
                    or not math.isfinite(claims[key])
                    for key in ("exp", "nbf")
                )
            ):
                raise invalid
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OSError, TimeoutError):
            raise invalid from None
        if claims["oid"] != self._object_id or claims["azp"] != self._client_id:
            raise HTTPException(status_code=403, detail="Caller is not authorized")


class EasyAuthProvider(AuthProvider):
    """Accept a bounded forwarded user only after frontend workload authorization."""

    def __init__(self, verifier: CallerVerifier) -> None:
        self.verifier = verifier

    async def authenticate(self, request: Request) -> dict[str, Any] | None:
        await self.verifier.authorize(request)
        principal = _single_header(request, "X-MS-CLIENT-PRINCIPAL", MAX_PRINCIPAL_ENCODED_BYTES)
        if principal is None:
            return None
        try:
            decoded = base64.b64decode(principal, validate=True)
            if len(decoded) > MAX_PRINCIPAL_DECODED_BYTES:
                return None
            claims_json = json.loads(decoded)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return None
        if not isinstance(claims_json, dict):
            return None
        claims = claims_json.get("claims")
        if not isinstance(claims, list) or len(claims) > MAX_PRINCIPAL_CLAIMS:
            return None
        subjects = []
        name = ""
        roles = []
        for claim in claims:
            if not isinstance(claim, dict):
                return None
            typ = claim.get("typ")
            val = claim.get("val")
            if not isinstance(typ, str) or not isinstance(val, str):
                return None
            try:
                type_bytes = typ.encode("utf-8")
                value_bytes = val.encode("utf-8")
            except UnicodeEncodeError:
                return None
            if not typ or len(type_bytes) > 256 or len(value_bytes) > MAX_PRINCIPAL_VALUE_BYTES:
                return None
            if typ in _SUBJECT_CLAIMS:
                subjects.append(val)
            elif typ == "name":
                name = val
            elif typ == "roles" and val:
                roles.append(val)
        if len(subjects) != 1 or not subjects[0].strip():
            return None
        return {
            "sub": subjects[0],
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
        if os.environ.get("DATAVIEWER_AUTH_DISABLED", "false").lower() == "true":
            raise ValueError("Hosted authentication cannot be disabled")
        return EasyAuthProvider(
            CallerVerifier(
                os.environ.get("DATAVIEWER_AZURE_TENANT_ID", ""),
                os.environ.get("DATAVIEWER_AZURE_CLIENT_ID", ""),
                os.environ.get("DATAVIEWER_CALLER_OBJECT_ID", ""),
                os.environ.get("DATAVIEWER_CALLER_CLIENT_ID", ""),
            )
        )

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


def validate_hosted_auth_configuration() -> None:
    """Fail startup before storage or background workers for invalid hosted settings."""
    if os.environ.get("DATAVIEWER_AUTH_PROVIDER", "apikey").lower() == "easy_auth":
        _get_provider()


class HostedAuthMiddleware:
    """Enforce hosted authentication before routing, including bootstrap and docs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and os.environ.get("DATAVIEWER_AUTH_PROVIDER", "apikey").lower() == "easy_auth"
            and not (scope["path"] == "/health" and scope["method"] in {"GET", "HEAD"})
        ):
            request = Request(scope, receive)
            try:
                request.state.hosted_user = await require_auth(request)
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# ============================================================================
# FastAPI dependency
# ============================================================================


async def require_auth(request: Request) -> dict[str, Any] | None:
    """Require valid authentication credentials for the current request.

    Returns the decoded user-info dict on success.
    Raises ``HTTP 401`` with a ``WWW-Authenticate`` header on failure.
    Set ``DATAVIEWER_AUTH_DISABLED=true`` to bypass (local development only).
    """
    hosted = os.environ.get("DATAVIEWER_AUTH_PROVIDER", "apikey").lower() == "easy_auth"
    if hosted and hasattr(request.state, "hosted_user"):
        return request.state.hosted_user
    if not hosted and os.environ.get("DATAVIEWER_AUTH_DISABLED", "false").lower() == "true":
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
