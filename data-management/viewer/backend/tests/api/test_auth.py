"""Unit tests for authentication and CSRF middleware."""

import os

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(autouse=True)
def reset_auth_state(tmp_path):
    """Reset the auth provider singleton and set a valid DATA_DIR before each test."""
    from src.api import auth as auth_mod

    os.environ["DATA_DIR"] = str(tmp_path)
    auth_mod.reset_auth_provider()
    yield
    auth_mod.reset_auth_provider()
    os.environ.pop("DATA_DIR", None)


@pytest.fixture
def client_with_auth():
    """Test client with authentication enabled and API-key provider configured."""
    import src.api.services.dataset_service as ds_mod

    ds_mod._dataset_service = None
    original_disabled = os.environ.pop("DATAVIEWER_AUTH_DISABLED", None)
    os.environ["DATAVIEWER_AUTH_PROVIDER"] = "apikey"
    os.environ["DATAVIEWER_API_KEY"] = "test-secret-key"
    yield TestClient(app)
    if original_disabled is not None:
        os.environ["DATAVIEWER_AUTH_DISABLED"] = original_disabled
    else:
        os.environ.pop("DATAVIEWER_AUTH_DISABLED", None)
    os.environ.pop("DATAVIEWER_AUTH_PROVIDER", None)
    os.environ.pop("DATAVIEWER_API_KEY", None)
    ds_mod._dataset_service = None


@pytest.fixture
def client_auth_disabled():
    """Test client with auth disabled (DATAVIEWER_AUTH_DISABLED=true)."""
    import src.api.services.dataset_service as ds_mod

    ds_mod._dataset_service = None
    original = os.environ.get("DATAVIEWER_AUTH_DISABLED")
    os.environ["DATAVIEWER_AUTH_DISABLED"] = "true"
    yield TestClient(app)
    if original is not None:
        os.environ["DATAVIEWER_AUTH_DISABLED"] = original
    else:
        os.environ.pop("DATAVIEWER_AUTH_DISABLED", None)
    ds_mod._dataset_service = None


# ============================================================================
# CSRF token endpoint
# ============================================================================


class TestCsrfTokenEndpoint:
    def test_returns_token(self, client_auth_disabled):
        resp = client_auth_disabled.get("/api/csrf-token")
        assert resp.status_code == 200
        data = resp.json()
        assert "csrf_token" in data
        assert len(data["csrf_token"]) == 64  # 32 bytes as hex

    def test_sets_cookie(self, client_auth_disabled):
        resp = client_auth_disabled.get("/api/csrf-token")
        assert "csrf_token" in resp.cookies

    def test_cookie_matches_body(self, client_auth_disabled):
        resp = client_auth_disabled.get("/api/csrf-token")
        assert resp.cookies["csrf_token"] == resp.json()["csrf_token"]

    def test_each_call_returns_fresh_token(self, client_auth_disabled):
        t1 = client_auth_disabled.get("/api/csrf-token").json()["csrf_token"]
        t2 = client_auth_disabled.get("/api/csrf-token").json()["csrf_token"]
        assert t1 != t2


# ============================================================================
# Auth dependency - auth disabled
# ============================================================================


class TestAuthDisabled:
    def test_mutation_succeeds_without_credentials(self, client_auth_disabled):
        """When DATAVIEWER_AUTH_DISABLED=true, mutations require no auth or CSRF."""
        resp = client_auth_disabled.post("/api/datasets/nonexistent/episodes/0/annotations/auto")
        # Auth/CSRF checks were bypassed; request was processed (404 = dataset not found)
        assert resp.status_code not in (401, 403)

    def test_read_endpoint_accessible(self, client_auth_disabled):
        resp = client_auth_disabled.get("/api/datasets")
        assert resp.status_code == 200


# ============================================================================
# Auth dependency - API key provider
# ============================================================================


class TestApiKeyAuth:
    def _csrf_token(self, client: TestClient) -> str:
        resp = client.get("/api/csrf-token")
        return resp.json()["csrf_token"]

    def test_missing_api_key_returns_401(self, client_with_auth):
        token = self._csrf_token(client_with_auth)
        client_with_auth.cookies.set("csrf_token", token)
        resp = client_with_auth.post(
            "/api/datasets/nonexistent/episodes/0/annotations/auto",
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 401

    def test_401_includes_www_authenticate_header(self, client_with_auth):
        token = self._csrf_token(client_with_auth)
        client_with_auth.cookies.set("csrf_token", token)
        resp = client_with_auth.post(
            "/api/datasets/nonexistent/episodes/0/annotations/auto",
            headers={"X-CSRF-Token": token},
        )
        assert "WWW-Authenticate" in resp.headers

    def test_wrong_api_key_returns_401(self, client_with_auth):
        token = self._csrf_token(client_with_auth)
        client_with_auth.cookies.set("csrf_token", token)
        resp = client_with_auth.post(
            "/api/datasets/nonexistent/episodes/0/annotations/auto",
            headers={"X-API-Key": "wrong-key", "X-CSRF-Token": token},
        )
        assert resp.status_code == 401

    def test_valid_api_key_passes_auth(self, client_with_auth):
        token = self._csrf_token(client_with_auth)
        client_with_auth.cookies.set("csrf_token", token)
        resp = client_with_auth.post(
            "/api/datasets/nonexistent/episodes/0/annotations/auto",
            headers={"X-API-Key": "test-secret-key", "X-CSRF-Token": token},
        )
        # Auth and CSRF passed; request was processed (not 401 or 403)
        assert resp.status_code not in (401, 403)

    def test_read_endpoint_requires_auth(self, client_with_auth):
        """GET endpoints require authentication when auth is enabled."""
        resp = client_with_auth.get("/api/datasets")
        assert resp.status_code == 401

    def test_read_endpoint_accessible_with_key(self, client_with_auth):
        resp = client_with_auth.get("/api/datasets", headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 200

    def test_csrf_endpoint_accessible_without_key(self, client_with_auth):
        resp = client_with_auth.get("/api/csrf-token")
        assert resp.status_code == 200

    def test_health_accessible_without_key(self, client_with_auth):
        resp = client_with_auth.get("/health")
        assert resp.status_code == 200


# ============================================================================
# CSRF enforcement
# ============================================================================


class TestCsrfProtection:
    def test_missing_csrf_token_returns_403(self, client_with_auth):
        resp = client_with_auth.post(
            "/api/datasets/nonexistent/episodes/0/annotations/auto",
            headers={"X-API-Key": "test-secret-key"},
        )
        assert resp.status_code == 403

    def test_mismatched_csrf_token_returns_403(self, client_with_auth):
        token_resp = client_with_auth.get("/api/csrf-token")
        token = token_resp.json()["csrf_token"]
        client_with_auth.cookies.set("csrf_token", token)
        resp = client_with_auth.post(
            "/api/datasets/nonexistent/episodes/0/annotations/auto",
            headers={"X-API-Key": "test-secret-key", "X-CSRF-Token": "wrong-token"},
        )
        assert resp.status_code == 403

    def test_valid_csrf_token_passes(self, client_with_auth):
        token_resp = client_with_auth.get("/api/csrf-token")
        token = token_resp.json()["csrf_token"]
        client_with_auth.cookies.set("csrf_token", token)
        resp = client_with_auth.post(
            "/api/datasets/nonexistent/episodes/0/annotations/auto",
            headers={"X-API-Key": "test-secret-key", "X-CSRF-Token": token},
        )
        # Auth and CSRF passed; request was processed (not 401 or 403)
        assert resp.status_code not in (401, 403)


# ============================================================================
# ApiKeyProvider unit tests
# ============================================================================


class TestApiKeyProvider:
    async def test_authenticate_valid_key(self):
        from unittest.mock import MagicMock

        from src.api.auth import ApiKeyProvider

        provider = ApiKeyProvider("my-secret")
        request = MagicMock()
        request.headers = {"X-API-Key": "my-secret"}
        result = await provider.authenticate(request)
        assert result is not None
        assert result["auth_method"] == "apikey"

    async def test_authenticate_wrong_key(self):
        from unittest.mock import MagicMock

        from src.api.auth import ApiKeyProvider

        provider = ApiKeyProvider("my-secret")
        request = MagicMock()
        request.headers = {"X-API-Key": "wrong"}
        result = await provider.authenticate(request)
        assert result is None

    async def test_authenticate_missing_key(self):
        from unittest.mock import MagicMock

        from src.api.auth import ApiKeyProvider

        provider = ApiKeyProvider("my-secret")
        request = MagicMock()
        request.headers = {}
        result = await provider.authenticate(request)
        assert result is None

    def test_www_authenticate_header(self):
        from src.api.auth import ApiKeyProvider

        provider = ApiKeyProvider("key")
        assert "ApiKey" in provider.www_authenticate


# ============================================================================
# EasyAuthProvider unit tests
# ============================================================================


class TestEasyAuthProvider:
    async def test_authenticate_valid_principal(self):
        import base64
        import json
        from unittest.mock import MagicMock

        from src.api.auth import EasyAuthProvider

        provider = EasyAuthProvider()
        claims = {
            "claims": [
                {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "user-123"},
                {"typ": "name", "val": "Test User"},
                {"typ": "roles", "val": "Dataviewer.Admin"},
            ]
        }
        encoded = base64.b64encode(json.dumps(claims).encode()).decode()
        request = MagicMock()
        request.headers = {"X-MS-CLIENT-PRINCIPAL": encoded}
        result = await provider.authenticate(request)
        assert result is not None
        assert result["auth_method"] == "easy_auth"
        assert "Dataviewer.Admin" in result["roles"]

    async def test_authenticate_missing_header(self):
        from unittest.mock import MagicMock

        from src.api.auth import EasyAuthProvider

        provider = EasyAuthProvider()
        request = MagicMock()
        request.headers = {}
        result = await provider.authenticate(request)
        assert result is None

    async def test_authenticate_invalid_base64(self):
        from unittest.mock import MagicMock

        from src.api.auth import EasyAuthProvider

        provider = EasyAuthProvider()
        request = MagicMock()
        request.headers = {"X-MS-CLIENT-PRINCIPAL": "not-valid-base64!!!"}
        result = await provider.authenticate(request)
        assert result is None

    def test_www_authenticate_header(self):
        from src.api.auth import EasyAuthProvider

        provider = EasyAuthProvider()
        assert "EasyAuth" in provider.www_authenticate


# ============================================================================
# generate_csrf_token
# ============================================================================


class TestGenerateCsrfToken:
    def test_returns_hex_string(self):
        from src.api.csrf import generate_csrf_token

        token = generate_csrf_token()
        assert isinstance(token, str)
        int(token, 16)  # must be valid hex

    def test_tokens_are_unique(self):
        from src.api.csrf import generate_csrf_token

        tokens = {generate_csrf_token() for _ in range(10)}
        assert len(tokens) == 10

    def test_token_length(self):
        from src.api.csrf import generate_csrf_token

        token = generate_csrf_token()
        assert len(token) == 64  # 32 bytes * 2 hex chars
