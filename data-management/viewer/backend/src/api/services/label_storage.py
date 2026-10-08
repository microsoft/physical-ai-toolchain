"""Conditional local and Blob label persistence shared by viewer mutation services."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import aiofiles
from evaluation.vlm_judge.curation import DatasetLabelsFile
from fastapi import HTTPException
from pydantic import ValidationError

from ..storage import RevisionConflictError, VersionedValue
from ..storage.local_revision import write_conditional
from ..storage.paths import dataset_id_to_blob_prefix
from ..validation import validate_path_containment

if TYPE_CHECKING:
    from ..storage.blob_dataset import BlobDatasetProvider

try:
    from azure.core import MatchConditions
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
    from azure.storage.blob import ContentSettings
except ImportError:
    ContentSettings = None
    MatchConditions = None
    HttpResponseError = None
    ResourceNotFoundError = None

logger = logging.getLogger(__name__)


def _labels_path_for_base(dataset_id: str, base_path: str) -> Path:
    """Build labels path, resolving -- to nested directories."""
    base = Path(base_path)
    parts = dataset_id.split("--") if "--" in dataset_id else [dataset_id]
    return validate_path_containment(base.joinpath(*parts, "meta", "episode_labels.json"), base)


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

    def _path(self, dataset_id: str) -> Path:
        return _labels_path_for_base(dataset_id, self._base_path)

    @staticmethod
    def _serialize(labels_file: DatasetLabelsFile) -> str:
        return json.dumps(labels_file.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

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
        try:
            async with aiofiles.open(path, encoding="utf-8", newline="") as labels_file:
                content = await labels_file.read()
        except FileNotFoundError:
            return VersionedValue(value=DatasetLabelsFile(dataset_id=dataset_id), etag=None)
        except (OSError, UnicodeError) as error:
            logger.error("Failed to read local labels: %s", type(error).__name__)
            raise HTTPException(status_code=500, detail="Failed to read labels") from None
        try:
            value = DatasetLabelsFile.model_validate_json(content)
        except ValidationError:
            logger.warning("Invalid local labels for dataset %s", dataset_id.replace("\r", "").replace("\n", ""))
            raise HTTPException(status_code=500, detail="Invalid labels content") from None
        return VersionedValue(value=value, etag=self._etag(content))

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
        return await asyncio.to_thread(
            write_conditional, path, self._serialize(labels_file), if_match=if_match, if_none_match=if_none_match
        )


class BlobLabelStorage:
    """Azure Blob Storage-backed label storage. Stores in datasets container."""

    def __init__(self, blob_provider: BlobDatasetProvider) -> None:
        self._provider = blob_provider

    def _blob_path(self, dataset_id: str) -> str:
        return f"{dataset_id_to_blob_prefix(dataset_id)}/meta/episode_labels.json"

    async def load(self, dataset_id: str) -> DatasetLabelsFile:
        return (await self.load_versioned(dataset_id)).value or DatasetLabelsFile(dataset_id=dataset_id)

    async def load_versioned(self, dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
        logger.debug("Reading versioned labels blob for %s", dataset_id.replace("\r", "").replace("\n", ""))
        try:
            client = await self._provider._get_client()
            blob_client = client.get_container_client(self._provider.container_name).get_blob_client(
                self._blob_path(dataset_id)
            )
            download = await blob_client.download_blob()
            data = await download.readall()
            etag = download.properties.etag
            if not etag:
                raise ValueError("Missing Azure label revision")
        except Exception as error:
            if ResourceNotFoundError is not None and isinstance(error, ResourceNotFoundError):
                logger.debug(
                    "Labels blob absent for %s; returning unpersisted defaults",
                    dataset_id.replace("\r", "").replace("\n", ""),
                )
                return VersionedValue(value=DatasetLabelsFile(dataset_id=dataset_id), etag=None)
            logger.error(
                "Failed to load labels blob for %s (%s)",
                dataset_id.replace("\r", "").replace("\n", ""),
                type(error).__name__,
            )
            raise HTTPException(status_code=500, detail="Failed to load labels") from error
        try:
            result = VersionedValue(
                value=DatasetLabelsFile.model_validate_json(data),
                etag=str(etag),
            )
        except ValidationError as error:
            logger.warning(
                "Invalid labels blob for %s; refusing to substitute defaults (%d validation errors)",
                dataset_id.replace("\r", "").replace("\n", ""),
                error.error_count(),
            )
            raise HTTPException(status_code=500, detail="Invalid labels data") from error
        logger.debug(
            "Loaded labels blob for %s with matching download revision (%d bytes)",
            dataset_id.replace("\r", "").replace("\n", ""),
            len(data),
        )
        return result

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
            content = LocalLabelStorage._serialize(labels_file).encode("utf-8")
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
            if (
                HttpResponseError is not None
                and isinstance(e, HttpResponseError)
                and (
                    e.status_code == 412
                    or (
                        if_none_match and e.status_code == 409 and getattr(e, "error_code", None) == "BlobAlreadyExists"
                    )
                )
            ):
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
    if storage_backend == "azure":
        if blob_provider is None:
            logger.error("Azure label storage provider unavailable; refusing local fallback")
            raise HTTPException(status_code=503, detail="Azure label storage unavailable")
        return BlobLabelStorage(blob_provider)
    return LocalLabelStorage(os.environ.get("DATA_DIR", "./data"))
