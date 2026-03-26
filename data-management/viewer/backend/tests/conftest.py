"""Pytest configuration and shared fixtures for integration tests."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

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
    """Create a FastAPI test client with HMI_DATA_PATH pointing to the real dataset."""
    os.environ["HMI_DATA_PATH"] = test_dataset_path

    import src.api.config as config_mod
    import src.api.services.annotation_service as ann_mod
    import src.api.services.dataset_service as ds_mod

    # Reset all singletons so each test gets a fresh service instance that
    # re-reads the current HMI_DATA_PATH from the environment.
    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None

    from src.api.main import app

    with TestClient(app) as c:
        yield c

    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None
