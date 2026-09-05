"""Fail-closed authorization for hardware-capable operator operations."""

from __future__ import annotations

import ipaddress
import os
import secrets
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from ..auth import require_auth
from ..csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME


def _loopback_host(value: str) -> bool:
    hostname = value.rsplit("@", 1)[-1].split(":", 1)[0].strip("[]").lower()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _roles(user: dict[str, Any]) -> list[str]:
    raw = user.get("roles", [])
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [role for role in raw if isinstance(role, str)]
    return []


async def require_operator_access(
    request: Request,
    user: dict[str, Any] | None = Depends(require_auth),
) -> None:
    """Authorize access to the operator control surface."""
    if user is None:
        return
    if user.get("auth_method") == "apikey":
        expected = os.environ.get("DATAVIEWER_OPERATOR_API_KEY", "")
        supplied = request.headers.get("X-Operator-API-Key", "")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operator API key required",
            )
        return
    if "Operator" not in _roles(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator role required",
        )


async def require_hardware_access(
    request: Request,
    user: dict[str, Any] | None = Depends(require_auth),
) -> None:
    """Authorize a side-effect-free hardware readiness operation."""
    if os.environ.get("OPERATOR_ADAPTER_MODE", "disabled").lower() != "lerobot":
        return
    auth_disabled = os.environ.get("DATAVIEWER_AUTH_DISABLED", "false").lower() == "true"
    if auth_disabled:
        allowed = os.environ.get("OPERATOR_ALLOW_UNAUTHENTICATED_LOOPBACK", "false").lower() == "true"
        server_host = request.scope.get("server", ("", 0))[0]
        client_host = request.scope.get("client", ("", 0))[0]
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if not (
            allowed
            and _loopback_host(server_host)
            and _loopback_host(client_host)
            and _loopback_host(host)
            and (origin is None or _loopback_host(urlsplit(origin).hostname or ""))
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hardware access requires loopback authorization",
            )
        return

    if user is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hardware access denied")
    auth_method = user.get("auth_method")
    if auth_method == "apikey":
        expected = os.environ.get("DATAVIEWER_OPERATOR_API_KEY", "")
        general = os.environ.get("DATAVIEWER_API_KEY", "")
        supplied = request.headers.get("X-Operator-API-Key", "")
        if not expected or expected == general or not secrets.compare_digest(expected, supplied):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Operator API key required",
            )
        return
    if auth_method == "easy_auth" and os.environ.get("OPERATOR_TRUST_EASY_AUTH", "false").lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Easy Auth is not trusted for hardware access",
        )
    if "Operator" not in _roles(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")


async def require_operator_csrf(request: Request) -> None:
    """Require double-submit CSRF for operator mutations even in local mode."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if os.environ.get("OPERATOR_ADAPTER_MODE", "disabled").lower() != "lerobot":
        return
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token or not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator CSRF token missing or invalid",
        )
