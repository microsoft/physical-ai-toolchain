"""CORS middleware behavior for literal and wildcard CORS_ORIGINS entries."""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

WILDCARD_ORIGINS = "http://localhost:5173,https://*.nicegrass-1a2b3c4d.eastus.azurecontainerapps.io"
FRONTEND_ORIGIN = "https://frontend.nicegrass-1a2b3c4d.eastus.azurecontainerapps.io"


def _reload_main() -> object:
    import src.api.config as config_mod
    import src.api.main as main_mod

    config_mod._app_config = None
    return importlib.reload(main_mod)


@pytest.fixture
def cors_client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[TestClient]:
    """Build the real app with wildcard CORS origins; main.py reads them at import time."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
    monkeypatch.setenv("DATAVIEWER_AUTH_PROVIDER", "apikey")
    monkeypatch.setenv("CORS_ORIGINS", WILDCARD_ORIGINS)
    main_mod = _reload_main()
    try:
        yield TestClient(main_mod.app)
    finally:
        monkeypatch.undo()
        _reload_main()


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/datasets",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


@pytest.mark.parametrize("origin", [FRONTEND_ORIGIN, "http://localhost:5173"], ids=["wildcard", "literal"])
def test_preflight_allows_configured_origin(cors_client: TestClient, origin: str) -> None:
    response = _preflight(cors_client, origin)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example.com",
        "https://frontend.nicegrass-1a2b3c4d.eastus.azurecontainerapps.io.evil.example.com",
        "https://frontendXnicegrass-1a2b3c4d.eastus.azurecontainerapps.io",
    ],
    ids=["other-domain", "suffix-appended", "dot-not-wildcard"],
)
def test_preflight_rejects_unconfigured_origin(cors_client: TestClient, origin: str) -> None:
    response = _preflight(cors_client, origin)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_simple_request_echoes_wildcard_origin(cors_client: TestClient) -> None:
    response = cors_client.get("/health", headers={"Origin": FRONTEND_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
