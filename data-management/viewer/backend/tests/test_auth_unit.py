"""Unit tests for authentication providers and dependencies."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from src.api import auth as auth_mod
from src.api.auth import (
    ApiKeyProvider,
    EasyAuthProvider,
    JwtProvider,
    _build_provider,
    require_auth,
    require_role,
    reset_auth_provider,
)


def _make_request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/x",
        "raw_path": b"/api/x",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "app": FastAPI(),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _reset_provider():
    reset_auth_provider()
    yield
    reset_auth_provider()


class TestApiKeyProvider:
    @pytest.mark.asyncio
    async def test_valid_key_returns_user(self):
        provider = ApiKeyProvider("secret")
        result = await provider.authenticate(_make_request({"X-API-Key": "secret"}))
        assert result is not None
        assert result["auth_method"] == "apikey"

    @pytest.mark.asyncio
    async def test_wrong_key_returns_none(self):
        provider = ApiKeyProvider("secret")
        assert await provider.authenticate(_make_request({"X-API-Key": "wrong"})) is None

    @pytest.mark.asyncio
    async def test_missing_header_returns_none(self):
        provider = ApiKeyProvider("secret")
        assert await provider.authenticate(_make_request()) is None

    @pytest.mark.asyncio
    async def test_empty_expected_key_rejects_all(self):
        provider = ApiKeyProvider("")
        assert await provider.authenticate(_make_request({"X-API-Key": "anything"})) is None

    def test_www_authenticate_header(self):
        assert "ApiKey" in ApiKeyProvider("k").www_authenticate


class TestEasyAuthProvider:
    @pytest.mark.asyncio
    async def test_decodes_principal(self):
        principal = {
            "claims": [
                {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "user-1"},
                {"typ": "name", "val": "Alice"},
                {"typ": "roles", "val": "admin"},
                {"typ": "roles", "val": "viewer"},
            ]
        }
        encoded = base64.b64encode(json.dumps(principal).encode()).decode()
        provider = EasyAuthProvider()
        result = await provider.authenticate(_make_request({"X-MS-CLIENT-PRINCIPAL": encoded}))
        assert result == {
            "sub": "user-1",
            "name": "Alice",
            "roles": ["admin", "viewer"],
            "auth_method": "easy_auth",
        }

    @pytest.mark.asyncio
    async def test_missing_principal_returns_none(self):
        assert await EasyAuthProvider().authenticate(_make_request()) is None

    @pytest.mark.asyncio
    async def test_invalid_base64_returns_none(self):
        result = await EasyAuthProvider().authenticate(_make_request({"X-MS-CLIENT-PRINCIPAL": "not-valid-base64!!!"}))
        assert result is None

    def test_www_authenticate_header(self):
        assert "EasyAuth" in EasyAuthProvider().www_authenticate


class TestJwtProvider:
    @pytest.mark.asyncio
    async def test_missing_bearer_returns_none(self):
        provider = JwtProvider("https://example/jwks", "aud", "iss")
        assert await provider.authenticate(_make_request()) is None
        assert await provider.authenticate(_make_request({"Authorization": "Basic abc"})) is None

    def test_www_authenticate_header(self):
        assert "Bearer" in JwtProvider("u", "a", "i").www_authenticate


class TestProviderFactory:
    def test_default_is_apikey(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("DATAVIEWER_AUTH_PROVIDER", raising=False)
        monkeypatch.setenv("DATAVIEWER_API_KEY", "k")
        assert isinstance(_build_provider(), ApiKeyProvider)

    def test_unknown_falls_back_to_apikey(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "bogus")
        assert isinstance(_build_provider(), ApiKeyProvider)

    def test_easy_auth_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "easy_auth")
        assert isinstance(_build_provider(), EasyAuthProvider)

    def test_azure_ad_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "azure_ad")
        monkeypatch.setenv("DATAVIEWER_AZURE_TENANT_ID", "tenant")
        monkeypatch.setenv("DATAVIEWER_AZURE_CLIENT_ID", "client")
        provider = _build_provider()
        assert isinstance(provider, JwtProvider)

    def test_auth0_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "auth0")
        monkeypatch.setenv("DATAVIEWER_AUTH0_DOMAIN", "x.auth0.com")
        monkeypatch.setenv("DATAVIEWER_AUTH0_AUDIENCE", "aud")
        assert isinstance(_build_provider(), JwtProvider)


class TestRequireAuth:
    @pytest.mark.asyncio
    async def test_bypass_when_disabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
        assert await require_auth(_make_request()) is None

    @pytest.mark.asyncio
    async def test_failure_raises_401_with_header(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "right")
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(_make_request({"X-API-Key": "wrong"}))
        assert exc_info.value.status_code == 401
        assert "WWW-Authenticate" in exc_info.value.headers

    @pytest.mark.asyncio
    async def test_success_returns_user(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "right")
        user = await require_auth(_make_request({"X-API-Key": "right"}))
        assert user is not None and user["auth_method"] == "apikey"


class TestRequireRole:
    @pytest.mark.asyncio
    async def test_bypass_when_user_none(self):
        dep = require_role("admin")
        result = await dep(user=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_role_present_passes(self):
        dep = require_role("admin")
        user = {"roles": ["admin", "viewer"]}
        assert await dep(user=user) is user

    @pytest.mark.asyncio
    async def test_missing_role_raises_403(self):
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc_info:
            await dep(user={"roles": ["viewer"]})
        assert exc_info.value.status_code == 403


class TestResetProvider:
    def test_resets_singleton(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "k")
        first = auth_mod._get_provider()
        reset_auth_provider()
        second = auth_mod._get_provider()
        assert first is not second
