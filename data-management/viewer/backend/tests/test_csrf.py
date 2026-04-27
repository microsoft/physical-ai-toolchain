"""Unit tests for CSRF double-submit cookie validation."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from src.api.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    generate_csrf_token,
    require_csrf_token,
)
from tests.conftest import make_asgi_request


def _csrf_request(
    method: str,
    path: str = "/api/datasets",
    cookie: str | None = None,
    header: str | None = None,
):
    headers: dict[str, str] = {}
    if cookie is not None:
        headers["cookie"] = f"{CSRF_COOKIE_NAME}={cookie}"
    if header is not None:
        headers[CSRF_HEADER_NAME] = header
    return make_asgi_request(method, path, headers=headers or None)


class TestGenerateCsrfToken:
    def test_token_is_hex_and_unique(self) -> None:
        a = generate_csrf_token()
        b = generate_csrf_token()
        assert a != b
        assert len(a) == 64
        assert int(a, 16) >= 0


class TestRequireCsrfToken:
    @pytest.fixture(autouse=True)
    def _enable_csrf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")

    def test_safe_method_passes_without_token(self) -> None:
        async def _run() -> None:
            await require_csrf_token(_csrf_request("GET"))
        asyncio.run(_run())

    def test_exempt_path_passes(self) -> None:
        async def _run() -> None:
            await require_csrf_token(_csrf_request("POST", path="/api/csrf-token"))
            await require_csrf_token(_csrf_request("POST", path="/health"))
        asyncio.run(_run())

    def test_matching_tokens_pass(self) -> None:
        async def _run() -> None:
            token = generate_csrf_token()
            await require_csrf_token(_csrf_request("POST", cookie=token, header=token))
        asyncio.run(_run())

    def test_missing_cookie_rejected(self) -> None:
        async def _run() -> None:
            with pytest.raises(HTTPException) as exc_info:
                await require_csrf_token(_csrf_request("POST", header="abc"))
            assert exc_info.value.status_code == 403
        asyncio.run(_run())

    def test_missing_header_rejected(self) -> None:
        async def _run() -> None:
            with pytest.raises(HTTPException) as exc_info:
                await require_csrf_token(_csrf_request("POST", cookie="abc"))
            assert exc_info.value.status_code == 403
        asyncio.run(_run())

    def test_mismatched_tokens_rejected(self) -> None:
        async def _run() -> None:
            with pytest.raises(HTTPException) as exc_info:
                await require_csrf_token(_csrf_request("PATCH", cookie="aaa", header="bbb"))
            assert exc_info.value.status_code == 403
        asyncio.run(_run())

    def test_bypass_when_auth_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _run() -> None:
            monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "TRUE")
            await require_csrf_token(_csrf_request("DELETE"))
        asyncio.run(_run())
