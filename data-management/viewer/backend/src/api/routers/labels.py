"""Episode label API endpoints.

Provides CRUD endpoints for episode labels (multi-select text tags)
and managing the set of available label options per dataset.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import aiofiles
import aiofiles.os
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import Field

from ..csrf import require_csrf_token
from ..services.dataset_service import DatasetService, get_dataset_service
from ..storage import RevisionConflictError, VersionedValue
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

try:
    from azure.core import MatchConditions
    from azure.core.exceptions import HttpResponseError
    from azure.storage.blob import ContentSettings
except ImportError:
    ContentSettings = None
    MatchConditions = None
    HttpResponseError = None

logger = logging.getLogger(__name__)

router = APIRouter()
DEFAULT_LABELS = ["SUCCESS", "FAILURE", "PARTIAL"]


class EpisodeLabels(SanitizedModel):
    """Labels assigned to a single episode."""

    episode_index: int
    labels: list[str] = Field(default_factory=list)


class EpisodeAnalysisRecord(SanitizedModel):
    """Structured per-episode analysis: VLM-derived labels plus computed motion metrics.

    Persisted beside the dataset in ``meta/episode_labels.json`` so it loads
    automatically with the dataset. All fields are optional so partial records
    (VLM-only or motion-only) round-trip cleanly.
    """

    pick_from: str | None = None
    object: str | None = None
    grasp_success: bool | None = None
    place_success: bool | None = None
    movement_quality: str | None = None
    notes: str | None = None
    instruction: str | None = None
    duration_s: float | None = None
    smoothness: float | None = None
    normalized_smoothness: float | None = None
    efficiency: float | None = None
    jitter: float | None = None
    hesitation_count: int | None = None
    correction_count: int | None = None
    motion_score: int | None = None
    motion_flags: list[str] = Field(default_factory=list)
    source: str | None = None


class DatasetLabelsFile(SanitizedModel):
    """All episode labels and available options for a dataset."""

    dataset_id: str
    available_labels: list[str] = Field(default_factory=lambda: DEFAULT_LABELS.copy())
    episodes: dict[str, list[str]] = Field(default_factory=dict)
    analysis: dict[str, EpisodeAnalysisRecord] = Field(default_factory=dict)


class BulkLabelUpdate(SanitizedModel):
    """Request body for updating labels on a single episode."""

    labels: list[str]


class AddLabelOption(SanitizedModel):
    """Request body for adding a new available label option."""

    label: str = Field(min_length=1, max_length=100)


# Analysis fields that make sensible categorical labels, mapped to their default
# label prefix. Free-text fields (movement_quality, notes, instruction) are
# intentionally excluded because they produce unbounded, unfilterable labels.
ANALYSIS_IMPORT_FIELDS: dict[str, str] = {
    "object": "OBJECT",
    "pick_from": "PICK",
    "grasp_success": "GRASP",
    "place_success": "PLACE",
    "motion_score": "MOTION",
    "motion_flags": "FLAG",
    "source": "SOURCE",
}


class ImportAnalysisRequest(SanitizedModel):
    """Request to promote an analysis field into filterable episode labels."""

    field: str = Field(min_length=1, max_length=64)
    prefix: str | None = Field(default=None, max_length=32)
    overwrite: bool = False


class ImportAnalysisResult(SanitizedModel):
    """Outcome of importing an analysis field into episode labels."""

    dataset_id: str
    available_labels: list[str]
    episodes: dict[str, list[str]]
    field: str
    prefix: str
    labels_added: list[str]
    episodes_updated: int


def _normalize_label(label: str) -> str:
    return label.strip().upper()


def _analysis_value_labels(prefix: str, value: object) -> list[str]:
    """Turn an analysis field value into zero or more normalized labels.

    Lists (e.g. motion_flags) yield one label per item; booleans render as
    yes/no; everything else is stringified. Empty values yield nothing.
    """
    items = value if isinstance(value, list) else [value]
    labels: list[str] = []
    for item in items:
        if item is None:
            continue
        text = ("yes" if item else "no") if isinstance(item, bool) else str(item).strip()
        if not text:
            continue
        labels.append(_normalize_label(f"{prefix}: {text}"))
    return labels


# ============================================================================
# Label Storage Backends
# ============================================================================


class LabelStorage(Protocol):
    """Protocol for label persistence backends."""

    async def load(self, dataset_id: str) -> DatasetLabelsFile:
        """Load labels for a dataset."""

    async def load_versioned(self, dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
        """Load labels and their strong validator."""

    async def save(
        self,
        dataset_id: str,
        labels_file: DatasetLabelsFile,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        """Persist labels for a dataset."""


class LocalLabelStorage:
    """Filesystem-backed label storage."""

    def __init__(self, base_path: str) -> None:
        self._base_path = base_path
        self._resource_locks: dict[Path, asyncio.Lock] = {}

    def _path(self, dataset_id: str) -> Path:
        return _labels_path_for_base(dataset_id, self._base_path)

    def _resource_lock(self, path: Path) -> asyncio.Lock:
        return self._resource_locks.setdefault(path, asyncio.Lock())

    @staticmethod
    def _serialize(labels_file: DatasetLabelsFile) -> str:
        return json.dumps(labels_file.model_dump(), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _etag(content: str) -> str:
        return f'"{hashlib.sha256(content.encode()).hexdigest()}"'

    async def load(self, dataset_id: str) -> DatasetLabelsFile:
        return (await self.load_versioned(dataset_id)).value or DatasetLabelsFile(dataset_id=dataset_id)

    async def load_versioned(self, dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
        path = self._path(dataset_id)
        safe_base = os.path.realpath(self._base_path)
        resolved = os.path.realpath(str(path))
        if not resolved.startswith(safe_base + os.sep):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        path = Path(resolved)
        if not await aiofiles.os.path.exists(path):
            return VersionedValue(value=DatasetLabelsFile(dataset_id=dataset_id), etag=None)
        async with aiofiles.open(path, encoding="utf-8") as f:
            content = await f.read()
            return VersionedValue(
                value=DatasetLabelsFile.model_validate(json.loads(content)),
                etag=self._etag(content),
            )

    async def save(
        self,
        dataset_id: str,
        labels_file: DatasetLabelsFile,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        path = self._path(dataset_id)
        safe_base = os.path.realpath(self._base_path)
        resolved = os.path.realpath(str(path))
        if not resolved.startswith(safe_base + os.sep):
            raise HTTPException(status_code=400, detail="Path traversal detected")
        path = Path(resolved)
        async with self._resource_lock(path):
            await aiofiles.os.makedirs(path.parent, exist_ok=True)
            current_content = None
            if await aiofiles.os.path.exists(path):
                async with aiofiles.open(path, encoding="utf-8") as file:
                    current_content = await file.read()
            current_etag = self._etag(current_content) if current_content is not None else None
            if if_none_match and current_content is not None:
                raise RevisionConflictError(current_etag)
            if if_match is not None and if_match != current_etag:
                raise RevisionConflictError(current_etag)

            content = self._serialize(labels_file)
            file_descriptor, temporary_path = await asyncio.to_thread(
                tempfile.mkstemp,
                dir=str(path.parent),
                suffix=".tmp",
                prefix="labels_",
            )
            try:
                async with aiofiles.open(file_descriptor, "w", encoding="utf-8") as file:
                    await file.write(content)
                await asyncio.to_thread(os.replace, temporary_path, str(path))
            except Exception:
                if await asyncio.to_thread(os.path.exists, temporary_path):
                    await asyncio.to_thread(os.unlink, temporary_path)
                raise
            return self._etag(content)


class BlobLabelStorage:
    """Azure Blob Storage-backed label storage. Stores in datasets container."""

    def __init__(self, blob_provider: BlobDatasetProvider) -> None:
        self._provider = blob_provider

    def _blob_path(self, dataset_id: str) -> str:
        return f"{dataset_id_to_blob_prefix(dataset_id)}/meta/episode_labels.json"

    async def load(self, dataset_id: str) -> DatasetLabelsFile:
        return (await self.load_versioned(dataset_id)).value or DatasetLabelsFile(dataset_id=dataset_id)

    async def load_versioned(self, dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
        data = await self._provider._read_blob_bytes(self._blob_path(dataset_id))
        if data is None:
            return VersionedValue(value=DatasetLabelsFile(dataset_id=dataset_id), etag=None)
        etag = f'"{hashlib.sha256(data).hexdigest()}"'
        get_client = getattr(self._provider, "_get_client", None)
        if get_client is not None:
            try:
                client = await get_client()
                blob_client = client.get_container_client(self._provider.container_name).get_blob_client(
                    self._blob_path(dataset_id)
                )
                properties = await blob_client.get_blob_properties()
                azure_etag = getattr(properties, "etag", None)
                if azure_etag:
                    etag = str(azure_etag)
            except Exception:
                logger.debug(
                    "Unable to read Azure label ETag for %s",
                    dataset_id.replace("\r", "").replace("\n", ""),
                    exc_info=True,
                )
        try:
            return VersionedValue(
                value=DatasetLabelsFile.model_validate(json.loads(data.decode("utf-8"))),
                etag=etag,
            )
        except (json.JSONDecodeError, Exception):
            logger.warning(
                "Invalid labels blob for %s, returning defaults",
                dataset_id.replace("\r", "").replace("\n", ""),
            )
            return VersionedValue(value=DatasetLabelsFile(dataset_id=dataset_id), etag=etag)

    async def save(
        self,
        dataset_id: str,
        labels_file: DatasetLabelsFile,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        try:
            client = await self._provider._get_client()
            container = client.get_container_client(self._provider.container_name)
            blob_client = container.get_blob_client(self._blob_path(dataset_id))
            content = json.dumps(labels_file.model_dump(), sort_keys=True, separators=(",", ":")).encode("utf-8")
            content_settings = ContentSettings(content_type="application/json") if ContentSettings is not None else None
            conditions: dict[str, object] = {"overwrite": True}
            if if_match is not None:
                conditions.update(etag=if_match, match_condition=MatchConditions.IfNotModified)
            elif if_none_match:
                conditions.update(overwrite=False, if_none_match="*")
            result = await blob_client.upload_blob(
                content,
                content_settings=content_settings,
                **conditions,
            )
            result_etag = result.get("etag") if isinstance(result, dict) else getattr(result, "etag", None)
            return str(result_etag) if result_etag else f'"{hashlib.sha256(content).hexdigest()}"'
        except Exception as e:
            if HttpResponseError is not None and isinstance(e, HttpResponseError) and e.status_code == 412:
                response = getattr(e, "response", None)
                headers = getattr(response, "headers", {})
                raise RevisionConflictError(headers.get("ETag") if headers else None) from e
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
    return LocalLabelStorage(os.environ.get("DATA_DIR", "./data"))


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
    return os.environ.get("DATA_DIR", "./data")


def _labels_path_for_base(dataset_id: str, base_path: str) -> Path:
    """Build labels path, resolving -- to nested directories."""
    base = Path(base_path)
    parts = dataset_id.split("--") if "--" in dataset_id else [dataset_id]
    return validate_path_containment(base.joinpath(*parts, "meta", "episode_labels.json"), base)


def _labels_path(dataset_id: str) -> Path:
    return _labels_path_for_base(dataset_id, _get_base_path())


async def _load_labels(dataset_id: str) -> DatasetLabelsFile:
    return await _get_label_storage().load(dataset_id)


async def _load_labels_versioned(dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
    return await _get_label_storage().load_versioned(dataset_id)


@dataclass(frozen=True)
class RevisionPrecondition:
    if_match: str | None
    if_none_match: bool


def require_revision_precondition(
    if_match: str | None = Header(default=None),
    if_none_match: str | None = Header(default=None),
) -> RevisionPrecondition:
    """Require exactly one strong revision precondition."""
    if if_match is None and if_none_match is None:
        raise HTTPException(status_code=428, detail="If-Match or If-None-Match is required")
    if if_match is not None and if_none_match is not None:
        raise HTTPException(status_code=400, detail="Specify only one revision precondition")
    if if_none_match is not None and if_none_match != "*":
        raise HTTPException(status_code=400, detail="If-None-Match must be '*'")
    return RevisionPrecondition(if_match=if_match, if_none_match=if_none_match == "*")


async def _load_labels_for_update(
    dataset_id: str,
    precondition: RevisionPrecondition,
) -> DatasetLabelsFile:
    versioned = await _load_labels_versioned(dataset_id)
    if precondition.if_none_match and versioned.etag is not None:
        raise RevisionConflictError(versioned.etag)
    if precondition.if_match is not None and precondition.if_match != versioned.etag:
        raise RevisionConflictError(versioned.etag)
    return versioned.value or DatasetLabelsFile(dataset_id=dataset_id)


async def _save_labels(
    dataset_id: str,
    labels_file: DatasetLabelsFile,
    precondition: RevisionPrecondition,
) -> str:
    return await _get_label_storage().save(
        dataset_id,
        labels_file,
        if_match=precondition.if_match,
        if_none_match=precondition.if_none_match,
    )


@router.get("/{dataset_id}/labels")
async def get_dataset_labels(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
) -> DatasetLabelsFile:
    """Get all episode labels and available label options for a dataset."""
    versioned = await _load_labels_versioned(dataset_id)
    if versioned.etag:
        response.headers["ETag"] = versioned.etag
    return versioned.value or DatasetLabelsFile(dataset_id=dataset_id)


@router.get("/{dataset_id}/labels/options")
async def get_label_options(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
) -> list[str]:
    """Get the list of available label options for a dataset."""
    labels_file = await _load_labels(dataset_id)
    return labels_file.available_labels


@router.post("/{dataset_id}/labels/options", dependencies=[Depends(require_csrf_token)])
async def add_label_option(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    body: AddLabelOption = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
) -> list[str]:
    """Add a new label option to the available set."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    normalized = _normalize_label(body.label)
    if not normalized:
        raise HTTPException(status_code=400, detail="Label cannot be empty")
    if normalized not in labels_file.available_labels:
        labels_file.available_labels.append(normalized)
    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    return labels_file.available_labels


@router.delete(
    "/{dataset_id}/labels/options/{label}",
    dependencies=[Depends(require_csrf_token)],
)
async def delete_label_option(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    label: str = Depends(path_string_param("label", label="label")),
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
) -> list[str]:
    """Delete a label option and remove it from all episode assignments."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
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

    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
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
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    body: BulkLabelUpdate = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> EpisodeLabels:
    """Set labels for a specific episode (replaces existing labels)."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    key = str(episode_idx)

    # Auto-add any new labels to available options
    for label in body.labels:
        normalized = _normalize_label(label)
        if normalized and normalized not in labels_file.available_labels:
            labels_file.available_labels.append(normalized)

    labels_file.episodes[key] = [normalized for label in body.labels if (normalized := _normalize_label(label))]
    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    dataset_service.invalidate_episode_cache(dataset_id, episode_idx)

    return EpisodeLabels(
        episode_index=episode_idx,
        labels=labels_file.episodes[key],
    )


@router.post("/{dataset_id}/labels/save", dependencies=[Depends(require_csrf_token)])
async def save_all_labels(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
) -> DatasetLabelsFile:
    """Persist all labels to disk (already persisted on each write, but
    this endpoint lets the frontend trigger an explicit save/confirmation)."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    return labels_file


@router.post(
    "/{dataset_id}/labels/import-from-analysis",
    dependencies=[Depends(require_csrf_token)],
)
async def import_analysis_labels(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    body: ImportAnalysisRequest = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> ImportAnalysisResult:
    """Promote a categorical analysis field into filterable/editable episode labels.

    Each episode with a value for ``field`` gains a namespaced label such as
    ``OBJECT: MARKER``. Labels merge into existing assignments and are added to
    the available options, so the operation is idempotent. When ``overwrite`` is
    set, labels sharing the prefix are cleared first so stale values drop out.
    """
    field = body.field.strip()
    if field not in ANALYSIS_IMPORT_FIELDS:
        allowed = ", ".join(sorted(ANALYSIS_IMPORT_FIELDS))
        raise HTTPException(status_code=400, detail=f"Field '{field}' is not importable. Allowed: {allowed}")

    prefix = _normalize_label(body.prefix) if body.prefix else ANALYSIS_IMPORT_FIELDS[field]
    namespace = f"{prefix}:"
    labels_file = await _load_labels_for_update(dataset_id, precondition)

    generated_by_episode: dict[str, list[str]] = {}
    generated_options: list[str] = []
    for key, record in labels_file.analysis.items():
        generated = _analysis_value_labels(prefix, getattr(record, field, None))
        generated_by_episode[key] = generated
        for label in generated:
            if label not in generated_options:
                generated_options.append(label)

    original_options = labels_file.available_labels.copy()
    if body.overwrite:
        next_options = [label for label in original_options if not label.startswith(namespace)]
    else:
        next_options = original_options.copy()
    for label in generated_options:
        if label not in next_options:
            next_options.append(label)
    labels_file.available_labels = next_options
    added_options = [label for label in generated_options if label not in original_options]

    updated: set[str] = set()
    episode_keys = set(labels_file.episodes) | set(generated_by_episode)
    for key in episode_keys:
        current = labels_file.episodes.get(key, [])
        merged = [existing for existing in current if not (body.overwrite and existing.startswith(namespace))]
        for label in generated_by_episode.get(key, []):
            if label not in merged:
                merged.append(label)

        if merged != current:
            labels_file.episodes[key] = merged
            updated.add(key)

    if updated or labels_file.available_labels != original_options:
        response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
        dataset_service.invalidate_episode_cache(dataset_id)
    elif precondition.if_match:
        response.headers["ETag"] = precondition.if_match

    return ImportAnalysisResult(
        dataset_id=dataset_id,
        available_labels=labels_file.available_labels,
        episodes=labels_file.episodes,
        field=field,
        prefix=prefix,
        labels_added=added_options,
        episodes_updated=len(updated),
    )


@router.get("/{dataset_id}/episodes/{episode_idx}/analysis")
async def get_episode_analysis(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
) -> EpisodeAnalysisRecord | None:
    """Get the structured analysis record for a specific episode, if any."""
    labels_file = await _load_labels(dataset_id)
    return labels_file.analysis.get(str(episode_idx))


@router.put(
    "/{dataset_id}/episodes/{episode_idx}/analysis",
    dependencies=[Depends(require_csrf_token)],
)
async def set_episode_analysis(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    episode_idx: int = Depends(path_int_param("episode_idx", ge=0, description="Episode index")),
    body: EpisodeAnalysisRecord = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> EpisodeAnalysisRecord:
    """Upsert the structured analysis record for a specific episode."""
    labels_file = await _load_labels_for_update(dataset_id, precondition)
    labels_file.analysis[str(episode_idx)] = body
    response.headers["ETag"] = await _save_labels(dataset_id, labels_file, precondition)
    dataset_service.invalidate_episode_cache(dataset_id, episode_idx)
    return body
