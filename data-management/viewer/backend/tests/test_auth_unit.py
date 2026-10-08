"""Unit tests for authentication providers and dependencies."""

from __future__ import annotations

import base64
import json
import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.auth import (
    ApiKeyProvider,
    EasyAuthProvider,
    JwtProvider,
    require_auth,
    require_role,
    reset_auth_provider,
    resolve_principal_context,
)
from tests.conftest import make_asgi_request


def _easy_auth_principal(*, subject: str = "user-1") -> str:
    principal = {
        "claims": [
            {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": subject},
            {"typ": "name", "val": "Alice"},
            {"typ": "roles", "val": "admin"},
            {"typ": "roles", "val": "viewer"},
        ]
    }
    return base64.b64encode(json.dumps(principal).encode()).decode()


@pytest.fixture(autouse=True)
def _reset_provider():
    reset_auth_provider()
    yield
    reset_auth_provider()


class TestApiKeyProvider:
    @pytest.mark.asyncio
    async def test_valid_key_returns_user(self):
        provider = ApiKeyProvider("secret")
        result = await provider.authenticate(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "secret"}))
        assert result == {"sub": "api-key-client", "auth_method": "apikey"}

    @pytest.mark.asyncio
    async def test_wrong_key_returns_none(self):
        provider = ApiKeyProvider("secret")
        result = await provider.authenticate(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "wrong"}))
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_header_returns_none(self):
        provider = ApiKeyProvider("secret")
        assert await provider.authenticate(make_asgi_request("POST", "/api/x")) is None

    @pytest.mark.asyncio
    async def test_empty_expected_key_rejects_all(self):
        provider = ApiKeyProvider("")
        result = await provider.authenticate(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "anything"}))
        assert result is None

    def test_www_authenticate_header(self):
        assert ApiKeyProvider("k").www_authenticate == 'ApiKey realm="DataViewer API"'


class TestEasyAuthProvider:
    @pytest.mark.asyncio
    async def test_bound_principal_is_authenticated(self):
        provider = EasyAuthProvider("proxy-secret")
        result = await provider.authenticate(
            make_asgi_request(
                "POST",
                "/api/x",
                headers={
                    "X-MS-CLIENT-PRINCIPAL": _easy_auth_principal(),
                    "X-Dataviewer-Proxy-Key": "proxy-secret",
                },
            )
        )
        assert result == {
            "sub": "user-1",
            "name": "Alice",
            "roles": ["admin", "viewer"],
            "auth_method": "easy_auth",
        }

    @pytest.mark.parametrize(
        "headers",
        [
            {"X-MS-CLIENT-PRINCIPAL": _easy_auth_principal()},
            {
                "X-MS-CLIENT-PRINCIPAL": _easy_auth_principal(),
                "X-Dataviewer-Proxy-Key": "wrong",
            },
            {
                "X-MS-CLIENT-PRINCIPAL": _easy_auth_principal(),
                "X-Dataviewer-Proxy-Key": "é",
            },
        ],
    )
    @pytest.mark.asyncio
    async def test_unbound_principal_is_rejected(self, headers: dict[str, str]):
        provider = EasyAuthProvider("proxy-secret")
        assert await provider.authenticate(make_asgi_request("POST", "/api/x", headers=headers)) is None

    @pytest.mark.parametrize(
        "principal",
        [
            "not-valid-base64!!!",
            base64.b64encode(b"not-json").decode(),
            base64.b64encode(json.dumps({}).encode()).decode(),
            _easy_auth_principal(subject=" "),
        ],
    )
    @pytest.mark.asyncio
    async def test_malformed_or_incomplete_principal_is_rejected(self, principal: str):
        provider = EasyAuthProvider("proxy-secret")
        result = await provider.authenticate(
            make_asgi_request(
                "POST",
                "/api/x",
                headers={
                    "X-MS-CLIENT-PRINCIPAL": principal,
                    "X-Dataviewer-Proxy-Key": "proxy-secret",
                },
            )
        )
        assert result is None

    def test_empty_proxy_key_is_rejected_at_initialization(self):
        with pytest.raises(ValueError, match="DATAVIEWER_PROXY_KEY"):
            EasyAuthProvider("")

    def test_www_authenticate_header(self):
        assert "EasyAuth" in EasyAuthProvider("proxy-secret").www_authenticate


class TestJwtProvider:
    @pytest.mark.asyncio
    async def test_missing_bearer_returns_none(self):
        provider = JwtProvider("https://example/jwks", "aud", "iss")
        assert await provider.authenticate(make_asgi_request("POST", "/api/x")) is None
        assert (
            await provider.authenticate(make_asgi_request("POST", "/api/x", headers={"Authorization": "Basic abc"}))
            is None
        )

    @pytest.mark.asyncio
    async def test_valid_token_returns_payload(self, monkeypatch: pytest.MonkeyPatch):
        signing_key = MagicMock()
        signing_key.key = "fake-key"
        jwks_client = MagicMock()
        jwks_client.get_signing_key_from_jwt.return_value = signing_key

        fake_jwt = types.ModuleType("jwt")
        fake_jwt.PyJWKClient = MagicMock(return_value=jwks_client)
        fake_jwt.PyJWTError = Exception
        fake_jwt.decode = MagicMock(return_value={"sub": "abc", "aud": "aud"})
        monkeypatch.setitem(sys.modules, "jwt", fake_jwt)

        provider = JwtProvider("https://example/jwks", "aud", "iss")
        result = await provider.authenticate(
            make_asgi_request("POST", "/api/x", headers={"Authorization": "Bearer my-token"})
        )
        assert result == {"sub": "abc", "aud": "aud", "auth_method": "azure_ad"}
        fake_jwt.decode.assert_called_once()
        # JWKS client is cached on the provider after first use.
        result2 = await provider.authenticate(
            make_asgi_request("POST", "/api/x", headers={"Authorization": "Bearer my-token"})
        )
        assert result2 == {"sub": "abc", "aud": "aud", "auth_method": "azure_ad"}
        fake_jwt.PyJWKClient.assert_called_once()
        assert fake_jwt.decode.call_count == 2

    @pytest.mark.asyncio
    async def test_decode_error_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        class FakeJWTError(Exception):
            pass

        signing_key = MagicMock()
        signing_key.key = "fake-key"
        jwks_client = MagicMock()
        jwks_client.get_signing_key_from_jwt.return_value = signing_key

        fake_jwt = types.ModuleType("jwt")
        fake_jwt.PyJWKClient = MagicMock(return_value=jwks_client)
        fake_jwt.PyJWTError = FakeJWTError
        fake_jwt.decode = MagicMock(side_effect=FakeJWTError("bad token"))
        monkeypatch.setitem(sys.modules, "jwt", fake_jwt)

        provider = JwtProvider("https://example/jwks", "aud", "iss")
        result = await provider.authenticate(
            make_asgi_request("POST", "/api/x", headers={"Authorization": "Bearer my-token"})
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_pyjwt_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch):
        # Block `import jwt` by inserting a finder that raises ImportError.
        monkeypatch.delitem(sys.modules, "jwt", raising=False)

        class _BlockJwt:
            def find_module(self, name, path=None):
                return self if name == "jwt" else None

            def load_module(self, name):
                raise ImportError("no jwt for you")

            def find_spec(self, name, path, target=None):
                if name == "jwt":
                    raise ImportError("no jwt for you")
                return None

        blocker = _BlockJwt()
        monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])

        provider = JwtProvider("https://example/jwks", "aud", "iss")
        with pytest.raises(RuntimeError, match="pyjwt"):
            await provider.authenticate(make_asgi_request("POST", "/api/x", headers={"Authorization": "Bearer t"}))

    def test_www_authenticate_header(self):
        assert "Bearer" in JwtProvider("u", "a", "i").www_authenticate


class TestProviderSelection:
    """Validate provider selection through the public ``require_auth`` dependency."""

    pytestmark = pytest.mark.asyncio

    @staticmethod
    async def _expect_challenge(scheme: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(make_asgi_request("POST", "/api/x"))
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Authentication required"
        assert exc_info.value.headers["WWW-Authenticate"].startswith(scheme)

    async def test_default_is_apikey(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.delenv("DATAVIEWER_AUTH_PROVIDER", raising=False)
        monkeypatch.setenv("DATAVIEWER_API_KEY", "k")
        await self._expect_challenge("ApiKey")

    async def test_apikey_without_env_logs_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.delenv("DATAVIEWER_API_KEY", raising=False)
        with caplog.at_level("WARNING", logger="src.api.auth"):
            await self._expect_challenge("ApiKey")
        assert "DATAVIEWER_API_KEY is not set; all API-key auth will fail" in caplog.messages

    async def test_unknown_provider_fails_closed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "bogus")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "k")
        with pytest.raises(ValueError, match="DATAVIEWER_AUTH_PROVIDER"):
            await require_auth(make_asgi_request("POST", "/api/x"))

    async def test_easy_auth_selection_requires_proxy_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "easy_auth")
        monkeypatch.delenv("DATAVIEWER_PROXY_KEY", raising=False)
        with pytest.raises(ValueError, match="DATAVIEWER_PROXY_KEY"):
            await require_auth(make_asgi_request("POST", "/api/x"))

    async def test_easy_auth_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "easy_auth")
        monkeypatch.setenv("DATAVIEWER_PROXY_KEY", "proxy-secret")
        await self._expect_challenge("EasyAuth")

    async def test_azure_ad_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "azure_ad")
        monkeypatch.setenv("DATAVIEWER_AZURE_TENANT_ID", "tenant")
        monkeypatch.setenv("DATAVIEWER_AZURE_CLIENT_ID", "client")
        await self._expect_challenge("Bearer")

    async def test_auth0_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "auth0")
        monkeypatch.setenv("DATAVIEWER_AUTH0_DOMAIN", "x.auth0.com")
        monkeypatch.setenv("DATAVIEWER_AUTH0_AUDIENCE", "aud")
        await self._expect_challenge("Bearer")


class TestRequireAuth:
    pytestmark = pytest.mark.asyncio

    async def test_bypass_when_disabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
        assert await require_auth(make_asgi_request("POST", "/api/x")) is None

    async def test_failure_raises_401_with_header(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "right")
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "wrong"}))
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Authentication required"
        assert exc_info.value.headers["WWW-Authenticate"] == 'ApiKey realm="DataViewer API"'

    @pytest.mark.parametrize("proxy_key", [None, "wrong", "é"])
    async def test_forged_easy_auth_header_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        proxy_key: str | None,
    ):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "easy_auth")
        monkeypatch.setenv("DATAVIEWER_PROXY_KEY", "proxy-secret")
        headers = {"X-MS-CLIENT-PRINCIPAL": _easy_auth_principal()}
        if proxy_key is not None:
            headers["X-Dataviewer-Proxy-Key"] = proxy_key

        with pytest.raises(HTTPException) as exc_info:
            await require_auth(make_asgi_request("POST", "/api/x", headers=headers))

        assert exc_info.value.status_code == 401

    async def test_failure_logs_unknown_client_when_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        # Build a request with no client tuple to exercise the "unknown" branch.
        from fastapi import FastAPI
        from starlette.requests import Request

        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "right")
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/x",
            "raw_path": b"/api/x",
            "query_string": b"",
            "headers": [],
            "client": None,
            "app": FastAPI(),
            "scheme": "http",
            "server": ("testserver", 80),
        }
        with (
            caplog.at_level("WARNING", logger="src.api.auth"),
            pytest.raises(HTTPException) as exc_info,
        ):
            await require_auth(Request(scope))
        assert exc_info.value.status_code == 401
        assert "Authentication failed: method=POST path=/api/x client=unknown" in caplog.messages

    async def test_success_returns_user(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "right")
        user = await require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "right"}))
        assert user == {"sub": "api-key-client", "auth_method": "apikey"}


class TestPrincipalContext:
    def test_given_authenticated_subject_when_resolved_then_scope_is_stable_and_opaque(self):
        user = {"sub": "alice@example.com", "auth_method": "azure_ad"}

        first = resolve_principal_context(user)
        second = resolve_principal_context(user)

        assert first == second
        assert first.auth_mode == "azure_ad"
        assert "alice" not in first.scope_id

    def test_given_different_providers_when_resolved_then_scopes_are_distinct(self):
        azure = resolve_principal_context({"sub": "shared-subject", "auth_method": "azure_ad"})
        easy_auth = resolve_principal_context({"sub": "shared-subject", "auth_method": "easy_auth"})

        assert azure.scope_id != easy_auth.scope_id
        assert easy_auth.auth_mode == "easy_auth"

    def test_given_disabled_auth_when_resolved_then_local_scope_is_non_personal(self):
        context = resolve_principal_context(None)

        assert context.auth_mode == "local"
        assert context.scope_id

    @pytest.mark.parametrize(
        "user",
        [
            {"sub": "subject", "auth_method": "unsupported"},
            {"sub": "", "auth_method": "apikey"},
        ],
    )
    def test_given_invalid_identity_claims_when_resolved_then_request_is_rejected(self, user):
        with pytest.raises(HTTPException) as exc_info:
            resolve_principal_context(user)

        assert exc_info.value.status_code == 401


class TestRequireRole:
    pytestmark = pytest.mark.asyncio

    async def test_bypass_when_user_none(self):
        dep = require_role("admin")
        assert await dep(user=None) is None

    async def test_role_present_passes(self):
        dep = require_role("admin")
        user = {"roles": ["admin", "viewer"]}
        assert await dep(user=user) is user

    async def test_missing_role_raises_403(self):
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc_info:
            await dep(user={"roles": ["viewer"]})
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Insufficient permissions"

    async def test_missing_roles_claim_raises_403(self):
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc_info:
            await dep(user={})
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Insufficient permissions"


class TestResetProvider:
    pytestmark = pytest.mark.asyncio

    async def test_reset_picks_up_new_configuration(self, monkeypatch: pytest.MonkeyPatch):
        """``reset_auth_provider`` clears the cached singleton so subsequent
        ``require_auth`` calls observe updated configuration."""
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "first")

        user = await require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "first"}))
        assert user == {"sub": "api-key-client", "auth_method": "apikey"}

        monkeypatch.setenv("DATAVIEWER_API_KEY", "second")
        user = await require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "first"}))
        assert user == {"sub": "api-key-client", "auth_method": "apikey"}

        reset_auth_provider()
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "first"}))
        assert exc_info.value.status_code == 401
        user = await require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "second"}))
        assert user == {"sub": "api-key-client", "auth_method": "apikey"}
