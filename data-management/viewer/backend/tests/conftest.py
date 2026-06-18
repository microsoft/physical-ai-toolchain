"""Pytest configuration and shared fixtures for integration tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

# Expose the repository root on sys.path so tests can import the
# ``evaluation.vlm_judge`` package the running backend reaches via
# ``start.sh`` (which exports PYTHONPATH when VLM_JUDGE_ENABLED=true). This
# keeps the VLM-judge router and service tests runnable in CI without the
# production launch wrapper.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


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


# Load .env from the backend directory so TEST_DATASET_ID and other
# settings can be configured without hardcoding.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TEST_DATASET_PATH = os.environ.get(
    "TEST_DATASET_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "datasets"),
)

TEST_DATASET_ID = os.environ.get("TEST_DATASET_ID", "lerobot")


@pytest.fixture(autouse=True, scope="session")
def disable_auth_for_tests():
    """Disable authentication and CSRF checks for all tests."""
    os.environ["DATAVIEWER_AUTH_DISABLED"] = "true"
    yield
    os.environ.pop("DATAVIEWER_AUTH_DISABLED", None)


@pytest.fixture(scope="session")
def test_dataset_path():
    """Absolute path to the directory containing the test LeRobot dataset."""
    if not os.path.isdir(TEST_DATASET_PATH):
        pytest.skip(f"Dataset base path not found: {TEST_DATASET_PATH}")
    if not os.path.isdir(os.path.join(TEST_DATASET_PATH, TEST_DATASET_ID)):
        pytest.skip(f"LeRobot dataset not found at {TEST_DATASET_PATH}/{TEST_DATASET_ID}")
    return TEST_DATASET_PATH


@pytest.fixture(scope="session")
def test_dataset_id():
    return TEST_DATASET_ID


@pytest.fixture
def client(test_dataset_path):
    """Create a FastAPI test client with DATA_DIR pointing to the real dataset."""
    os.environ["DATA_DIR"] = test_dataset_path

    import src.api.config as config_mod
    import src.api.services.annotation_service as ann_mod
    import src.api.services.dataset_service as ds_mod

    # Reset all singletons so each test gets a fresh service instance that
    # re-reads the current DATA_DIR from the environment.
    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None

    from src.api.main import app

    with TestClient(app) as c:
        yield c

    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None
