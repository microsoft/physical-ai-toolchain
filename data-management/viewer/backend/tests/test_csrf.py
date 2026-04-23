"""Unit tests for CSRF double-submit cookie validation."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from src.api.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    generate_csrf_token,
    require_csrf_token,
)


def _make_request(
    method: str,
    path: str = "/api/datasets",
    cookie: str | None = None,
    header: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if cookie is not None:
        headers.append((b"cookie", f"{CSRF_COOKIE_NAME}={cookie}".encode()))
    if header is not None:
        headers.append((CSRF_HEADER_NAME.lower().encode(), header.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "app": FastAPI(),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


class TestGenerateCsrfToken:
    def test_token_is_hex_and_unique(self):
        a = generate_csrf_token()
        b = generate_csrf_token()
        assert a != b
        assert len(a) == 64
        int(a, 16)


class TestRequireCsrfToken:
    @pytest.fixture(autouse=True)
    def _enable_csrf(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")

    @pytest.mark.asyncio
    async def test_safe_method_passes_without_token(self):
        await require_csrf_token(_make_request("GET"))

    @pytest.mark.asyncio
    async def test_exempt_path_passes(self):
        await require_csrf_token(_make_request("POST", path="/api/csrf-token"))
        await require_csrf_token(_make_request("POST", path="/health"))

    @pytest.mark.asyncio
    async def test_matching_tokens_pass(self):
        token = generate_csrf_token()
        await require_csrf_token(_make_request("POST", cookie=token, header=token))

    @pytest.mark.asyncio
    async def test_missing_cookie_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf_token(_make_request("POST", header="abc"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_header_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf_token(_make_request("POST", cookie="abc"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_mismatched_tokens_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            await require_csrf_token(_make_request("PATCH", cookie="aaa", header="bbb"))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_bypass_when_auth_disabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "TRUE")
        await require_csrf_token(_make_request("DELETE"))
