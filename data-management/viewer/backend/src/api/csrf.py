"""CSRF protection for the DataViewer API.

Uses the *double-submit cookie* pattern:

1. The client calls ``GET /api/csrf-token``.
2. The server generates a random token, returns it in the JSON body **and**
   sets it as a ``csrf_token`` cookie (``SameSite=Strict``).
3. For every state-changing request (POST / PUT / PATCH / DELETE) the client
   includes the token in the ``X-CSRF-Token`` request header.
4. The server compares the header value to the cookie value; mismatches
   result in ``HTTP 403``.

Set ``DATAVIEWER_AUTH_DISABLED=true`` to bypass CSRF validation in local
development (mirrors the auth bypass flag).
"""

import logging
import os
import secrets

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
_CSRF_TOKEN_BYTES = 32

# Paths that are never subject to CSRF validation.
_CSRF_EXEMPT_PATHS = frozenset({"/api/csrf-token", "/health"})
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def generate_csrf_token() -> str:
    """Return a cryptographically random hex token."""
    return secrets.token_hex(_CSRF_TOKEN_BYTES)


async def require_csrf_token(request: Request) -> None:
    """FastAPI dependency: enforce CSRF token validation for mutation requests.

    Safe (GET / HEAD / OPTIONS) requests are skipped automatically.
    Raises ``HTTP 403`` when the ``X-CSRF-Token`` header is absent or does not
    match the ``csrf_token`` cookie.
    """
    if os.environ.get("DATAVIEWER_AUTH_DISABLED", "false").lower() == "true":
        return

    if request.method not in _MUTATION_METHODS:
        return

    if request.url.path in _CSRF_EXEMPT_PATHS:
        return

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)

    if not cookie_token or not header_token:
        logger.warning(
            "CSRF token missing: method=%s path=%s client=%s",
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )

    if not secrets.compare_digest(cookie_token, header_token):
        logger.warning(
            "CSRF token mismatch: method=%s path=%s client=%s",
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )
