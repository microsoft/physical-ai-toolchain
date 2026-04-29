"""Unit tests for authentication providers and dependencies."""

from __future__ import annotations

import asyncio
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
)
from tests.conftest import make_asgi_request


@pytest.fixture(autouse=True)
def _reset_provider():
    reset_auth_provider()
    yield
    reset_auth_provider()


class TestApiKeyProvider:
    def test_valid_key_returns_user(self):
        provider = ApiKeyProvider("secret")
        result = asyncio.run(
            provider.authenticate(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "secret"}))
        )
        assert result is not None
        assert result["auth_method"] == "apikey"

    def test_wrong_key_returns_none(self):
        provider = ApiKeyProvider("secret")
        assert (
            asyncio.run(provider.authenticate(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "wrong"})))
            is None
        )

    def test_missing_header_returns_none(self):
        provider = ApiKeyProvider("secret")
        assert asyncio.run(provider.authenticate(make_asgi_request("POST", "/api/x"))) is None

    def test_empty_expected_key_rejects_all(self):
        provider = ApiKeyProvider("")
        assert (
            asyncio.run(provider.authenticate(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "anything"})))
            is None
        )

    def test_www_authenticate_header(self):
        assert "ApiKey" in ApiKeyProvider("k").www_authenticate


class TestEasyAuthProvider:
    def test_decodes_principal(self):
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
        result = asyncio.run(
            provider.authenticate(make_asgi_request("POST", "/api/x", headers={"X-MS-CLIENT-PRINCIPAL": encoded}))
        )
        assert result == {
            "sub": "user-1",
            "name": "Alice",
            "roles": ["admin", "viewer"],
            "auth_method": "easy_auth",
        }

    def test_missing_principal_returns_none(self):
        assert asyncio.run(EasyAuthProvider().authenticate(make_asgi_request("POST", "/api/x"))) is None

    def test_invalid_base64_returns_none(self):
        result = asyncio.run(
            EasyAuthProvider().authenticate(
                make_asgi_request("POST", "/api/x", headers={"X-MS-CLIENT-PRINCIPAL": "not-valid-base64!!!"})
            )
        )
        assert result is None

    def test_invalid_json_payload_returns_none(self):
        encoded = base64.b64encode(b"not-json").decode()
        result = asyncio.run(
            EasyAuthProvider().authenticate(
                make_asgi_request("POST", "/api/x", headers={"X-MS-CLIENT-PRINCIPAL": encoded})
            )
        )
        assert result is None

    def test_missing_claims_yields_blank_identity(self):
        encoded = base64.b64encode(json.dumps({}).encode()).decode()
        result = asyncio.run(
            EasyAuthProvider().authenticate(
                make_asgi_request("POST", "/api/x", headers={"X-MS-CLIENT-PRINCIPAL": encoded})
            )
        )
        assert result == {"sub": "", "name": "", "roles": [], "auth_method": "easy_auth"}

    def test_www_authenticate_header(self):
        assert "EasyAuth" in EasyAuthProvider().www_authenticate


class TestJwtProvider:
    def test_missing_bearer_returns_none(self):
        provider = JwtProvider("https://example/jwks", "aud", "iss")
        assert asyncio.run(provider.authenticate(make_asgi_request("POST", "/api/x"))) is None
        assert (
            asyncio.run(
                provider.authenticate(make_asgi_request("POST", "/api/x", headers={"Authorization": "Basic abc"}))
            )
            is None
        )

    def test_valid_token_returns_payload(self, monkeypatch: pytest.MonkeyPatch):
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
        result = asyncio.run(
            provider.authenticate(make_asgi_request("POST", "/api/x", headers={"Authorization": "Bearer my-token"}))
        )
        assert result == {"sub": "abc", "aud": "aud"}
        fake_jwt.decode.assert_called_once()
        # JWKS client is cached on the provider after first use.
        result2 = asyncio.run(
            provider.authenticate(make_asgi_request("POST", "/api/x", headers={"Authorization": "Bearer my-token"}))
        )
        assert result2 == {"sub": "abc", "aud": "aud"}
        fake_jwt.PyJWKClient.assert_called_once()

    def test_decode_error_returns_none(self, monkeypatch: pytest.MonkeyPatch):
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
        result = asyncio.run(
            provider.authenticate(make_asgi_request("POST", "/api/x", headers={"Authorization": "Bearer my-token"}))
        )
        assert result is None

    def test_missing_pyjwt_raises_runtime_error(self, monkeypatch: pytest.MonkeyPatch):
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
            asyncio.run(
                provider.authenticate(make_asgi_request("POST", "/api/x", headers={"Authorization": "Bearer t"}))
            )

    def test_www_authenticate_header(self):
        assert "Bearer" in JwtProvider("u", "a", "i").www_authenticate


class TestProviderSelection:
    """Validate provider selection through the public ``require_auth`` dependency."""

    @staticmethod
    def _expect_challenge(scheme: str) -> None:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_auth(make_asgi_request("POST", "/api/x")))
        assert exc_info.value.status_code == 401
        assert scheme in exc_info.value.headers.get("WWW-Authenticate", "")

    def test_default_is_apikey(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.delenv("DATAVIEWER_AUTH_PROVIDER", raising=False)
        monkeypatch.setenv("DATAVIEWER_API_KEY", "k")
        self._expect_challenge("ApiKey")

    def test_apikey_without_env_logs_warning(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.delenv("DATAVIEWER_API_KEY", raising=False)
        with caplog.at_level("WARNING", logger="src.api.auth"):
            self._expect_challenge("ApiKey")
        assert any("DATAVIEWER_API_KEY" in r.message for r in caplog.records)

    def test_unknown_falls_back_to_apikey(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "bogus")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "k")
        with caplog.at_level("ERROR", logger="src.api.auth"):
            self._expect_challenge("ApiKey")
        assert any("Unknown DATAVIEWER_AUTH_PROVIDER" in r.message for r in caplog.records)

    def test_easy_auth_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "easy_auth")
        self._expect_challenge("EasyAuth")

    def test_azure_ad_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "azure_ad")
        monkeypatch.setenv("DATAVIEWER_AZURE_TENANT_ID", "tenant")
        monkeypatch.setenv("DATAVIEWER_AZURE_CLIENT_ID", "client")
        self._expect_challenge("Bearer")

    def test_auth0_selection(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "auth0")
        monkeypatch.setenv("DATAVIEWER_AUTH0_DOMAIN", "x.auth0.com")
        monkeypatch.setenv("DATAVIEWER_AUTH0_AUDIENCE", "aud")
        self._expect_challenge("Bearer")


class TestRequireAuth:
    def test_bypass_when_disabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
        assert asyncio.run(require_auth(make_asgi_request("POST", "/api/x"))) is None

    def test_failure_raises_401_with_header(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "right")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "wrong"})))
        assert exc_info.value.status_code == 401
        assert "WWW-Authenticate" in exc_info.value.headers

    def test_failure_logs_unknown_client_when_missing(self, monkeypatch: pytest.MonkeyPatch):
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
        with pytest.raises(HTTPException):
            asyncio.run(require_auth(Request(scope)))

    def test_success_returns_user(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "right")
        user = asyncio.run(require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "right"})))
        assert user is not None and user["auth_method"] == "apikey"


class TestRequireRole:
    def test_bypass_when_user_none(self):
        dep = require_role("admin")
        assert asyncio.run(dep(user=None)) is None

    def test_role_present_passes(self):
        dep = require_role("admin")
        user = {"roles": ["admin", "viewer"]}
        assert asyncio.run(dep(user=user)) is user

    def test_missing_role_raises_403(self):
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(dep(user={"roles": ["viewer"]}))
        assert exc_info.value.status_code == 403

    def test_missing_roles_claim_raises_403(self):
        dep = require_role("admin")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(dep(user={}))
        assert exc_info.value.status_code == 403


class TestResetProvider:
    def test_reset_picks_up_new_configuration(self, monkeypatch: pytest.MonkeyPatch):
        """``reset_auth_provider`` clears the cached singleton so subsequent
        ``require_auth`` calls observe updated configuration."""
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
        monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
        monkeypatch.setenv("DATAVIEWER_API_KEY", "first")

        user = asyncio.run(require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "first"})))
        assert user is not None and user["auth_method"] == "apikey"

        monkeypatch.setenv("DATAVIEWER_API_KEY", "second")
        user = asyncio.run(require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "first"})))
        assert user is not None

        reset_auth_provider()
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "first"})))
        assert exc_info.value.status_code == 401
        user = asyncio.run(require_auth(make_asgi_request("POST", "/api/x", headers={"X-API-Key": "second"})))
        assert user is not None
