"""Integration and unit tests for label API endpoints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import src.api.routers.labels as labels_mod
from src.api.services.dataset_service import get_dataset_service


@pytest.fixture
def dataset_service(client: TestClient) -> Iterator[MagicMock]:
    """Override the dataset service at the FastAPI dependency boundary."""
    service = MagicMock()
    client.app.dependency_overrides[get_dataset_service] = lambda: service
    yield service
    client.app.dependency_overrides.pop(get_dataset_service, None)


def _revision_headers(client: TestClient, dataset_id: str) -> dict[str, str]:
    response = client.get(f"/api/datasets/{dataset_id}/labels")
    etag = response.headers.get("etag")
    return {"If-Match": etag} if etag else {"If-None-Match": "*"}


def test_get_dataset_labels_returns_defaults(client: TestClient) -> None:
    """GET /labels returns default available_labels for an unknown dataset."""
    response = client.get("/api/datasets/new-dataset/labels")
    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "new-dataset",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {},
    }


def test_get_label_options_returns_defaults(client: TestClient) -> None:
    """GET /labels/options returns default options for an unknown dataset."""
    response = client.get("/api/datasets/new-dataset/labels/options")
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL"]


def test_add_label_option_normalizes_and_dedupes(client: TestClient) -> None:
    """POST /labels/options normalizes input and ignores duplicates."""
    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": " review "},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL", "REVIEW"]

    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": "review"},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL", "REVIEW"]


def test_add_label_option_rejects_empty(client: TestClient) -> None:
    """POST /labels/options with whitespace-only label returns 400."""
    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": "   "},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Label cannot be empty"}


def test_get_episode_labels_unknown_returns_empty(client: TestClient) -> None:
    """GET episode labels returns empty list when episode has no labels."""
    response = client.get("/api/datasets/test/episodes/7/labels")
    assert response.status_code == 200
    assert response.json() == {"episode_index": 7, "labels": []}


def test_set_episode_labels_auto_adds_and_invalidates_cache(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    """PUT episode labels auto-adds new labels and invalidates dataset cache."""
    response = client.put(
        "/api/datasets/test/episodes/3/labels",
        json={"labels": [" custom ", "success"]},
        headers={"If-None-Match": "*"},
    )
    assert response.status_code == 200
    assert response.json() == {"episode_index": 3, "labels": ["CUSTOM", "SUCCESS"]}
    dataset_service.invalidate_episode_cache.assert_called_once_with("test", 3)
    assert response.headers["etag"]

    options_response = client.get("/api/datasets/test/labels/options")
    assert options_response.status_code == 200
    assert options_response.json() == ["SUCCESS", "FAILURE", "PARTIAL", "CUSTOM"]


def test_set_episode_labels_rejects_stale_revision_without_modifying_labels(client: TestClient) -> None:
    created = client.put(
        "/api/datasets/test/episodes/3/labels",
        json={"labels": ["SUCCESS"]},
        headers={"If-None-Match": "*"},
    )

    stale = client.put(
        "/api/datasets/test/episodes/3/labels",
        json={"labels": ["FAILURE"]},
        headers={"If-Match": '"stale-revision"'},
    )

    assert created.status_code == 200
    assert stale.status_code == 412
    current = client.get("/api/datasets/test/labels")
    assert current.headers["etag"] == created.headers["etag"]
    assert current.json()["episodes"]["3"] == ["SUCCESS"]


def test_save_all_labels_roundtrip(client: TestClient, dataset_service: MagicMock) -> None:
    """POST /labels/save persists current state and returns full file."""
    update_response = client.put(
        "/api/datasets/test/episodes/1/labels",
        json={"labels": ["SUCCESS"]},
        headers=_revision_headers(client, "test"),
    )
    response = client.post(
        "/api/datasets/test/labels/save",
        headers=_revision_headers(client, "test"),
    )
    assert update_response.status_code == 200
    assert update_response.json() == {"episode_index": 1, "labels": ["SUCCESS"]}
    dataset_service.invalidate_episode_cache.assert_called_once_with("test", 1)
    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {"1": ["SUCCESS"]},
        "analysis": {},
    }


def test_delete_label_option_removes_assignments(client: TestClient, dataset_service: MagicMock) -> None:
    """Deleting a label option should also remove it from episode assignments."""
    first_update = client.put(
        "/api/datasets/test-dataset/episodes/1/labels",
        json={"labels": ["SUCCESS", "REVIEW"]},
        headers=_revision_headers(client, "test-dataset"),
    )
    second_update = client.put(
        "/api/datasets/test-dataset/episodes/2/labels",
        json={"labels": ["REVIEW"]},
        headers=_revision_headers(client, "test-dataset"),
    )
    assert first_update.status_code == 200
    assert first_update.json() == {"episode_index": 1, "labels": ["SUCCESS", "REVIEW"]}
    assert second_update.status_code == 200
    assert second_update.json() == {"episode_index": 2, "labels": ["REVIEW"]}
    assert dataset_service.invalidate_episode_cache.call_args_list == [
        call("test-dataset", 1),
        call("test-dataset", 2),
    ]

    response = client.delete(
        "/api/datasets/test-dataset/labels/options/review",
        headers=_revision_headers(client, "test-dataset"),
    )

    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL"]

    labels_response = client.get("/api/datasets/test-dataset/labels")
    assert labels_response.status_code == 200
    assert labels_response.json() == {
        "dataset_id": "test-dataset",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {"1": ["SUCCESS"], "2": []},
        "analysis": {},
    }


def test_delete_default_label_option_rejected(client: TestClient) -> None:
    """Built-in labels should not be deletable."""
    response = client.delete(
        "/api/datasets/test-dataset/labels/options/success",
        headers=_revision_headers(client, "test-dataset"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Built-in labels cannot be deleted"}


def test_delete_label_option_rejects_empty(client: TestClient) -> None:
    """Whitespace-only label name returns 400."""
    response = client.delete(
        "/api/datasets/test/labels/options/%20",
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Label cannot be empty"}


def test_get_episode_analysis_unknown_returns_null(client: TestClient) -> None:
    """GET episode analysis returns null when no record exists."""
    response = client.get("/api/datasets/test/episodes/4/analysis")
    assert response.status_code == 200
    assert response.json() is None


def test_set_and_get_episode_analysis_roundtrip(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    """PUT analysis persists a structured record, invalidates cache, and rides along /labels."""
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
    expected_record = {
        "pick_from": "front",
        "object": "black cloth",
        "grasp_success": True,
        "place_success": False,
        "movement_quality": "Smooth approach then a missed release.",
        "notes": "Gripper opened early.",
        "instruction": None,
        "duration_s": None,
        "smoothness": None,
        "normalized_smoothness": 0.2,
        "efficiency": None,
        "jitter": None,
        "hesitation_count": None,
        "correction_count": None,
        "motion_score": 2,
        "motion_flags": ["jittery"],
        "source": "qwen3-vl",
    }

    put_resp = client.put(
        "/api/datasets/test/episodes/5/analysis",
        json=record,
        headers=_revision_headers(client, "test"),
    )
    assert put_resp.status_code == 200
    assert put_resp.json() == expected_record
    dataset_service.invalidate_episode_cache.assert_called_once_with("test", 5)

    get_resp = client.get("/api/datasets/test/episodes/5/analysis")
    assert get_resp.status_code == 200
    assert get_resp.json() == expected_record

    labels_response = client.get("/api/datasets/test/labels")
    assert labels_response.status_code == 200
    assert labels_response.json() == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {"5": expected_record},
    }


def test_import_analysis_labels_handles_scalar_boolean_and_list_values(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    """Analysis imports normalize supported value types and invalidate the dataset cache."""
    client.put(
        "/api/datasets/test/episodes/0/analysis",
        json={"object": "Black Cloth", "grasp_success": True, "motion_flags": ["jittery", "hesitation"]},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/1/analysis",
        json={"object": "Black Cloth", "grasp_success": False, "motion_flags": []},
        headers=_revision_headers(client, "test"),
    )
    dataset_service.invalidate_episode_cache.reset_mock()

    object_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "prefix": "item"},
        headers=_revision_headers(client, "test"),
    )
    assert object_response.status_code == 200
    assert object_response.json() == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "ITEM: BLACK CLOTH"],
        "episodes": {"0": ["ITEM: BLACK CLOTH"], "1": ["ITEM: BLACK CLOTH"]},
        "field": "object",
        "prefix": "ITEM",
        "labels_added": ["ITEM: BLACK CLOTH"],
        "episodes_updated": 2,
    }

    grasp_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "grasp_success"},
        headers=_revision_headers(client, "test"),
    )
    assert grasp_response.status_code == 200
    assert grasp_response.json() == {
        "dataset_id": "test",
        "available_labels": [
            "SUCCESS",
            "FAILURE",
            "PARTIAL",
            "ITEM: BLACK CLOTH",
            "GRASP: YES",
            "GRASP: NO",
        ],
        "episodes": {
            "0": ["ITEM: BLACK CLOTH", "GRASP: YES"],
            "1": ["ITEM: BLACK CLOTH", "GRASP: NO"],
        },
        "field": "grasp_success",
        "prefix": "GRASP",
        "labels_added": ["GRASP: YES", "GRASP: NO"],
        "episodes_updated": 2,
    }

    flags_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "motion_flags"},
        headers=_revision_headers(client, "test"),
    )
    assert flags_response.status_code == 200
    assert flags_response.json() == {
        "dataset_id": "test",
        "available_labels": [
            "SUCCESS",
            "FAILURE",
            "PARTIAL",
            "ITEM: BLACK CLOTH",
            "GRASP: YES",
            "GRASP: NO",
            "FLAG: JITTERY",
            "FLAG: HESITATION",
        ],
        "episodes": {
            "0": ["ITEM: BLACK CLOTH", "GRASP: YES", "FLAG: JITTERY", "FLAG: HESITATION"],
            "1": ["ITEM: BLACK CLOTH", "GRASP: NO"],
        },
        "field": "motion_flags",
        "prefix": "FLAG",
        "labels_added": ["FLAG: JITTERY", "FLAG: HESITATION"],
        "episodes_updated": 1,
    }
    assert dataset_service.invalidate_episode_cache.call_args_list == [
        call("test"),
        call("test"),
        call("test"),
    ]


def test_import_analysis_labels_overwrites_stale_namespace_and_is_idempotent(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    """Overwrite removes stale namespace values and repeated imports make no changes."""
    client.put(
        "/api/datasets/test/episodes/2/labels",
        json={"labels": ["SUCCESS", "OBJECT: OLD"]},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/2/analysis",
        json={"object": "new"},
        headers=_revision_headers(client, "test"),
    )
    dataset_service.invalidate_episode_cache.reset_mock()

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 200
    expected_payload = {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "OBJECT: NEW"],
        "episodes": {"2": ["SUCCESS", "OBJECT: NEW"]},
        "field": "object",
        "prefix": "OBJECT",
        "labels_added": ["OBJECT: NEW"],
        "episodes_updated": 1,
    }
    assert response.json() == expected_payload
    dataset_service.invalidate_episode_cache.assert_called_once_with("test")

    dataset_service.invalidate_episode_cache.reset_mock()
    repeated = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
        headers=_revision_headers(client, "test"),
    )
    assert repeated.status_code == 200
    assert repeated.json() == {
        **expected_payload,
        "labels_added": [],
        "episodes_updated": 0,
    }
    dataset_service.invalidate_episode_cache.assert_not_called()


def test_import_analysis_labels_overwrite_removes_stale_values_without_current_analysis(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    client.put(
        "/api/datasets/test/episodes/0/labels",
        json={"labels": ["SUCCESS", "OBJECT: OLD"]},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/1/labels",
        json={"labels": ["OBJECT: STALE"]},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/0/analysis",
        json={"object": "new"},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/1/analysis",
        json={"notes": "No object value"},
        headers=_revision_headers(client, "test"),
    )
    dataset_service.invalidate_episode_cache.reset_mock()

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
        headers=_revision_headers(client, "test"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "OBJECT: NEW"],
        "episodes": {"0": ["SUCCESS", "OBJECT: NEW"], "1": []},
        "field": "object",
        "prefix": "OBJECT",
        "labels_added": ["OBJECT: NEW"],
        "episodes_updated": 2,
    }
    dataset_service.invalidate_episode_cache.assert_called_once_with("test")


def test_import_analysis_labels_rejects_unsupported_field(client: TestClient) -> None:
    """Free-text and unknown analysis fields cannot become dataset labels."""
    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "movement_quality"},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": (
            "Field 'movement_quality' is not importable. Allowed: grasp_success, motion_flags, "
            "motion_score, object, pick_from, place_success, source"
        )
    }


def test_import_analysis_labels_without_values_does_not_persist(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    dataset_service: MagicMock,
) -> None:
    """Absent and blank analysis values do not generate labels or persist changes."""
    analysis_response = client.put(
        "/api/datasets/test/episodes/0/analysis",
        json={"source": " "},
        headers=_revision_headers(client, "test"),
    )
    assert analysis_response.status_code == 200

    save = AsyncMock()
    monkeypatch.setattr(labels_mod.LocalLabelStorage, "save", save)
    dataset_service.invalidate_episode_cache.reset_mock()

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "source"},
        headers=_revision_headers(client, "test"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "field": "source",
        "prefix": "SOURCE",
        "labels_added": [],
        "episodes_updated": 0,
    }
    save.assert_not_awaited()
    dataset_service.invalidate_episode_cache.assert_not_called()


@pytest.mark.asyncio
async def test_local_storage_save_then_load_roundtrip(tmp_path: Path) -> None:
    """LocalLabelStorage persists and reloads a labels file."""
    storage = labels_mod.LocalLabelStorage(str(tmp_path))
    original = labels_mod.DatasetLabelsFile(
        dataset_id="ds",
        available_labels=["A", "B"],
        episodes={"1": ["A"]},
    )

    await storage.save("ds", original)
    loaded = await storage.load("ds")

    assert loaded == original
    path = tmp_path / "ds" / "meta" / "episode_labels.json"
    assert json.loads(path.read_text(encoding="utf-8")) == original.model_dump()


def test_label_update_resolves_nested_dataset_id(
    client: TestClient,
    dataset_service: MagicMock,
    tmp_path: Path,
) -> None:
    """The labels API persists nested dataset IDs beneath the configured root."""
    response = client.put(
        "/api/datasets/owner--dataset/episodes/2/labels",
        json={"labels": [" inspect "]},
        headers=_revision_headers(client, "owner--dataset"),
    )

    assert response.status_code == 200
    assert response.json() == {"episode_index": 2, "labels": ["INSPECT"]}
    dataset_service.invalidate_episode_cache.assert_called_once_with("owner--dataset", 2)

    labels_path = tmp_path / "owner" / "dataset" / "meta" / "episode_labels.json"
    assert labels_path.relative_to(tmp_path) == Path("owner/dataset/meta/episode_labels.json")
    assert list(tmp_path.rglob("episode_labels.json")) == [labels_path]
    assert json.loads(labels_path.read_text(encoding="utf-8")) == {
        "dataset_id": "owner--dataset",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "INSPECT"],
        "episodes": {"2": ["INSPECT"]},
        "analysis": {},
    }


def test_azure_configuration_loads_labels_from_blob_provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Azure configuration selects blob-backed labels through the API boundary."""

    class DeterministicBlobProvider:
        container_name = "datasets"

        def __init__(self) -> None:
            self.requested_paths: list[str] = []

        async def _read_blob_bytes(self, blob_path: str) -> bytes:
            self.requested_paths.append(blob_path)
            return json.dumps(
                {
                    "dataset_id": "owner--dataset",
                    "available_labels": ["SUCCESS", "REVIEWED"],
                    "episodes": {"4": ["REVIEWED"]},
                    "analysis": {},
                }
            ).encode()

    provider = DeterministicBlobProvider()
    config = SimpleNamespace(storage_backend="azure")
    provider_factory = MagicMock(return_value=provider)
    monkeypatch.setattr("src.api.config.get_app_config", lambda: config)
    monkeypatch.setattr("src.api.config.create_blob_dataset_provider", provider_factory)

    response = client.get("/api/datasets/owner--dataset/labels")

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "owner--dataset",
        "available_labels": ["SUCCESS", "REVIEWED"],
        "episodes": {"4": ["REVIEWED"]},
        "analysis": {},
    }
    provider_factory.assert_called_once_with(config)
    assert provider.requested_paths == ["owner/dataset/meta/episode_labels.json"]


@pytest.mark.asyncio
async def test_local_storage_load_missing_returns_defaults(tmp_path: Path) -> None:
    """LocalLabelStorage.load returns defaults when no file exists."""
    storage = labels_mod.LocalLabelStorage(str(tmp_path))
    loaded = await storage.load("missing")
    assert loaded.model_dump() == {
        "dataset_id": "missing",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {},
    }


@pytest.mark.asyncio
async def test_blob_label_storage_logs_sanitized_dataset_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid blob content should log a sanitized dataset identifier."""
    logged: list[tuple[object, ...]] = []
    provider = SimpleNamespace(_read_blob_bytes=AsyncMock(return_value=b"not-json"))
    storage = labels_mod.BlobLabelStorage(provider)

    monkeypatch.setattr(
        "src.api.routers.labels.logger.warning",
        lambda message, *args: logged.append((message, *args)),
    )

    result = await storage.load("dataset\r\nname")

    provider._read_blob_bytes.assert_awaited_once_with("dataset\r\nname/meta/episode_labels.json")
    assert result.model_dump() == {
        "dataset_id": "datasetname",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {},
    }
    assert logged == [("Invalid labels blob for %s, returning defaults", "datasetname")]


@pytest.mark.asyncio
async def test_blob_label_storage_load_missing_returns_defaults() -> None:
    """BlobLabelStorage.load returns defaults when blob is absent."""
    provider = SimpleNamespace(_read_blob_bytes=AsyncMock(return_value=None))
    storage = labels_mod.BlobLabelStorage(provider)

    result = await storage.load("ds")
    provider._read_blob_bytes.assert_awaited_once_with("ds/meta/episode_labels.json")
    assert result.model_dump() == {
        "dataset_id": "ds",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {},
    }


@pytest.mark.asyncio
async def test_blob_label_storage_load_uses_provider_etag() -> None:
    """BlobLabelStorage prefers the provider's native ETag when available."""
    blob_client = SimpleNamespace(get_blob_properties=AsyncMock(return_value=SimpleNamespace(etag='"azure-revision"')))
    container = MagicMock()
    container.get_blob_client.return_value = blob_client
    client = MagicMock()
    client.get_container_client.return_value = container
    provider = SimpleNamespace(
        _read_blob_bytes=AsyncMock(
            return_value=json.dumps(labels_mod.DatasetLabelsFile(dataset_id="ds").model_dump()).encode()
        ),
        _get_client=AsyncMock(return_value=client),
        container_name="datasets",
    )
    storage = labels_mod.BlobLabelStorage(provider)

    result = await storage.load_versioned("ds")

    assert result.etag == '"azure-revision"'
    assert result.value is not None
    assert result.value.dataset_id == "ds"


@pytest.mark.asyncio
async def test_blob_label_storage_load_falls_back_when_provider_etag_fails() -> None:
    """BlobLabelStorage retains its content ETag when provider metadata fails."""
    content = json.dumps(labels_mod.DatasetLabelsFile(dataset_id="ds").model_dump()).encode()
    provider = SimpleNamespace(
        _read_blob_bytes=AsyncMock(return_value=content),
        _get_client=AsyncMock(side_effect=RuntimeError("metadata unavailable")),
        container_name="datasets",
    )
    storage = labels_mod.BlobLabelStorage(provider)

    result = await storage.load_versioned("ds")

    assert result.etag == f'"{hashlib.sha256(content).hexdigest()}"'
    assert result.value is not None


@pytest.mark.asyncio
async def test_blob_label_storage_save_uploads_json() -> None:
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

    await storage.save("ds", labels_file)

    provider._get_client.assert_awaited_once_with()
    client.get_container_client.assert_called_once_with("datasets")
    container.get_blob_client.assert_called_once_with("ds/meta/episode_labels.json")
    blob_client.upload_blob.assert_awaited_once()
    args, kwargs = blob_client.upload_blob.await_args
    assert len(args) == 1
    assert json.loads(args[0]) == labels_file.model_dump()
    assert kwargs["overwrite"] is True
    assert set(kwargs) == {"overwrite", "content_settings"}
    if labels_mod.ContentSettings is None:
        assert kwargs["content_settings"] is None
    else:
        assert kwargs["content_settings"].content_type == "application/json"


@pytest.mark.asyncio
async def test_blob_label_storage_save_uses_matching_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blob label updates use Azure native ETag matching."""
    match_conditions = SimpleNamespace(IfNotModified="if-not-modified")
    monkeypatch.setattr(labels_mod, "MatchConditions", match_conditions)
    blob_client = SimpleNamespace(upload_blob=AsyncMock(return_value={"etag": '"revision-two"'}))
    container = MagicMock()
    container.get_blob_client.return_value = blob_client
    client = MagicMock()
    client.get_container_client.return_value = container
    provider = SimpleNamespace(_get_client=AsyncMock(return_value=client), container_name="datasets")
    storage = labels_mod.BlobLabelStorage(provider)

    etag = await storage.save(
        "ds",
        labels_mod.DatasetLabelsFile(dataset_id="ds"),
        if_match='"revision-one"',
    )

    assert etag == '"revision-two"'
    kwargs = blob_client.upload_blob.await_args.kwargs
    assert kwargs["etag"] == '"revision-one"'
    assert kwargs["match_condition"] == "if-not-modified"


@pytest.mark.asyncio
async def test_blob_label_storage_save_uses_create_only_precondition() -> None:
    """Blob label creation sends Azure's create-only precondition."""
    blob_client = SimpleNamespace(upload_blob=AsyncMock(return_value={}))
    container = MagicMock()
    container.get_blob_client.return_value = blob_client
    client = MagicMock()
    client.get_container_client.return_value = container
    provider = SimpleNamespace(_get_client=AsyncMock(return_value=client), container_name="datasets")
    storage = labels_mod.BlobLabelStorage(provider)

    etag = await storage.save(
        "ds",
        labels_mod.DatasetLabelsFile(dataset_id="ds"),
        if_none_match=True,
    )

    kwargs = blob_client.upload_blob.await_args.kwargs
    assert kwargs["overwrite"] is False
    assert kwargs["if_none_match"] == "*"
    assert etag.startswith('"') and etag.endswith('"')


@pytest.mark.asyncio
async def test_blob_label_storage_save_failure_raises_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """BlobLabelStorage.save logs and raises HTTPException(500) on errors."""
    logged: list[tuple[object, ...]] = []
    error = RuntimeError("boom")
    provider = SimpleNamespace(
        _get_client=AsyncMock(side_effect=error),
        container_name="datasets",
    )
    storage = labels_mod.BlobLabelStorage(provider)

    monkeypatch.setattr(
        "src.api.routers.labels.logger.error",
        lambda message, *args: logged.append((message, *args)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await storage.save("ds\r\nx", labels_mod.DatasetLabelsFile(dataset_id="ds"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to save labels"
    provider._get_client.assert_awaited_once_with()
    assert logged == [("Failed to save labels blob for %s: %s", "dsx", error)]


@pytest.mark.parametrize(
    ("if_match", "if_none_match", "expected_status"),
    [
        (None, None, 428),
        ('"revision"', "*", 400),
        (None, '"revision"', 400),
    ],
)
def test_revision_precondition_rejects_invalid_header_combinations(if_match, if_none_match, expected_status):
    """Revision preconditions reject missing, conflicting, and non-wildcard headers."""
    with pytest.raises(HTTPException) as exc_info:
        labels_mod.require_revision_precondition(if_match, if_none_match)

    assert exc_info.value.status_code == expected_status


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
