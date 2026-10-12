"""Signed-JWT coverage for native browser media authentication."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi.testclient import TestClient

from src.api import auth
from src.api.main import app
from tests.conftest import make_asgi_request

COOKIE = "__Secure-dataviewer-media"
NOW = 2_000_000_000


@pytest.fixture
def signing_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token_provider(monkeypatch: pytest.MonkeyPatch, signing_key: RSAPrivateKey) -> tuple[auth.JwtProvider, str]:
    provider = auth.JwtProvider("https://identity.example/keys", "viewer", "https://identity.example")
    client = SimpleNamespace(get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=signing_key.public_key()))
    monkeypatch.setattr(provider, "_get_jwks_client", lambda: client)
    monkeypatch.setattr(auth, "_get_provider", lambda: provider)
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "false")

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime.fromtimestamp(NOW, UTC)

    monkeypatch.setattr(jwt.api_jwt, "datetime", Clock)
    token = jwt.encode(
        {"sub": "viewer-test", "iss": "https://identity.example", "aud": "viewer", "exp": NOW + 600},
        signing_key,
        algorithm="RS256",
    )
    return provider, token


@pytest.mark.parametrize(
    "path",
    [
        "/api/datasets/example/episodes/0/video/observation.images.front",
        "/api/datasets/example/episodes/0/frames/5",
        "/api/datasets/owner--example/episodes/2/video/wrist",
        "/api/datasets/owner--dataset.v3/episodes/2/video/wrist",
        "/api/datasets/dataset name/episodes/2/frames/3",
    ],
)
async def test_cookie_authenticates_only_native_media(token_provider: tuple[auth.JwtProvider, str], path: str) -> None:
    provider, token = token_provider
    request = make_asgi_request("GET", path, {"Cookie": f"{COOKIE}={token}", "Sec-Fetch-Site": "same-origin"})
    user = await provider.authenticate(request)
    assert user is not None and user["sub"] == "viewer-test"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/datasets"),
        ("GET", "/api/datasets/example/episodes/0"),
        ("GET", "/api/datasets/example/episodes/0/annotations"),
        ("POST", "/api/datasets/example/episodes/0/video/front"),
        ("GET", "/api/datasets/example/episodes/0/video/front/extra"),
    ],
)
async def test_cookie_never_grants_general_api_access(
    token_provider: tuple[auth.JwtProvider, str],
    method: str,
    path: str,
) -> None:
    provider, token = token_provider
    request = make_asgi_request(method, path, {"Cookie": f"{COOKIE}={token}"})
    assert await provider.authenticate(request) is None


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
async def test_cross_origin_media_cookie_rejected(token_provider: tuple[auth.JwtProvider, str], site: str) -> None:
    provider, token = token_provider
    request = make_asgi_request(
        "GET", "/api/datasets/example/episodes/0/video/front", {"Cookie": f"{COOKIE}={token}", "Sec-Fetch-Site": site}
    )
    assert await provider.authenticate(request) is None


@pytest.mark.parametrize("authorization", ["Bearer invalid", "", "Basic invalid"])
async def test_invalid_header_does_not_fall_back_to_cookie(
    token_provider: tuple[auth.JwtProvider, str],
    authorization: str,
) -> None:
    provider, token = token_provider
    request = make_asgi_request(
        "GET",
        "/api/datasets/example/episodes/0/video/front",
        {"Cookie": f"{COOKIE}={token}", "Authorization": authorization},
    )
    assert await provider.authenticate(request) is None


async def test_cookie_signature_is_verified(token_provider: tuple[auth.JwtProvider, str]) -> None:
    provider, token = token_provider
    parts = token.split(".")
    parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    request = make_asgi_request(
        "GET", "/api/datasets/example/episodes/0/video/front", {"Cookie": f"{COOKIE}={'.'.join(parts)}"}
    )
    assert await provider.authenticate(request) is None


def test_media_session_requires_bearer_and_csrf(token_provider: tuple[auth.JwtProvider, str]) -> None:
    _, token = token_provider
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/api/auth/media-session", headers={"Authorization": f"Bearer {token}"}).status_code == 403
        csrf = client.get("/api/csrf-token").json()["csrf_token"]
        assert client.post("/api/auth/media-session", headers={"X-CSRF-Token": csrf}).status_code == 401


def test_media_session_sets_bounded_secure_cookie(
    token_provider: tuple[auth.JwtProvider, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, token = token_provider
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    with TestClient(app, base_url="https://testserver") as client:
        csrf = client.get("/api/csrf-token").json()["csrf_token"]
        response = client.post(
            "/api/auth/media-session", headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}
        )
        assert response.status_code == 200
        assert response.json() == {"expires_in": 300}
        cookie = response.headers["set-cookie"]
        for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/api/datasets/", "Max-Age=300"):
            assert flag in cookie
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": NOW - 1, "aud": "viewer", "iss": "https://identity.example"},
        {"exp": NOW + 600, "aud": "other", "iss": "https://identity.example"},
        {"exp": NOW + 600, "aud": "viewer", "iss": "https://other.example"},
        {"aud": "viewer", "iss": "https://identity.example"},
    ],
)
async def test_cookie_requires_valid_expiration_audience_and_issuer(
    token_provider: tuple[auth.JwtProvider, str],
    signing_key: RSAPrivateKey,
    claims: dict[str, object],
) -> None:
    provider, _ = token_provider
    token = jwt.encode({"sub": "viewer-test", **claims}, signing_key, algorithm="RS256")
    request = make_asgi_request(
        "GET",
        "/api/datasets/example/episodes/0/video/front",
        {"Cookie": f"{COOKIE}={token}"},
    )
    assert await provider.authenticate(request) is None


@pytest.mark.parametrize(("remaining", "expected"), [(61, 61), (600, 300)])
def test_session_never_outlives_verified_token(
    token_provider: tuple[auth.JwtProvider, str],
    signing_key: RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
    remaining: int,
    expected: int,
) -> None:
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    token = jwt.encode(
        {"sub": "viewer-test", "exp": NOW + remaining, "aud": "viewer", "iss": "https://identity.example"},
        signing_key,
        algorithm="RS256",
    )
    with TestClient(app, base_url="https://testserver") as client:
        csrf = client.get("/api/csrf-token").json()["csrf_token"]
        response = client.post(
            "/api/auth/media-session",
            headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.json() == {"expires_in": expected}
        assert f"Max-Age={expected}" in response.headers["set-cookie"]


async def test_cookie_accepts_head_requests(token_provider: tuple[auth.JwtProvider, str]) -> None:
    provider, token = token_provider
    request = make_asgi_request(
        "HEAD",
        "/api/datasets/example/episodes/0/video/front",
        {"Cookie": f"{COOKIE}={token}"},
    )
    assert await provider.authenticate(request) is not None


def test_oversized_jwt_fails_explicitly_before_cookie_issuance(
    token_provider: tuple[auth.JwtProvider, str],
    signing_key: RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    token = jwt.encode(
        {
            "sub": "viewer-test",
            "exp": NOW + 600,
            "aud": "viewer",
            "iss": "https://identity.example",
            "padding": "x" * 4000,
        },
        signing_key,
        algorithm="RS256",
    )
    with TestClient(app, base_url="https://testserver") as client:
        csrf = client.get("/api/csrf-token").json()["csrf_token"]
        response = client.post(
            "/api/auth/media-session",
            headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
        )
        assert response.status_code == 400
        assert "set-cookie" not in response.headers
