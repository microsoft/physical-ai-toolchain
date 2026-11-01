"""Unit tests for authentication and CSRF middleware."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.main import app
from tests.conftest import (
    CALLER_API_AUDIENCE,
    CALLER_CLIENT_ID,
    CALLER_OBJECT_ID,
    CALLER_TOKEN_HEADER,
    DEFAULT_PRINCIPAL,
    NAMEID_CLAIM,
    PRINCIPAL_HEADER,
    HostedCaller,
    encode_bytes,
    encode_principal,
    make_asgi_request,
    set_hosted_environment,
)


@pytest.mark.parametrize(
    "path",
    [
        "/api/datasets",
        "/api/csrf-token",
        "/api/auth/context",
        "/docs",
        "/openapi.json",
        "/health/anything",
        "/unknown",
        "/api/datasets/example/episodes/0/video/front",
        "/api/datasets/example/episodes/0/frames/0",
    ],
)
def test_given_hosted_mode_without_caller_when_requesting_any_route_then_denied(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")
    monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "easy_auth")
    monkeypatch.setenv("DATAVIEWER_AZURE_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("DATAVIEWER_AZURE_CLIENT_ID", "00000000-0000-0000-0000-000000000002")
    monkeypatch.setenv("DATAVIEWER_CALLER_OBJECT_ID", "00000000-0000-0000-0000-000000000003")
    monkeypatch.setenv("DATAVIEWER_CALLER_CLIENT_ID", "00000000-0000-0000-0000-000000000004")

    response = TestClient(app).get(path)

    assert response.status_code == 401


@pytest.fixture(autouse=True)
def reset_auth_state(tmp_path, monkeypatch):
    """Reset application state and use isolated local storage for each test."""
    from src.api import auth as auth_mod
    from src.api import config as config_mod
    from src.api import main as main_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    config_mod._app_config = None
    monkeypatch.setattr(main_mod, "_config", config_mod.load_config())
    auth_mod.reset_auth_provider()
    yield
    auth_mod.reset_auth_provider()
    config_mod._app_config = None


@pytest.fixture
def client_with_auth(monkeypatch):
    """Test client with authentication enabled and API-key provider configured."""
    import src.api.services.dataset_service as ds_mod

    ds_mod._dataset_service = None
    monkeypatch.delenv("DATAVIEWER_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
    monkeypatch.setenv("DATAVIEWER_API_KEY", "test-secret-key")
    yield TestClient(app)
    ds_mod._dataset_service = None


@pytest.fixture
def client_auth_disabled(monkeypatch):
    """Test client with auth disabled (DATAVIEWER_AUTH_DISABLED=true)."""
    import src.api.services.dataset_service as ds_mod

    ds_mod._dataset_service = None
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    yield TestClient(app)
    ds_mod._dataset_service = None


@pytest.fixture
def client_with_easy_auth(hosted_caller: HostedCaller):
    """Test client with signed frontend caller proof configured."""
    import src.api.services.dataset_service as ds_mod

    ds_mod._dataset_service = None
    yield TestClient(app)
    ds_mod._dataset_service = None


def _easy_auth_headers(*, proxy_key: str | None = "proxy-secret") -> dict[str, str]:
    principal = {
        "claims": [
            {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "user-123"},
            {"typ": "name", "val": "Test User"},
            {"typ": "roles", "val": "Dataviewer.Admin"},
        ]
    }
    headers = {"X-MS-CLIENT-PRINCIPAL": encode_principal(principal)}
    if proxy_key is not None:
        headers["X-Dataviewer-Proxy-Key"] = proxy_key
    return headers


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


class TestPrincipalContextEndpoint:
    def test_auth_disabled_returns_stable_non_personal_scope(self, client_auth_disabled):
        first = client_auth_disabled.get("/api/auth/context")
        second = client_auth_disabled.get("/api/auth/context")

        assert first.status_code == 200
        assert first.json() == second.json()
        assert first.json()["auth_mode"] == "local"
        assert first.json()["scope_id"].startswith("principal-")

    def test_api_key_returns_opaque_service_scope(self, client_with_auth):
        response = client_with_auth.get(
            "/api/auth/context",
            headers={"X-API-Key": "test-secret-key"},
        )

        assert response.status_code == 200
        assert response.json()["auth_mode"] == "apikey"
        assert "test-secret-key" not in response.json()["scope_id"]

    def test_missing_api_key_is_rejected(self, client_with_auth):
        response = client_with_auth.get("/api/auth/context")

        assert response.status_code == 401

    def test_proxy_bound_easy_auth_returns_opaque_user_scope(self, client_with_easy_auth, hosted_caller):
        response = client_with_easy_auth.get("/api/auth/context", headers=hosted_caller.request_headers())

        assert response.status_code == 200
        assert response.json()["auth_mode"] == "easy_auth"
        assert "user-123" not in response.json()["scope_id"]

    @pytest.mark.parametrize("proxy_key", [None, "wrong"])
    def test_unbound_easy_auth_principal_is_rejected(self, client_with_easy_auth, proxy_key):
        response = client_with_easy_auth.get(
            "/api/auth/context",
            headers=_easy_auth_headers(proxy_key=proxy_key),
        )

        assert response.status_code == 401


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
    @pytest.mark.asyncio
    async def test_authenticate_valid_key(self):
        from unittest.mock import MagicMock

        from src.api.auth import ApiKeyProvider

        provider = ApiKeyProvider("my-secret")
        request = MagicMock()
        request.headers = {"X-API-Key": "my-secret"}
        result = await provider.authenticate(request)
        assert result is not None
        assert result["auth_method"] == "apikey"

    @pytest.mark.asyncio
    async def test_authenticate_wrong_key(self):
        from unittest.mock import MagicMock

        from src.api.auth import ApiKeyProvider

        provider = ApiKeyProvider("my-secret")
        request = MagicMock()
        request.headers = {"X-API-Key": "wrong"}
        result = await provider.authenticate(request)
        assert result is None

    @pytest.mark.asyncio
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
    @pytest.mark.asyncio
    async def test_authenticate_valid_principal(self, hosted_caller: HostedCaller) -> None:
        claims = {
            "claims": [
                {"typ": NAMEID_CLAIM, "val": "user-123"},
                {"typ": "name", "val": "Test User"},
                {"typ": "roles", "val": "Dataviewer.Admin"},
            ]
        }
        request = make_asgi_request(
            "POST", "/api/x", headers=hosted_caller.request_headers(principal=encode_principal(claims))
        )
        result = await hosted_caller.provider.authenticate(request)
        assert result is not None
        assert result["auth_method"] == "easy_auth"
        assert "Dataviewer.Admin" in result["roles"]

    @pytest.mark.asyncio
    async def test_authenticate_missing_principal(self, hosted_caller: HostedCaller) -> None:
        request = make_asgi_request("POST", "/api/x", headers=hosted_caller.request_headers(principal=None))
        assert await hosted_caller.provider.authenticate(request) is None

    @pytest.mark.asyncio
    async def test_authenticate_invalid_base64(self, hosted_caller: HostedCaller) -> None:
        request = make_asgi_request(
            "POST", "/api/x", headers=hosted_caller.request_headers(principal="not-valid-base64!!!")
        )
        assert await hosted_caller.provider.authenticate(request) is None

    @pytest.mark.asyncio
    async def test_missing_caller_proof_is_denied_before_principal(self, hosted_caller: HostedCaller) -> None:
        request = make_asgi_request("POST", "/api/x", headers=hosted_caller.request_headers(include_caller=False))
        with pytest.raises(HTTPException) as exc_info:
            await hosted_caller.provider.authenticate(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Caller authentication required"

    def test_www_authenticate_header(self, hosted_caller: HostedCaller) -> None:
        assert "EasyAuth" in hosted_caller.provider.www_authenticate


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


# ============================================================================
# Hosted caller proof and principal HTTP matrix (easy_auth)
# ============================================================================

OTHER_ID = "00000000-0000-0000-0000-0000000000ff"
MUTATION_PATH = "/api/datasets/nonexistent/episodes/0/annotations/auto"
REQUIRED_CALLER_CLAIMS = ("ver", "tid", "idtyp", "oid", "sub", "azp", "exp", "nbf", "aud", "iss")
ALICE_SCOPE = "principal-c0ccc90d5b0e65ef7c696c1ce71babf77b68bc340ebd476ca02cac3fa01c4b70"
BOB_SCOPE = "principal-47420aaaedf495b8c69c299db13cefd1904ee8f0681d572f09622abdb032535c"
SUBJECT_CLAIM = {"typ": NAMEID_CLAIM, "val": "user-1"}

_HOSTED_EXTRA_CASES: list[tuple[str, str]] = [
    ("GET", "/unknown"),
    ("POST", "/health"),
    ("GET", "/health/anything"),
    ("GET", "/health/"),
    ("OPTIONS", "/api/datasets"),
    ("HEAD", "/api/datasets"),
    ("GET", "/api/datasets/example/episodes/0/video/front"),
    ("GET", "/api/datasets/example/episodes/0/frames/0"),
]


def _hosted_route_cases() -> list[tuple[str, str]]:
    cases: set[tuple[str, str]] = set(_HOSTED_EXTRA_CASES)
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        path = re.sub(r"\{[^}]+\}", "sample", route.path)
        cases.update((method, path) for method in methods)
    # Only exact GET and HEAD /health are exempt from caller proof.
    exempt = {("GET", "/health"), ("HEAD", "/health")}
    return sorted(case for case in cases if case not in exempt)


_HOSTED_ROUTE_CASES = _hosted_route_cases()


@pytest.mark.parametrize(
    ("method", "path"),
    _HOSTED_ROUTE_CASES,
    ids=[f"{method} {path}" for method, path in _HOSTED_ROUTE_CASES],
)
def test_hosted_route_denied_without_caller_proof(hosted_client: TestClient, method: str, path: str) -> None:
    response = hosted_client.request(method, path)

    assert response.status_code == 401
    if method != "HEAD":
        assert response.json() == {"detail": "Caller authentication required"}


def test_hosted_route_matrix_covers_bootstrap_docs_and_api() -> None:
    covered = {path for _, path in _HOSTED_ROUTE_CASES}

    assert {"/api/csrf-token", "/api/auth/context", "/api/auth/media-session", "/api/datasets"} <= covered
    assert "/openapi.json" in covered


@pytest.mark.parametrize("include_caller", [False, True])
def test_given_hosted_denial_when_requesting_context_then_security_headers_remain(
    hosted_client: TestClient, hosted_caller: HostedCaller, include_caller: bool
) -> None:
    response = hosted_client.get(
        "/api/auth/context",
        headers=hosted_caller.request_headers(include_caller=include_caller, principal=None),
    )

    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["WWW-Authenticate"] == 'EasyAuth realm="DataViewer API"'


@pytest.mark.parametrize("include_caller", [False, True])
def test_given_hosted_preflight_when_authenticating_then_only_verified_user_reaches_cors(
    hosted_client: TestClient, hosted_caller: HostedCaller, include_caller: bool
) -> None:
    from src.api.main import _config

    origin = _config.cors_origins[0]
    response = hosted_client.options(
        "/api/datasets",
        headers=hosted_caller.request_headers(
            include_caller=include_caller,
            extra=[("Origin", origin), ("Access-Control-Request-Method", "GET")],
        ),
    )

    assert response.status_code == (200 if include_caller else 401)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    if include_caller:
        assert response.headers["Access-Control-Allow-Origin"] == origin


@pytest.mark.asyncio
async def test_given_empty_roles_when_authenticating_then_only_main_compatible_roles_remain(
    hosted_caller: HostedCaller,
) -> None:
    principal = encode_principal(
        {"claims": [SUBJECT_CLAIM, {"typ": "roles", "val": ""}, {"typ": "roles", "val": "Dataviewer.Admin"}]}
    )

    user = await hosted_caller.provider.authenticate(
        make_asgi_request("GET", "/api/auth/context", headers=hosted_caller.request_headers(principal=principal))
    )

    assert user is not None
    assert user["roles"] == ["Dataviewer.Admin"]


def _caller_case(case_id: str, **options: Any) -> Any:
    return pytest.param(lambda caller: caller.request_headers(caller_token=caller.token(**options)), id=case_id)


def _claim_case(case_id: str, **claims: object) -> Any:
    return _caller_case(case_id, claims=claims)


def _tamper_signature(token: str) -> str:
    header, payload, signature = token.split(".")
    replacement = "B" if signature[0] == "A" else "A"
    return ".".join((header, payload, replacement + signature[1:]))


CALLER_DENIALS = [
    pytest.param(lambda caller: caller.request_headers(include_caller=False), id="missing-caller"),
    pytest.param(
        lambda caller: caller.request_headers(extra=[(CALLER_TOKEN_HEADER, caller.token())]),
        id="duplicate-caller",
    ),
    pytest.param(
        lambda caller: caller.request_headers(caller_token=f"{caller.token()},{caller.token()}"),
        id="comma-joined-caller",
    ),
    pytest.param(lambda caller: caller.request_headers(caller_token=f"{caller.token()} "), id="whitespace-caller"),
    pytest.param(lambda caller: caller.request_headers(caller_token=""), id="empty-caller"),
    pytest.param(lambda caller: caller.request_headers(caller_token="x" * 8193), id="oversized-caller"),
    pytest.param(lambda caller: caller.request_headers(caller_token="not-a-jwt"), id="malformed-caller"),
    pytest.param(
        lambda caller: caller.request_headers(caller_token=_tamper_signature(caller.token())),
        id="bad-signature",
    ),
    pytest.param(
        lambda caller: caller.request_headers(caller_token=caller.token(key=caller.keys.rogue)),
        id="untrusted-signature",
    ),
    _caller_case("hs256-key-confusion", algorithm="HS256", key=b"k" * 32),
    _caller_case("alg-none", algorithm="none"),
    _caller_case("unknown-kid", kid="rotated-away"),
    _caller_case("missing-kid", kid=None),
    _claim_case("expired", exp=1_000_000_000),
    _claim_case("not-yet-valid", nbf=4_000_000_000),
    _claim_case("non-numeric-exp", exp="soon"),
    _claim_case("boolean-nbf", nbf=True),
    _claim_case("wrong-version", ver="1.0"),
    _claim_case("wrong-tenant", tid=OTHER_ID),
    _claim_case("user-token", idtyp="user"),
    _claim_case("delegated-scope", scp="user_impersonation"),
    _claim_case("wrong-issuer", iss=f"https://login.microsoftonline.com/{OTHER_ID}/v2.0"),
    _claim_case("wrong-audience", aud=OTHER_ID),
    _claim_case("audience-list", aud=[CALLER_API_AUDIENCE, OTHER_ID]),
    _claim_case("blank-object-id", oid="   "),
    _claim_case("non-string-subject", sub=123),
    _claim_case("list-client-id", azp=["x"]),
    *[_caller_case(f"omitted-{name}", omit=[name]) for name in REQUIRED_CALLER_CLAIMS],
]


@pytest.mark.parametrize("caller_denial", CALLER_DENIALS)
def test_invalid_caller_proof_is_denied_before_principal(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
    caller_denial: Any,
) -> None:
    response = hosted_client.get("/api/datasets", headers=caller_denial(hosted_caller))

    assert response.status_code == 401
    assert response.json() == {"detail": "Caller authentication required"}


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"oid": OTHER_ID, "sub": OTHER_ID}, id="other-object-id"),
        pytest.param({"azp": OTHER_ID}, id="other-client-id"),
    ],
)
def test_valid_proof_for_another_caller_returns_403(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
    claims: dict[str, str],
) -> None:
    headers = hosted_caller.request_headers(caller_token=hosted_caller.token(claims))

    response = hosted_client.get("/api/datasets", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "Caller is not authorized"}


def test_jwks_outage_fails_closed(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
    jwks_server: Any,
) -> None:
    jwks_server.status = 500

    response = hosted_client.get("/api/datasets", headers=hosted_caller.request_headers())

    assert response.status_code == 401
    assert response.json() == {"detail": "Caller authentication required"}


def test_missing_caller_proof_never_contacts_jwks(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
    jwks_server: Any,
) -> None:
    response = hosted_client.get("/api/datasets", headers=hosted_caller.request_headers(include_caller=False))

    assert response.status_code == 401
    assert jwks_server.requests == 0


def test_bearer_token_cannot_substitute_for_caller_proof(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
) -> None:
    headers = hosted_caller.request_headers(
        include_caller=False,
        extra=[("Authorization", f"Bearer {hosted_caller.token()}")],
    )

    response = hosted_client.get("/api/datasets", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Caller authentication required"}


def _principal_case(case_id: str, principal: str | None, *, extra: Sequence[tuple[str, str]] = ()) -> Any:
    return pytest.param(lambda caller: caller.request_headers(principal=principal, extra=extra), id=case_id)


def _claims_principal(*claims: object) -> str:
    return encode_principal({"claims": list(claims)})


PRINCIPAL_DENIALS = [
    _principal_case("missing-principal", None),
    _principal_case("duplicate-principal", DEFAULT_PRINCIPAL, extra=[(PRINCIPAL_HEADER, DEFAULT_PRINCIPAL)]),
    _principal_case("comma-joined-principal", f"{DEFAULT_PRINCIPAL},{DEFAULT_PRINCIPAL}"),
    _principal_case("whitespace-principal", f"{DEFAULT_PRINCIPAL} "),
    _principal_case("invalid-base64", "not-valid-base64!!!"),
    _principal_case("oversized-encoded", "A" * 8196),
    _principal_case("non-json", encode_bytes(b"not-json")),
    _principal_case("non-utf8", encode_bytes(b"\x80\x81\x82")),
    _principal_case("deep-nesting", encode_bytes(b"[" * 2000 + b"]" * 2000)),
    _principal_case("array-root", encode_principal([])),
    _principal_case("claims-absent", encode_principal({})),
    _principal_case("claims-not-list", encode_principal({"claims": "user-1"})),
    _principal_case(
        "too-many-claims",
        _claims_principal(SUBJECT_CLAIM, *({"typ": "roles", "val": "r"} for _ in range(64))),
    ),
    _principal_case("claim-not-object", encode_principal({"claims": ["user-1"]})),
    _principal_case("claim-type-missing", encode_principal({"claims": [{"val": "user-1"}]})),
    _principal_case("claim-type-not-string", encode_principal({"claims": [{"typ": 1, "val": "user-1"}]})),
    _principal_case("claim-type-empty", encode_principal({"claims": [{"typ": "", "val": "user-1"}]})),
    _principal_case("claim-type-oversized", _claims_principal(SUBJECT_CLAIM, {"typ": "t" * 257, "val": "x"})),
    _principal_case("claim-value-not-string", encode_principal({"claims": [{"typ": NAMEID_CLAIM, "val": 1}]})),
    _principal_case("claim-value-null", encode_principal({"claims": [{"typ": NAMEID_CLAIM, "val": None}]})),
    _principal_case("claim-value-surrogate", _claims_principal({"typ": NAMEID_CLAIM, "val": "\ud800"})),
    _principal_case("claim-type-surrogate", _claims_principal(SUBJECT_CLAIM, {"typ": "\ud800", "val": "x"})),
    _principal_case(
        "claim-value-oversized",
        _claims_principal({"typ": NAMEID_CLAIM, "val": "u" * 2049}),
    ),
    _principal_case("subject-missing", _claims_principal({"typ": "name", "val": "Alice"})),
    _principal_case("subject-blank", _claims_principal({"typ": NAMEID_CLAIM, "val": "   "})),
    _principal_case("subject-ambiguous", _claims_principal(SUBJECT_CLAIM, {"typ": NAMEID_CLAIM, "val": "user-2"})),
    _principal_case("subject-mixed-sources", _claims_principal(SUBJECT_CLAIM, {"typ": "sub", "val": "user-2"})),
    _principal_case("subject-claim-substring", _claims_principal({"typ": f"{NAMEID_CLAIM}-extra", "val": "user-1"})),
]


@pytest.mark.parametrize("principal_denial", PRINCIPAL_DENIALS)
def test_principal_denied_after_valid_caller(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
    principal_denial: Any,
) -> None:
    response = hosted_client.get("/api/datasets", headers=principal_denial(hosted_caller))

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    assert response.headers["WWW-Authenticate"] == 'EasyAuth realm="DataViewer API"'


def test_principal_is_not_parsed_before_caller_is_authorized(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import auth as auth_mod

    class _CallCounter:
        def __init__(self, target: Any) -> None:
            self._target = target
            self.calls = 0

        def __getattr__(self, name: str) -> Any:
            attribute = getattr(self._target, name)

            def counted(*args: Any, **kwargs: Any) -> Any:
                self.calls += 1
                return attribute(*args, **kwargs)

            return counted

    counters = {name: _CallCounter(getattr(auth_mod, name)) for name in ("base64", "json")}
    for name, counter in counters.items():
        monkeypatch.setattr(auth_mod, name, counter)

    denied = hosted_client.get("/api/datasets", headers=hosted_caller.request_headers(include_caller=False))
    assert denied.status_code == 401
    assert all(counter.calls == 0 for counter in counters.values())

    allowed = hosted_client.get("/api/datasets", headers=hosted_caller.request_headers())
    assert allowed.status_code == 200
    assert all(counter.calls >= 1 for counter in counters.values())


def test_caller_denial_precedes_csrf(hosted_client: TestClient) -> None:
    response = hosted_client.post(MUTATION_PATH)

    assert response.status_code == 401


def test_principal_denial_precedes_csrf(hosted_client: TestClient, hosted_caller: HostedCaller) -> None:
    response = hosted_client.post(MUTATION_PATH, headers=hosted_caller.request_headers(principal=None))

    assert response.status_code == 401


def test_missing_csrf_with_full_caller_and_principal_returns_403(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
) -> None:
    response = hosted_client.post(MUTATION_PATH, headers=hosted_caller.request_headers())

    assert response.status_code == 403


def test_caller_principal_and_csrf_reach_mutation_handler(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
) -> None:
    csrf = hosted_client.get("/api/csrf-token", headers=hosted_caller.request_headers()).json()["csrf_token"]
    hosted_client.cookies.set("csrf_token", csrf)

    response = hosted_client.post(
        MUTATION_PATH,
        headers=hosted_caller.request_headers(extra=[("X-CSRF-Token", csrf)]),
    )

    assert response.status_code not in (401, 403)


def test_hosted_media_session_is_not_a_principal_credential(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
) -> None:
    csrf = hosted_client.get("/api/csrf-token", headers=hosted_caller.request_headers()).json()["csrf_token"]

    response = hosted_client.post(
        "/api/auth/media-session",
        headers=hosted_caller.request_headers(extra=[("X-CSRF-Token", csrf)]),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "JWT authentication required for native media"}


def test_principal_scope_is_pinned_and_isolated_per_subject(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
) -> None:
    bob_principal = encode_principal({"claims": [{"typ": NAMEID_CLAIM, "val": "user-2"}]})

    alice = hosted_client.get("/api/auth/context", headers=hosted_caller.request_headers()).json()
    bob = hosted_client.get(
        "/api/auth/context",
        headers=hosted_caller.request_headers(principal=bob_principal),
    ).json()

    assert alice["scope_id"] == ALICE_SCOPE
    assert bob["scope_id"] == BOB_SCOPE
    assert alice["auth_mode"] == "easy_auth"
    assert "user-1" not in str(alice)


def test_rejected_credentials_are_not_logged(
    hosted_client: TestClient,
    hosted_caller: HostedCaller,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    forged = hosted_caller.token(key=hosted_caller.keys.rogue)
    malformed = encode_bytes(b'{"claims": "subject-secret-7f3a"')

    caller_denied = hosted_client.get("/api/datasets", headers=hosted_caller.request_headers(caller_token=forged))
    principal_denied = hosted_client.get("/api/datasets", headers=hosted_caller.request_headers(principal=malformed))

    assert caller_denied.status_code == 401
    assert principal_denied.status_code == 401
    assert "Authentication failed" in caplog.text
    for secret in (forged, malformed, "subject-secret-7f3a"):
        assert secret not in caplog.text


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("DATAVIEWER_AZURE_TENANT_ID", "not-a-uuid"),
        ("DATAVIEWER_AZURE_CLIENT_ID", "0000000A-0000-0000-0000-0000000000AB"),
        ("DATAVIEWER_CALLER_OBJECT_ID", f"{CALLER_OBJECT_ID} "),
        ("DATAVIEWER_CALLER_CLIENT_ID", f"{{{CALLER_CLIENT_ID}}}"),
    ],
    ids=["tenant-not-uuid", "api-audience-uppercase", "object-id-whitespace", "client-id-braced"],
)
def test_hosted_startup_rejects_non_canonical_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
) -> None:
    set_hosted_environment(monkeypatch)
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match="canonical"), TestClient(app):
        pass


def test_hosted_startup_rejects_disabled_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    set_hosted_environment(monkeypatch)
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")

    with pytest.raises(ValueError, match="Hosted authentication cannot be disabled"), TestClient(app):
        pass
