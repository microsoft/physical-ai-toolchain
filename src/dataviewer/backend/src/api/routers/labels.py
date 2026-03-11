"""Episode label API endpoints.

Provides CRUD endpoints for episode labels (multi-select text tags)
and managing the set of available label options per dataset.
"""

import json
import logging
import os
from pathlib import Path

import aiofiles
import aiofiles.os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..csrf import require_csrf_token
from ..services.dataset_service import DatasetService, get_dataset_service
from ..validation import validate_path_containment, validated_dataset_id

logger = logging.getLogger(__name__)

router = APIRouter()
DEFAULT_LABELS = ["SUCCESS", "FAILURE", "PARTIAL"]


class EpisodeLabels(BaseModel):
    """Labels assigned to a single episode."""

    episode_index: int
    labels: list[str] = Field(default_factory=list)


class DatasetLabelsFile(BaseModel):
    """All episode labels and available options for a dataset."""

    dataset_id: str
    available_labels: list[str] = Field(default_factory=lambda: DEFAULT_LABELS.copy())
    episodes: dict[str, list[str]] = Field(default_factory=dict)


class BulkLabelUpdate(BaseModel):
    """Request body for updating labels on a single episode."""

    labels: list[str]


class AddLabelOption(BaseModel):
    """Request body for adding a new available label option."""

    label: str = Field(min_length=1, max_length=100)


def _normalize_label(label: str) -> str:
    return label.strip().upper()


def _get_base_path() -> str:
    return os.environ.get("HMI_DATA_PATH", "./data")


def _labels_path(dataset_id: str) -> Path:
    base = Path(_get_base_path())
    path = base / dataset_id / "meta" / "episode_labels.json"
    return validate_path_containment(path, base)


async def _load_labels(dataset_id: str) -> DatasetLabelsFile:
    path = _labels_path(dataset_id)
    safe_base = os.path.realpath(_get_base_path())
    resolved = os.path.realpath(str(path))
    if not resolved.startswith(safe_base + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected: labels path escapes base directory",
        )
    path = Path(resolved)
    if not await aiofiles.os.path.exists(path):
        return DatasetLabelsFile(dataset_id=dataset_id)
    async with aiofiles.open(path, encoding="utf-8") as f:
        data = json.loads(await f.read())
        return DatasetLabelsFile.model_validate(data)


async def _save_labels(dataset_id: str, labels_file: DatasetLabelsFile) -> None:
    path = _labels_path(dataset_id)
    safe_base = os.path.realpath(_get_base_path())
    resolved = os.path.realpath(str(path))
    if not resolved.startswith(safe_base + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected: labels path escapes base directory",
        )
    path = Path(resolved)
    await aiofiles.os.makedirs(path.parent, exist_ok=True)
    content = json.dumps(labels_file.model_dump(), indent=2)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)


@router.get("/{dataset_id}/labels")
async def get_dataset_labels(dataset_id: str = Depends(validated_dataset_id)) -> DatasetLabelsFile:
    """Get all episode labels and available label options for a dataset."""
    return await _load_labels(dataset_id)


@router.get("/{dataset_id}/labels/options")
async def get_label_options(dataset_id: str = Depends(validated_dataset_id)) -> list[str]:
    """Get the list of available label options for a dataset."""
    labels_file = await _load_labels(dataset_id)
    return labels_file.available_labels


@router.post("/{dataset_id}/labels/options", dependencies=[Depends(require_csrf_token)])
async def add_label_option(dataset_id: str = Depends(validated_dataset_id), body: AddLabelOption = ...) -> list[str]:
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
    dataset_id: str = Depends(validated_dataset_id),
    label: str = ...,
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
async def get_episode_labels(dataset_id: str = Depends(validated_dataset_id), episode_idx: int = ...) -> EpisodeLabels:
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
    dataset_id: str = Depends(validated_dataset_id),
    episode_idx: int = ...,
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
async def save_all_labels(dataset_id: str = Depends(validated_dataset_id)) -> DatasetLabelsFile:
    """Persist all labels to disk (already persisted on each write, but
    this endpoint lets the frontend trigger an explicit save/confirmation)."""
    labels_file = await _load_labels(dataset_id)
    await _save_labels(dataset_id, labels_file)
    return labels_file
