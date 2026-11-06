"""Loopback-only managed-identity proof supplier for frontend nginx."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from azure.core.credentials import AccessToken
from azure.identity.aio import ManagedIdentityCredential
from fastapi import FastAPI, Response

TOKEN_HEADER = "X-Dataviewer-Caller-Token"
REFRESH_AHEAD_SECONDS = 300
SAFETY_SECONDS = 60
RETRY_SECONDS = 10


def create_app(
    *,
    credential_factory: Callable[..., Any] = ManagedIdentityCredential,
    clock: Callable[[], float] = time.time,
    acquisition_timeout: float = 5,
) -> FastAPI:
    """Create a storage-independent helper; invalid identity configuration fails startup."""
    client_id = os.environ.get("DATAVIEWER_CALLER_CLIENT_ID", "")
    api_id = os.environ.get("DATAVIEWER_AZURE_CLIENT_ID", "")
    try:
        if any(str(UUID(value)) != value for value in (client_id, api_id)):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("Invalid caller helper configuration") from None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        credential = credential_factory(client_id=client_id, logging_enable=False)
        app.state.credential = credential
        try:
            yield
        finally:
            await credential.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    lock = asyncio.Lock()
    token = None
    retry_at = 0.0

    async def usable_token() -> AccessToken | None:
        nonlocal token, retry_at
        async with lock:
            now = clock()
            if (token is None or token.expires_on - now <= REFRESH_AHEAD_SECONDS) and now >= retry_at:
                retry_at = now + RETRY_SECONDS
                try:
                    async with asyncio.timeout(acquisition_timeout):
                        candidate = await app.state.credential.get_token(f"api://{api_id}/.default")
                    if (
                        isinstance(candidate.token, str)
                        and 0 < len(candidate.token) <= 8192
                        and candidate.token.isascii()
                        and not any(character.isspace() or character == "," for character in candidate.token)
                        and candidate.expires_on > clock() + SAFETY_SECONDS
                    ):
                        token = candidate
                except Exception:
                    logging.getLogger(__name__).warning("Caller token acquisition unavailable")
            return token if token is not None and token.expires_on > clock() + SAFETY_SECONDS else None

    @app.get("/health")
    async def health() -> Response:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    @app.get("/ready")
    async def ready() -> Response:
        return Response(status_code=204 if await usable_token() else 503, headers={"Cache-Control": "no-store"})

    @app.get("/token")
    async def proof() -> Response:
        current = await usable_token()
        headers = {"Cache-Control": "no-store"}
        if current is not None:
            headers[TOKEN_HEADER] = current.token
        return Response(status_code=204 if current else 503, headers=headers)

    return app


def main() -> None:
    """Start only on the fixed shared-container loopback endpoint."""
    import uvicorn

    logging.getLogger("azure").setLevel(logging.CRITICAL)
    logging.getLogger("msal").setLevel(logging.CRITICAL)
    uvicorn.run(create_app(), host="127.0.0.1", port=8090, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
