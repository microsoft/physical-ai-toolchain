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


def test_get_episode_analysis_unknown_returns_null(client):
    """GET episode analysis returns null when no record exists."""
    response = client.get("/api/datasets/test/episodes/4/analysis")
    assert response.status_code == 200
    assert response.json() is None


def test_set_and_get_episode_analysis_roundtrip(client, monkeypatch):
    """PUT analysis persists a structured record, invalidates cache, and rides along /labels."""
    invalidations: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "src.api.services.dataset_service.DatasetService.invalidate_episode_cache",
        lambda self, dataset_id, episode_idx: invalidations.append((dataset_id, episode_idx)),
    )

    record = {
        "pick_from": "front",
        "object": "black cloth",
        "grasp_success": True,
        "place_success": False,
        "movement_quality": "Smooth approach then a missed release.",
        "notes": "Gripper opened early.",
        "normalized_smoothness": 0.2,
        "motion_score": 2,
        "motion_flags": ["jittery"],
        "source": "qwen3-vl",
    }

    put_resp = client.put("/api/datasets/test/episodes/5/analysis", json=record)
    assert put_resp.status_code == 200
    assert put_resp.json()["object"] == "black cloth"
    assert invalidations == [("test", 5)]

    get_resp = client.get("/api/datasets/test/episodes/5/analysis")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["pick_from"] == "front"
    assert body["grasp_success"] is True
    assert body["place_success"] is False
    assert body["motion_flags"] == ["jittery"]

    # The full labels file carries the analysis map so it auto-loads with the dataset.
    labels = client.get("/api/datasets/test/labels").json()
    assert labels["analysis"]["5"]["object"] == "black cloth"


def test_import_analysis_labels_handles_scalar_boolean_and_list_values(client, monkeypatch):
    """Analysis imports normalize supported value types and invalidate the dataset cache."""
    invalidations: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        "src.api.services.dataset_service.DatasetService.invalidate_episode_cache",
        lambda self, dataset_id, episode_idx=None: invalidations.append((dataset_id, episode_idx)),
    )

    client.put(
        "/api/datasets/test/episodes/0/analysis",
        json={"object": "Black Cloth", "grasp_success": True, "motion_flags": ["jittery", "hesitation"]},
    )
    client.put(
        "/api/datasets/test/episodes/1/analysis",
        json={"object": "Black Cloth", "grasp_success": False, "motion_flags": []},
    )
    invalidations.clear()

    object_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "prefix": "item"},
    )
    assert object_response.status_code == 200
    assert object_response.json()["labels_added"] == ["ITEM: BLACK CLOTH"]
    assert object_response.json()["episodes_updated"] == 2

    grasp_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "grasp_success"},
    )
    assert grasp_response.status_code == 200
    assert grasp_response.json()["labels_added"] == ["GRASP: YES", "GRASP: NO"]
    assert grasp_response.json()["episodes_updated"] == 2

    flags_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "motion_flags"},
    )
    assert flags_response.status_code == 200
    assert flags_response.json()["labels_added"] == ["FLAG: JITTERY", "FLAG: HESITATION"]
    assert flags_response.json()["episodes_updated"] == 1
    assert invalidations == [("test", None), ("test", None), ("test", None)]


def test_import_analysis_labels_overwrites_stale_namespace_and_is_idempotent(client, monkeypatch):
    """Overwrite removes stale namespace values and repeated imports make no changes."""
    invalidations: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        "src.api.services.dataset_service.DatasetService.invalidate_episode_cache",
        lambda self, dataset_id, episode_idx=None: invalidations.append((dataset_id, episode_idx)),
    )

    client.put("/api/datasets/test/episodes/2/labels", json={"labels": ["SUCCESS", "OBJECT: OLD"]})
    client.put("/api/datasets/test/episodes/2/analysis", json={"object": "new"})
    invalidations.clear()

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
    )
    assert response.status_code == 200
    assert response.json()["episodes"]["2"] == ["SUCCESS", "OBJECT: NEW"]
    assert response.json()["episodes_updated"] == 1
    assert invalidations == [("test", None)]

    invalidations.clear()
    repeated = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
    )
    assert repeated.status_code == 200
    assert repeated.json()["labels_added"] == []
    assert repeated.json()["episodes_updated"] == 0
    assert invalidations == []


def test_import_analysis_labels_overwrite_removes_stale_values_without_current_analysis(client):
    client.put("/api/datasets/test/episodes/0/labels", json={"labels": ["SUCCESS", "OBJECT: OLD"]})
    client.put("/api/datasets/test/episodes/1/labels", json={"labels": ["OBJECT: STALE"]})
    client.put("/api/datasets/test/episodes/0/analysis", json={"object": "new"})
    client.put("/api/datasets/test/episodes/1/analysis", json={"notes": "No object value"})

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["episodes"]["0"] == ["SUCCESS", "OBJECT: NEW"]
    assert body["episodes"]["1"] == []
    assert "OBJECT: OLD" not in body["available_labels"]
    assert "OBJECT: STALE" not in body["available_labels"]


def test_import_analysis_labels_rejects_unsupported_field(client):
    """Free-text and unknown analysis fields cannot become dataset labels."""
    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "movement_quality"},
    )
    assert response.status_code == 400
    assert "is not importable" in response.json()["detail"]


def test_analysis_value_labels_ignores_absent_and_blank_values():
    """Missing and blank analysis values do not produce filter labels."""
    assert labels_mod._analysis_value_labels("OBJECT", None) == []
    assert labels_mod._analysis_value_labels("OBJECT", [" ", None]) == []


def test_import_analysis_labels_without_values_does_not_persist(client, monkeypatch):
    """An import with no populated values is a no-op."""
    save = AsyncMock()
    invalidations: list[tuple[str, int | None]] = []
    monkeypatch.setattr(labels_mod, "_save_labels", save)
    monkeypatch.setattr(
        "src.api.services.dataset_service.DatasetService.invalidate_episode_cache",
        lambda self, dataset_id, episode_idx=None: invalidations.append((dataset_id, episode_idx)),
    )

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "source"},
    )

    assert response.status_code == 200
    assert response.json()["labels_added"] == []
    assert response.json()["episodes_updated"] == 0
    save.assert_not_awaited()
    assert invalidations == []


# ---------------------------------------------------------------------------
# Storage backend unit tests
# ---------------------------------------------------------------------------


def test_local_storage_save_then_load_roundtrip():
    """LocalLabelStorage persists and reloads a labels file."""
    with tempfile.TemporaryDirectory() as tmp:
        storage = labels_mod.LocalLabelStorage(tmp)
        original = labels_mod.DatasetLabelsFile(
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
        storage = labels_mod.LocalLabelStorage(tmp)
        loaded = asyncio.run(storage.load("missing"))
        assert loaded.dataset_id == "missing"
        assert loaded.available_labels == ["SUCCESS", "FAILURE", "PARTIAL"]
        assert loaded.episodes == {}


def test_blob_label_storage_logs_sanitized_dataset_id(monkeypatch):
    """Invalid blob content should log a sanitized dataset identifier."""
    logged: list[tuple[object, ...]] = []
    provider = SimpleNamespace(_read_blob_bytes=AsyncMock(return_value=b"not-json"))
    storage = labels_mod.BlobLabelStorage(provider)

    monkeypatch.setattr(
        "src.api.routers.labels.logger.warning",
        lambda message, *args: logged.append((message, *args)),
    )

    result = asyncio.run(storage.load("dataset\r\nname"))

    assert isinstance(result, labels_mod.DatasetLabelsFile)
    assert result.available_labels == ["SUCCESS", "FAILURE", "PARTIAL"]
    assert logged == [("Invalid labels blob for %s, returning defaults", "datasetname")]


def test_blob_label_storage_load_missing_returns_defaults():
    """BlobLabelStorage.load returns defaults when blob is absent."""
    provider = SimpleNamespace(_read_blob_bytes=AsyncMock(return_value=None))
    storage = labels_mod.BlobLabelStorage(provider)

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
    storage = labels_mod.BlobLabelStorage(provider)
    labels_file = labels_mod.DatasetLabelsFile(dataset_id="ds")

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
    storage = labels_mod.BlobLabelStorage(provider)

    monkeypatch.setattr(
        "src.api.routers.labels.logger.error",
        lambda message, *args: logged.append((message, *args)),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(storage.save("ds\r\nx", labels_mod.DatasetLabelsFile(dataset_id="ds")))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to save labels"
    assert logged and logged[0][1] == "dsx"


# ---------------------------------------------------------------------------
# Factory + singleton wiring
# ---------------------------------------------------------------------------


def test_labels_path_resolves_nested_dataset_id(monkeypatch, tmp_path):
    """Nested dataset IDs resolve to the expected labels metadata path."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    path = labels_mod._labels_path("owner--dataset")

    assert path == tmp_path / "owner" / "dataset" / "meta" / "episode_labels.json"


def test_create_label_storage_returns_local_when_no_provider():
    """Default backend yields LocalLabelStorage."""
    storage = labels_mod._create_label_storage("local", None)
    assert isinstance(storage, labels_mod.LocalLabelStorage)


def test_create_label_storage_returns_blob_for_azure():
    """azure backend with a provider yields BlobLabelStorage."""
    provider = SimpleNamespace()
    storage = labels_mod._create_label_storage("azure", provider)
    assert isinstance(storage, labels_mod.BlobLabelStorage)


def test_create_label_storage_falls_back_when_azure_without_provider():
    """azure backend without provider falls back to LocalLabelStorage."""
    storage = labels_mod._create_label_storage("azure", None)
    assert isinstance(storage, labels_mod.LocalLabelStorage)


def test_get_label_storage_singleton(monkeypatch):
    """_get_label_storage caches the storage instance and uses app config."""
    monkeypatch.setattr(labels_mod, "_label_storage", None)
    fake_config = SimpleNamespace(storage_backend="local")
    monkeypatch.setattr(
        "src.api.config.get_app_config",
        lambda: fake_config,
    )

    first = labels_mod._get_label_storage()
    second = labels_mod._get_label_storage()
    assert first is second
    assert isinstance(first, labels_mod.LocalLabelStorage)

    monkeypatch.setattr(labels_mod, "_label_storage", None)


def test_get_label_storage_creates_azure_provider_once(monkeypatch):
    """Azure label storage creates one provider and caches the resulting adapter."""
    monkeypatch.setattr(labels_mod, "_label_storage", None)
    fake_config = SimpleNamespace(storage_backend="azure")
    provider = SimpleNamespace()
    create_provider = MagicMock(return_value=provider)
    monkeypatch.setattr("src.api.config.get_app_config", lambda: fake_config)
    monkeypatch.setattr("src.api.config.create_blob_dataset_provider", create_provider)

    first = labels_mod._get_label_storage()
    second = labels_mod._get_label_storage()

    assert first is second
    assert isinstance(first, labels_mod.BlobLabelStorage)
    assert first._provider is provider
    create_provider.assert_called_once_with(fake_config)

    monkeypatch.setattr(labels_mod, "_label_storage", None)
