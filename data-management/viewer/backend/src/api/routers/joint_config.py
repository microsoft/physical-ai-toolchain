"""Joint configuration API endpoints.

Provides read/write endpoints for per-dataset joint labels and groupings,
plus global defaults stored at the datasets root directory.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field, ValidationError

from ..config import get_app_config
from ..csrf import require_csrf_token
from ..services.dataset_service import DatasetService, get_dataset_service
from ..storage.base import RevisionConflictError, VersionedValue
from ..storage.blob_dataset import BlobDatasetProvider
from ..storage.local_revision import content_etag, write_conditional
from ..validation import SAFE_DATASET_ID_PATTERN, SanitizedModel, path_string_param, validate_path_containment
from .labels import RevisionPrecondition, require_revision_precondition

try:
    from azure.core import MatchConditions
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
    from azure.storage.blob import ContentSettings
except ImportError:
    MatchConditions = None
    HttpResponseError = None
    ResourceNotFoundError = None
    ContentSettings = None

logger = logging.getLogger(__name__)

router = APIRouter()
defaults_router = APIRouter()


class JointGroupConfig(SanitizedModel):
    """A named group of joint indices."""

    id: str
    label: str
    indices: list[int] = Field(default_factory=list)


class JointConfig(SanitizedModel):
    """Joint labels and groupings for a dataset."""

    dataset_id: str
    labels: dict[str, str] = Field(default_factory=dict)
    groups: list[JointGroupConfig] = Field(default_factory=list)


class JointConfigUpdate(SanitizedModel):
    """Request body for updating joint configuration."""

    labels: dict[str, str] = Field(default_factory=dict)
    groups: list[JointGroupConfig] = Field(default_factory=list)


_DEFAULT_LABELS: dict[str, str] = {
    "0": "Right X",
    "1": "Right Y",
    "2": "Right Z",
    "3": "Right Qx",
    "4": "Right Qy",
    "5": "Right Qz",
    "6": "Right Qw",
    "7": "Right Gripper",
    "8": "Left X",
    "9": "Left Y",
    "10": "Left Z",
    "11": "Left Qx",
    "12": "Left Qy",
    "13": "Left Qz",
    "14": "Left Qw",
    "15": "Left Gripper",
}

_DEFAULT_GROUPS: list[dict] = [
    {"id": "right-pos", "label": "Right Arm", "indices": [0, 1, 2]},
    {"id": "right-orient", "label": "Right Orientation", "indices": [3, 4, 5, 6]},
    {"id": "right-grip", "label": "Right Gripper", "indices": [7]},
    {"id": "left-pos", "label": "Left Arm", "indices": [8, 9, 10]},
    {"id": "left-orient", "label": "Left Orientation", "indices": [11, 12, 13, 14]},
    {"id": "left-grip", "label": "Left Gripper", "indices": [15]},
]


def _get_base_path() -> str:
    return os.environ.get("DATA_DIR", "./data")


def _dataset_config_path(dataset_id: str) -> Path:
    base = Path(_get_base_path())
    path = base.joinpath(*dataset_id.split("--"), "meta", "joint_config.json")
    return validate_path_containment(path, base)


def _global_defaults_path() -> Path:
    base = Path(_get_base_path())
    return validate_path_containment(base / "joint_config_defaults.json", base)


def _hardcoded_defaults() -> JointConfig:
    return JointConfig(
        dataset_id="_defaults",
        labels=dict(_DEFAULT_LABELS),
        groups=[JointGroupConfig(**g) for g in _DEFAULT_GROUPS],
    )


def _get_blob_provider() -> BlobDatasetProvider | None:
    if get_app_config().storage_backend != "azure":
        return None
    provider = get_dataset_service()._blob_provider
    if provider is None:
        logger.error("Azure joint configuration provider unavailable; refusing local fallback")
        raise HTTPException(status_code=503, detail="Azure joint configuration storage unavailable")
    return provider


def _blob_name(path: Path) -> str:
    return path.relative_to(Path(_get_base_path()).resolve()).as_posix()


async def _read_config(path: Path, dataset_id: str) -> VersionedValue[JointConfig]:
    logger.debug("Reading joint configuration for %s", dataset_id.replace("\r", "").replace("\n", ""))
    provider = _get_blob_provider()
    try:
        if provider is None:
            content = await asyncio.to_thread(path.read_bytes)
            etag = content_etag(content)
        else:
            client = await provider._get_client()
            blob = client.get_container_client(provider.container_name).get_blob_client(_blob_name(path))
            download = await blob.download_blob()
            content = await download.readall()
            etag = download.properties.etag
            if not etag:
                raise ValueError("Missing Azure joint configuration revision")
    except Exception as error:
        if (provider is None and isinstance(error, FileNotFoundError)) or (
            provider is not None
            and ResourceNotFoundError is not None
            and isinstance(error, ResourceNotFoundError)
            and getattr(error, "error_code", None) == "BlobNotFound"
        ):
            logger.debug("Joint configuration absent for %s", dataset_id.replace("\r", "").replace("\n", ""))
            return VersionedValue(value=None, etag=None)
        logger.error(
            "Failed to read joint configuration for %s (%s)",
            dataset_id.replace("\r", "").replace("\n", ""),
            type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="Failed to read joint configuration") from error
    try:
        config = JointConfig.model_validate_json(content)
        if config.dataset_id != dataset_id:
            raise ValueError("Joint configuration scope mismatch")
    except (ValidationError, ValueError) as error:
        logger.warning(
            "Invalid joint configuration for %s; refusing defaults (%s)",
            dataset_id.replace("\r", "").replace("\n", ""),
            type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="Invalid joint configuration") from error
    logger.debug(
        "Loaded joint configuration for %s with matching revision", dataset_id.replace("\r", "").replace("\n", "")
    )
    return VersionedValue(value=config, etag=str(etag))


async def _load_global_defaults() -> VersionedValue[JointConfig]:
    stored = await _read_config(_global_defaults_path(), "_defaults")
    return stored if stored.value is not None else VersionedValue(value=_hardcoded_defaults(), etag=None)


async def _state_name_config(dataset_id: str, dataset_service: DatasetService) -> JointConfig | None:
    dataset = await dataset_service.get_dataset(dataset_id)
    state = dataset.features.get("observation.state") if dataset is not None else None
    if state is None or not state.names:
        return None
    return JointConfig(
        dataset_id=dataset_id,
        labels={str(index): name for index, name in enumerate(state.names)},
        groups=[JointGroupConfig(id="state", label="State", indices=list(range(len(state.names))))],
    )


async def _load_dataset_config(dataset_id: str, dataset_service: DatasetService) -> VersionedValue[JointConfig]:
    stored = await _read_config(_dataset_config_path(dataset_id), dataset_id)
    if stored.value is not None:
        return stored
    derived = await _state_name_config(dataset_id, dataset_service)
    if derived is not None:
        logger.debug(
            "Derived joint configuration for %s from state names", dataset_id.replace("\r", "").replace("\n", "")
        )
        return VersionedValue(value=derived, etag=None)
    defaults = (await _load_global_defaults()).value
    return VersionedValue(
        value=JointConfig(dataset_id=dataset_id, labels=defaults.labels, groups=defaults.groups),
        etag=None,
    )


async def _save_config(path: Path, config: JointConfig, precondition: RevisionPrecondition) -> str:
    provider = _get_blob_provider()
    try:
        if provider is None:
            etag = await asyncio.to_thread(
                write_conditional,
                path,
                config.model_dump_json(),
                if_match=precondition.if_match,
                if_none_match=precondition.if_none_match,
            )
        else:
            client = await provider._get_client()
            blob = client.get_container_client(provider.container_name).get_blob_client(_blob_name(path))
            conditions = {"overwrite": False, "if_none_match": "*"}
            if precondition.if_match is not None:
                conditions = {
                    "overwrite": True,
                    "etag": precondition.if_match,
                    "match_condition": MatchConditions.IfNotModified,
                }
            try:
                result = await blob.upload_blob(
                    config.model_dump_json().encode("utf-8"),
                    content_settings=ContentSettings(content_type="application/json"),
                    **conditions,
                )
            except HttpResponseError as error:
                if error.status_code == 412 or (
                    precondition.if_none_match
                    and error.status_code == 409
                    and getattr(error, "error_code", None) == "BlobAlreadyExists"
                ):
                    raise RevisionConflictError(None) from error
                raise
            etag = result.get("etag")
            if not etag:
                raise ValueError("Missing saved Azure joint configuration revision")
    except RevisionConflictError:
        logger.warning(
            "Joint configuration save conflict for %s", config.dataset_id.replace("\r", "").replace("\n", "")
        )
        raise
    except Exception as error:
        logger.error(
            "Failed to save joint configuration for %s (%s)",
            config.dataset_id.replace("\r", "").replace("\n", ""),
            type(error).__name__,
        )
        raise HTTPException(status_code=500, detail="Failed to save joint configuration") from error
    logger.info("Saved joint configuration for %s", config.dataset_id.replace("\r", "").replace("\n", ""))
    return str(etag)


async def _update_config(
    path: Path, dataset_id: str, body: JointConfigUpdate, precondition: RevisionPrecondition, response: Response
) -> JointConfig:
    stored = await _read_config(path, dataset_id)
    if (precondition.if_none_match and stored.etag is not None) or (
        precondition.if_match is not None and precondition.if_match != stored.etag
    ):
        logger.warning("Joint configuration save conflict for %s", dataset_id.replace("\r", "").replace("\n", ""))
        raise RevisionConflictError(stored.etag)
    current = stored.value or JointConfig(dataset_id=dataset_id)
    config = JointConfig.model_validate(current.model_dump() | body.model_dump(exclude_unset=True))
    response.headers["ETag"] = await _save_config(path, config, precondition)
    return config


@router.get("/{dataset_id}/joint-config")
async def get_joint_config(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> JointConfig:
    """Read joint configuration or unpersisted defaults with its saved revision."""
    stored = await _load_dataset_config(dataset_id, dataset_service)
    if stored.etag:
        response.headers["ETag"] = stored.etag
    return stored.value


@router.put(
    "/{dataset_id}/joint-config",
    dependencies=[Depends(require_csrf_token)],
)
async def update_joint_config(
    response: Response,
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    body: JointConfigUpdate = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
) -> JointConfig:
    """Update joint configuration for a dataset."""
    return await _update_config(_dataset_config_path(dataset_id), dataset_id, body, precondition, response)


@defaults_router.get("/joint-config/defaults")
async def get_joint_config_defaults(response: Response) -> JointConfig:
    """Get global joint configuration defaults."""
    stored = await _load_global_defaults()
    if stored.etag:
        response.headers["ETag"] = stored.etag
    return stored.value


@defaults_router.put(
    "/joint-config/defaults",
    dependencies=[Depends(require_csrf_token)],
)
async def update_joint_config_defaults(
    response: Response,
    body: JointConfigUpdate = ...,
    precondition: RevisionPrecondition = Depends(require_revision_precondition),
) -> JointConfig:
    """Update global joint configuration defaults."""
    return await _update_config(_global_defaults_path(), "_defaults", body, precondition, response)
