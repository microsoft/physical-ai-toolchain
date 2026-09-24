"""Fail-closed authorization for hardware-capable operator operations."""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from ..auth import require_auth
from ..csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME


def _roles(user: dict[str, Any]) -> list[str]:
    raw_roles = user.get("roles", [])
    if isinstance(raw_roles, str):
        return [raw_roles]
    if isinstance(raw_roles, list):
        return [role for role in raw_roles if isinstance(role, str)]
    return []


async def require_operator_access(
    request: Request,
    user: dict[str, Any] | None = Depends(require_auth),
) -> None:
    """Authorize the operator surface under the current development policy."""
    if user is None:
        return
    if user.get("auth_method") == "apikey":
        expected = os.environ.get("DATAVIEWER_OPERATOR_API_KEY", "")
        supplied = request.headers.get("X-Operator-API-Key", "")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator API key required")
        return
    if "Operator" not in _roles(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")


async def require_hardware_access(
    request: Request,
    user: dict[str, Any] | None = Depends(require_auth),
) -> None:
    """Require trusted authentication before any physical-hardware operation."""
    if os.environ.get("OPERATOR_ADAPTER_MODE", "disabled").lower() != "lerobot":
        return
    if os.environ.get("DATAVIEWER_AUTH_DISABLED", "false").lower() == "true" or user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hardware access requires authentication")

    auth_method = user.get("auth_method")
    if auth_method == "apikey":
        expected = os.environ.get("DATAVIEWER_OPERATOR_API_KEY", "")
        general = os.environ.get("DATAVIEWER_API_KEY", "")
        supplied = request.headers.get("X-Operator-API-Key", "")
        if not expected or expected == general or not secrets.compare_digest(expected, supplied):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Distinct operator API key required")
        return
    if auth_method == "easy_auth" and os.environ.get("OPERATOR_TRUST_EASY_AUTH", "false").lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Easy Auth is not trusted for hardware access",
        )
    if "Operator" not in _roles(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")


async def require_operator_csrf(request: Request) -> None:
    """Enforce double-submit CSRF for physical-hardware mutations."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if os.environ.get("OPERATOR_ADAPTER_MODE", "disabled").lower() != "lerobot":
        return
    if os.environ.get("OPERATOR_CSRF_DISABLED", "false").lower() == "true":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator CSRF enforcement is required")
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator CSRF token missing or invalid")
