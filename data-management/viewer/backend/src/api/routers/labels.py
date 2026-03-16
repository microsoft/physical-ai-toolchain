"""Episode label API endpoints.

Provides CRUD endpoints for episode labels (multi-select text tags)
and managing the set of available label options per dataset.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import aiofiles
import aiofiles.os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from ..csrf import require_csrf_token
from ..services.dataset_service import DatasetService, get_dataset_service
from ..storage.paths import dataset_id_to_blob_prefix
from ..validation import (
    SAFE_DATASET_ID_PATTERN,
    SanitizedModel,
    path_int_param,
    path_string_param,
    validate_path_containment,
)

if TYPE_CHECKING:
    from ..storage.blob_dataset import BlobDatasetProvider

logger = logging.getLogger(__name__)

router = APIRouter()
DEFAULT_LABELS = ["SUCCESS", "FAILURE", "PARTIAL"]


class EpisodeLabels(SanitizedModel):
    """Labels assigned to a single episode."""

    episode_index: int
    labels: list[str] = Field(default_factory=list)


class DatasetLabelsFile(SanitizedModel):
    """All episode labels and available options for a dataset."""

    dataset_id: str
    available_labels: list[str] = Field(default_factory=lambda: DEFAULT_LABELS.copy())
    episodes: dict[str, list[str]] = Field(default_factory=dict)


class BulkLabelUpdate(SanitizedModel):
    """Request body for updating labels on a single episode."""

    labels: list[str]


class AddLabelOption(SanitizedModel):
    """Request body for adding a new available label option."""

    label: str = Field(min_length=1, max_length=100)


def _normalize_label(label: str) -> str:
    return label.strip().upper()


# ============================================================================
# Label Storage Backends
# ============================================================================


class LabelStorage(Protocol):
    """Protocol for label persistence backends."""

    async def load(self, dataset_id: str) -> DatasetLabelsFile:
        """Load labels for a dataset."""

    async def save(self, dataset_id: str, labels_file: DatasetLabelsFile) -> None:
        """Persist labels for a dataset."""


class LocalLabelStorage:
    """Filesystem-backed label storage."""

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path

    def _path(self, dataset_id: str) -> Path:
        return _labels_path_for_base(dataset_id, self._base_path)

    async def load(self, dataset_id: str) -> DatasetLabelsFile:
        path = self._path(dataset_id)
        safe_base = os.path.realpath(self._base_path)
        resolved = os.path.realpath(str(path))
        if not resolved.startswith(safe_base + os.sep):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        path = Path(resolved)
        if not await aiofiles.os.path.exists(path):
            return DatasetLabelsFile(dataset_id=dataset_id)
        async with aiofiles.open(path, encoding="utf-8") as f:
            data = json.loads(await f.read())
            return DatasetLabelsFile.model_validate(data)

    async def save(self, dataset_id: str, labels_file: DatasetLabelsFile) -> None:
        path = self._path(dataset_id)
        safe_base = os.path.realpath(self._base_path)
        resolved = os.path.realpath(str(path))
        if not resolved.startswith(safe_base + os.sep):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        path = Path(resolved)
        await aiofiles.os.makedirs(path.parent, exist_ok=True)
        content = json.dumps(labels_file.model_dump(), indent=2)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)


class BlobLabelStorage:
    """Azure Blob Storage-backed label storage. Stores in datasets container."""

    def __init__(self, blob_provider: BlobDatasetProvider) -> None:
        self._provider = blob_provider

    def _blob_path(self, dataset_id: str) -> str:
        return f"{dataset_id_to_blob_prefix(dataset_id)}/meta/episode_labels.json"

    async def load(self, dataset_id: str) -> DatasetLabelsFile:
        data = await self._provider._read_blob_bytes(self._blob_path(dataset_id))
        if data is None:
            return DatasetLabelsFile(dataset_id=dataset_id)
        try:
            return DatasetLabelsFile.model_validate(json.loads(data.decode("utf-8")))
        except (json.JSONDecodeError, Exception):
            logger.warning(
                "Invalid labels blob for %s, returning defaults",
                dataset_id.replace("\r", "").replace("\n", ""),
            )
            return DatasetLabelsFile(dataset_id=dataset_id)

    async def save(self, dataset_id: str, labels_file: DatasetLabelsFile) -> None:
        try:
            from azure.storage.blob import ContentSettings

            client = await self._provider._get_client()
            container = client.get_container_client(self._provider.container_name)
            blob_client = container.get_blob_client(self._blob_path(dataset_id))
            content = json.dumps(labels_file.model_dump(), indent=2).encode("utf-8")
            await blob_client.upload_blob(
                content,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
        except Exception as e:
            logger.error(
                "Failed to save labels blob for %s: %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                e,
            )
            raise HTTPException(status_code=500, detail="Failed to save labels") from e


def _create_label_storage(
    storage_backend: str = "local",
    blob_provider: BlobDatasetProvider | None = None,
) -> LabelStorage:
    """Create label storage backend based on config."""
    if storage_backend == "azure" and blob_provider is not None:
        return BlobLabelStorage(blob_provider)
    return LocalLabelStorage(os.environ.get("HMI_DATA_PATH", "./data"))


_label_storage: LabelStorage | None = None


def _get_label_storage() -> LabelStorage:
    """Get or create the global label storage singleton."""
    global _label_storage
    if _label_storage is None:
        from ..config import get_app_config

        config = get_app_config()
        blob_provider = None
        if config.storage_backend == "azure":
            from ..config import create_blob_dataset_provider

            blob_provider = create_blob_dataset_provider(config)
        _label_storage = _create_label_storage(config.storage_backend, blob_provider)
    return _label_storage


def _get_base_path() -> str:
    return os.environ.get("HMI_DATA_PATH", "./data")


def _labels_path_for_base(dataset_id: str, base_path: str) -> Path:
    """Build labels path, resolving -- to nested directories."""
    base = Path(base_path)
    parts = dataset_id.split("--") if "--" in dataset_id else [dataset_id]
    return validate_path_containment(base.joinpath(*parts, "meta", "episode_labels.json"), base)


def _labels_path(dataset_id: str) -> Path:
    return _labels_path_for_base(dataset_id, _get_base_path())


async def _load_labels(dataset_id: str) -> DatasetLabelsFile:
    return await _get_label_storage().load(dataset_id)


async def _save_labels(dataset_id: str, labels_file: DatasetLabelsFile) -> None:
    await _get_label_storage().save(dataset_id, labels_file)


@router.get("/{dataset_id}/labels")
async def get_dataset_labels(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
) -> DatasetLabelsFile:
    """Get all episode labels and available label options for a dataset."""
    return await _load_labels(dataset_id)


@router.get("/{dataset_id}/labels/options")
async def get_label_options(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
) -> list[str]:
    """Get the list of available label options for a dataset."""
    labels_file = await _load_labels(dataset_id)
    return labels_file.available_labels


@router.post("/{dataset_id}/labels/options", dependencies=[Depends(require_csrf_token)])
async def add_label_option(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    body: AddLabelOption = ...,
) -> list[str]:
    """Add a new label option to the available set."""
    labels_file = await _load_labels(dataset_id)
    normalized = _normalize_label(body.label)
    if not normalized:
        raise HTTPException(status_code=400, detail="Label cannot be empty")
    if normalized not in labels_file.available_labels:
        labels_file.available_labels.append(normalized)
        await _save_labels(dataset_id, labels_file)
    return labels_file.available_labels


@router.delete(
    "/{dataset_id}/labels/options/{label}",
    dependencies=[Depends(require_csrf_token)],
)
async def delete_label_option(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    label: str = Depends(path_string_param("label", label="label")),
) -> list[str]:
    """Delete a label option and remove it from all episode assignments."""
    labels_file = await _load_labels(dataset_id)
    normalized = _normalize_label(label)

    if not normalized:
        raise HTTPException(status_code=400, detail="Label cannot be empty")

    if normalized in DEFAULT_LABELS:
        raise HTTPException(status_code=400, detail="Built-in labels cannot be deleted")

    labels_file.available_labels = [existing for existing in labels_file.available_labels if existing != normalized]

    labels_file.episodes = {
        episode_idx: [existing for existing in labels if existing != normalized]
        for episode_idx, labels in labels_file.episodes.items()
    }

    await _save_labels(dataset_id, labels_file)
    return labels_file.available_labels


@router.get("/{dataset_id}/episodes/{episode_idx}/labels")
async def get_episode_labels(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
) -> EpisodeLabels:
    """Get labels for a specific episode."""
    labels_file = await _load_labels(dataset_id)
    key = str(episode_idx)
    return EpisodeLabels(
        episode_index=episode_idx,
        labels=labels_file.episodes.get(key, []),
    )


@router.put(
    "/{dataset_id}/episodes/{episode_idx}/labels",
    dependencies=[Depends(require_csrf_token)],
)
async def set_episode_labels(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    body: BulkLabelUpdate = ...,
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> EpisodeLabels:
    """Set labels for a specific episode (replaces existing labels)."""
    labels_file = await _load_labels(dataset_id)
    key = str(episode_idx)

    # Auto-add any new labels to available options
    for label in body.labels:
        normalized = _normalize_label(label)
        if normalized and normalized not in labels_file.available_labels:
            labels_file.available_labels.append(normalized)

    labels_file.episodes[key] = [normalized for label in body.labels if (normalized := _normalize_label(label))]
    await _save_labels(dataset_id, labels_file)
    dataset_service.invalidate_episode_cache(dataset_id, episode_idx)

    return EpisodeLabels(
        episode_index=episode_idx,
        labels=labels_file.episodes[key],
    )


@router.post("/{dataset_id}/labels/save", dependencies=[Depends(require_csrf_token)])
async def save_all_labels(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
) -> DatasetLabelsFile:
    """Persist all labels to disk (already persisted on each write, but
    this endpoint lets the frontend trigger an explicit save/confirmation)."""
    labels_file = await _load_labels(dataset_id)
    await _save_labels(dataset_id, labels_file)
    return labels_file
