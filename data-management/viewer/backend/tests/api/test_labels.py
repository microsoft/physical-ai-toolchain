"""Integration and unit tests for label API endpoints."""

import asyncio
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import src.api.routers.labels as labels_mod
from src.api.main import app
from src.api.routers.labels import (
    BlobLabelStorage,
    DatasetLabelsFile,
    LocalLabelStorage,
    _create_label_storage,
    _get_label_storage,
)


@pytest.fixture
def client():
    """Create test client with isolated singletons and empty temp data path."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HMI_DATA_PATH"] = tmp

        import src.api.config as config_mod
        import src.api.services.annotation_service as ann_mod
        import src.api.services.dataset_service as ds_mod

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None
        labels_mod._label_storage = None

        with TestClient(app) as c:
            yield c

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None
        labels_mod._label_storage = None


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_get_dataset_labels_returns_defaults(client):
    """GET /labels returns default available_labels for an unknown dataset."""
    response = client.get("/api/datasets/new-dataset/labels")
    assert response.status_code == 200
    body = response.json()
    assert body["dataset_id"] == "new-dataset"
    assert body["available_labels"] == ["SUCCESS", "FAILURE", "PARTIAL"]
    assert body["episodes"] == {}


def test_get_label_options_returns_defaults(client):
    """GET /labels/options returns default options for an unknown dataset."""
    response = client.get("/api/datasets/new-dataset/labels/options")
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL"]


def test_add_label_option_normalizes_and_dedupes(client):
    """POST /labels/options normalizes input and ignores duplicates."""
    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": " review "},
    )
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL", "REVIEW"]

    # Duplicate (case-insensitive) is silently ignored
    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": "review"},
    )
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL", "REVIEW"]


def test_add_label_option_rejects_empty(client):
    """POST /labels/options with whitespace-only label returns 400."""
    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": "   "},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Label cannot be empty"


def test_get_episode_labels_unknown_returns_empty(client):
    """GET episode labels returns empty list when episode has no labels."""
    response = client.get("/api/datasets/test/episodes/7/labels")
    assert response.status_code == 200
    body = response.json()
    assert body["episode_index"] == 7
    assert body["labels"] == []


def test_set_episode_labels_auto_adds_and_invalidates_cache(client, monkeypatch):
    """PUT episode labels auto-adds new labels and invalidates dataset cache."""
    invalidations: list[tuple[str, int]] = []

    def fake_invalidate(self, dataset_id, episode_idx):
        invalidations.append((dataset_id, episode_idx))

    monkeypatch.setattr(
        "src.api.services.dataset_service.DatasetService.invalidate_episode_cache",
        fake_invalidate,
    )

    response = client.put(
        "/api/datasets/test/episodes/3/labels",
        json={"labels": [" custom ", "success"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["episode_index"] == 3
    assert body["labels"] == ["CUSTOM", "SUCCESS"]
    assert invalidations == [("test", 3)]

    options = client.get("/api/datasets/test/labels/options").json()
    assert "CUSTOM" in options


def test_save_all_labels_roundtrip(client):
    """POST /labels/save persists current state and returns full file."""
    client.put(
        "/api/datasets/test/episodes/1/labels",
        json={"labels": ["SUCCESS"]},
    )
    response = client.post("/api/datasets/test/labels/save")
    assert response.status_code == 200
    body = response.json()
    assert body["dataset_id"] == "test"
    assert body["episodes"]["1"] == ["SUCCESS"]


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


def test_delete_label_option_rejects_empty(client):
    """Whitespace-only label name returns 400."""
    response = client.delete("/api/datasets/test/labels/options/%20")
    assert response.status_code == 400
    assert response.json()["detail"] == "Label cannot be empty"


# ---------------------------------------------------------------------------
# Storage backend unit tests
# ---------------------------------------------------------------------------


def test_local_storage_save_then_load_roundtrip():
    """LocalLabelStorage persists and reloads a labels file."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = LocalLabelStorage(tmp)
        original = DatasetLabelsFile(
            dataset_id="ds",
            available_labels=["A", "B"],
            episodes={"1": ["A"]},
        )

        asyncio.run(storage.save("ds", original))
        loaded = asyncio.run(storage.load("ds"))

        assert loaded.dataset_id == "ds"
        assert loaded.available_labels == ["A", "B"]
        assert loaded.episodes == {"1": ["A"]}


def test_local_storage_load_missing_returns_defaults():
    """LocalLabelStorage.load returns defaults when no file exists."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = LocalLabelStorage(tmp)
        loaded = asyncio.run(storage.load("missing"))
        assert loaded.dataset_id == "missing"
        assert loaded.available_labels == ["SUCCESS", "FAILURE", "PARTIAL"]
        assert loaded.episodes == {}


def test_blob_label_storage_logs_sanitized_dataset_id(monkeypatch):
    """Invalid blob content should log a sanitized dataset identifier."""
    logged: list[tuple[object, ...]] = []
    provider = SimpleNamespace(_read_blob_bytes=AsyncMock(return_value=b"not-json"))
    storage = BlobLabelStorage(provider)

    monkeypatch.setattr(
        "src.api.routers.labels.logger.warning",
        lambda message, *args: logged.append((message, *args)),
    )

    result = asyncio.run(storage.load("dataset\r\nname"))

    assert isinstance(result, DatasetLabelsFile)
    assert result.available_labels == ["SUCCESS", "FAILURE", "PARTIAL"]
    assert logged == [("Invalid labels blob for %s, returning defaults", "datasetname")]


def test_blob_label_storage_load_missing_returns_defaults():
    """BlobLabelStorage.load returns defaults when blob is absent."""
    provider = SimpleNamespace(_read_blob_bytes=AsyncMock(return_value=None))
    storage = BlobLabelStorage(provider)

    result = asyncio.run(storage.load("ds"))
    assert result.dataset_id == "ds"
    assert result.available_labels == ["SUCCESS", "FAILURE", "PARTIAL"]


def test_blob_label_storage_save_uploads_json():
    """BlobLabelStorage.save uploads serialized JSON via the blob client."""
    blob_client = SimpleNamespace(upload_blob=AsyncMock())
    container = MagicMock()
    container.get_blob_client.return_value = blob_client
    client = MagicMock()
    client.get_container_client.return_value = container

    provider = SimpleNamespace(
        _get_client=AsyncMock(return_value=client),
        container_name="datasets",
    )
    storage = BlobLabelStorage(provider)
    labels_file = DatasetLabelsFile(dataset_id="ds")

    asyncio.run(storage.save("ds", labels_file))

    client.get_container_client.assert_called_once_with("datasets")
    container.get_blob_client.assert_called_once()
    blob_client.upload_blob.assert_awaited_once()


def test_blob_label_storage_save_failure_raises_500(monkeypatch):
    """BlobLabelStorage.save logs and raises HTTPException(500) on errors."""
    logged: list[tuple[object, ...]] = []
    provider = SimpleNamespace(
        _get_client=AsyncMock(side_effect=RuntimeError("boom")),
        container_name="datasets",
    )
    storage = BlobLabelStorage(provider)

    monkeypatch.setattr(
        "src.api.routers.labels.logger.error",
        lambda message, *args: logged.append((message, *args)),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(storage.save("ds\r\nx", DatasetLabelsFile(dataset_id="ds")))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to save labels"
    assert logged and logged[0][1] == "dsx"


# ---------------------------------------------------------------------------
# Factory + singleton wiring
# ---------------------------------------------------------------------------


def test_create_label_storage_returns_local_when_no_provider():
    """Default backend yields LocalLabelStorage."""
    storage = _create_label_storage("local", None)
    assert isinstance(storage, LocalLabelStorage)


def test_create_label_storage_returns_blob_for_azure():
    """azure backend with a provider yields BlobLabelStorage."""
    provider = SimpleNamespace()
    storage = _create_label_storage("azure", provider)
    assert isinstance(storage, BlobLabelStorage)


def test_create_label_storage_falls_back_when_azure_without_provider():
    """azure backend without provider falls back to LocalLabelStorage."""
    storage = _create_label_storage("azure", None)
    assert isinstance(storage, LocalLabelStorage)


def test_get_label_storage_singleton(monkeypatch):
    """_get_label_storage caches the storage instance and uses app config."""
    monkeypatch.setattr(labels_mod, "_label_storage", None)
    fake_config = SimpleNamespace(storage_backend="local")
    monkeypatch.setattr(
        "src.api.config.get_app_config",
        lambda: fake_config,
    )

    first = _get_label_storage()
    second = _get_label_storage()
    assert first is second
    assert isinstance(first, LocalLabelStorage)

    monkeypatch.setattr(labels_mod, "_label_storage", None)
