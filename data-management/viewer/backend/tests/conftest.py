"""Shared pytest configuration and fixtures for backend tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request


def make_asgi_request(
    method: str = "POST",
    path: str = "/api/x",
    headers: dict[str, str] | None = None,
) -> Request:
    """Build a minimal Starlette `Request` for unit-testing dependency callables."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234),
        "app": FastAPI(),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture(autouse=True, scope="session")
def disable_auth_for_tests() -> Iterator[None]:
    """Disable authentication and CSRF checks for all tests."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("DATAVIEWER_AUTH_DISABLED", "true")
        yield


@pytest.fixture(scope="session")
def test_dataset_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return an isolated dataset root for tests that need session scope."""
    return tmp_path_factory.mktemp("datasets")


@pytest.fixture(scope="session")
def test_dataset_id() -> str:
    return "test-dataset"


def _reset_singletons() -> None:
    import src.api.config as config_mod
    import src.api.routers.labels as labels_mod
    import src.api.services.annotation_service as ann_mod
    import src.api.services.dataset_service.service as ds_mod

    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None
    labels_mod._label_storage = None


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Create an isolated FastAPI client backed by a temporary data directory."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    _reset_singletons()

    from src.api.main import app

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    _reset_singletons()
