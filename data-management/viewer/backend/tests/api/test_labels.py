"""Integration tests for label API endpoints."""

import os
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers.labels import BlobLabelStorage


@pytest.fixture
def client():
    """Create test client with isolated singletons and empty temp data path."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATA_DIR"] = tmp

        import src.api.config as config_mod
        import src.api.services.annotation_service as ann_mod
        import src.api.services.dataset_service as ds_mod

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None

        with TestClient(app) as c:
            yield c

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None


def test_delete_label_option_removes_assignments(client):
    """Deleting a label option should also remove it from episode assignments."""
    client.put(
        "/api/datasets/test-dataset/episodes/1/labels",
        json={"labels": ["SUCCESS", "REVIEW"]},
    )
    client.put(
        "/api/datasets/test-dataset/episodes/2/labels",
        json={"labels": ["REVIEW"]},
    )

    response = client.delete("/api/datasets/test-dataset/labels/options/review")

    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL"]

    labels = client.get("/api/datasets/test-dataset/labels").json()
    assert labels["available_labels"] == ["SUCCESS", "FAILURE", "PARTIAL"]
    assert labels["episodes"]["1"] == ["SUCCESS"]
    assert labels["episodes"]["2"] == []


def test_delete_default_label_option_rejected(client):
    """Built-in labels should not be deletable."""
    response = client.delete("/api/datasets/test-dataset/labels/options/success")

    assert response.status_code == 400
    assert response.json()["detail"] == "Built-in labels cannot be deleted"


@pytest.mark.asyncio
async def test_blob_label_storage_logs_sanitized_dataset_id(monkeypatch):
    """Invalid blob content should log a sanitized dataset identifier."""
    logged: list[tuple[object, ...]] = []
    provider = SimpleNamespace(_read_blob_bytes=lambda _path: b"not-json")
    storage = BlobLabelStorage(provider)

    async def fake_read_blob_bytes(_path: str) -> bytes:
        return b"not-json"

    monkeypatch.setattr(provider, "_read_blob_bytes", fake_read_blob_bytes)
    monkeypatch.setattr(
        "src.api.routers.labels.logger.warning",
        lambda message, *args: logged.append((message, *args)),
    )

    await storage.load("dataset\r\nname")

    assert logged == [("Invalid labels blob for %s, returning defaults", "datasetname")]
