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


def _write_accessibility_episode(path: Path, *, length: int, phase: float) -> None:
    """Write one deterministic synthetic HDF5 episode."""
    h5py = pytest.importorskip("h5py")
    numpy = pytest.importorskip("numpy")

    timeline = numpy.linspace(0.0, 1.0, length, dtype=numpy.float32)
    joint_offsets = numpy.linspace(0.0, 0.5, 6, dtype=numpy.float32)
    positions = numpy.sin((timeline[:, None] + phase) * numpy.pi) + joint_offsets
    actions = positions + 0.05

    row = numpy.arange(48, dtype=numpy.uint8)[:, None]
    column = numpy.arange(64, dtype=numpy.uint8)[None, :]
    frames = numpy.empty((length, 48, 64, 3), dtype=numpy.uint8)
    for frame_index in range(length):
        frames[frame_index, :, :, 0] = (row + frame_index * 7) % 255
        frames[frame_index, :, :, 1] = (column + frame_index * 11) % 255
        frames[frame_index, :, :, 2] = (row + column + frame_index * 13) % 255

    with h5py.File(path, "w") as file:
        observations = file.create_group("observations")
        observations.create_dataset("qpos", data=positions)
        images = observations.create_group("images")
        images.create_dataset("front", data=frames)
        images.create_dataset("wrist", data=numpy.flip(frames, axis=2))
        file.create_dataset("action", data=actions)
        file.attrs["fps"] = 12.0
        file.attrs["task_index"] = 0


@pytest.fixture(scope="session")
def accessibility_dataset_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create the deterministic synthetic dataset used by accessibility runners."""
    root = tmp_path_factory.mktemp("a11y-datasets")
    dataset = root / "a11y-synthetic"
    dataset.mkdir()
    _write_accessibility_episode(dataset / "episode_0.hdf5", length=12, phase=0.0)
    _write_accessibility_episode(dataset / "episode_1.hdf5", length=18, phase=0.25)
    return root


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
