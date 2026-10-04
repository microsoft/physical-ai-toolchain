"""Joint configuration API endpoints.

Provides read/write endpoints for per-dataset joint labels and groupings,
plus global defaults stored at the datasets root directory.
"""

import json
import logging
import os
from pathlib import Path

import aiofiles
import aiofiles.os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from ..csrf import require_csrf_token
from ..services.dataset_service import DatasetService, get_dataset_service
from ..validation import SAFE_DATASET_ID_PATTERN, SanitizedModel, path_string_param, validate_path_containment

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
    """Resolve the config inside the dataset's own folder; ``parent--child`` IDs map to nested folders."""
    base = Path(_get_base_path())
    parts = dataset_id.split("--") if "--" in dataset_id else [dataset_id]
    return validate_path_containment(base.joinpath(*parts, "meta", "joint_config.json"), base)


def _global_defaults_path() -> Path:
    base = Path(_get_base_path())
    return validate_path_containment(base / "joint_config_defaults.json", base)


def _hardcoded_defaults() -> JointConfig:
    return JointConfig(
        dataset_id="_defaults",
        labels=dict(_DEFAULT_LABELS),
        groups=[JointGroupConfig(**g) for g in _DEFAULT_GROUPS],
    )


async def _load_global_defaults() -> JointConfig:
    path = _global_defaults_path()
    if not await aiofiles.os.path.exists(path):
        return _hardcoded_defaults()
    async with aiofiles.open(path, encoding="utf-8") as f:
        data = json.loads(await f.read())
        return JointConfig.model_validate(data)


async def _save_global_defaults(config: JointConfig) -> None:
    path = _global_defaults_path()
    content = json.dumps(config.model_dump(), indent=2)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)


async def _state_name_config(dataset_id: str, dataset_service: DatasetService) -> JointConfig | None:
    """Label each state channel with the dataset's own feature name, when the dataset declares them."""
    dataset = await dataset_service.get_dataset(dataset_id)
    state = dataset.features.get("observation.state") if dataset is not None else None
    if state is None or not state.names:
        return None
    return JointConfig(
        dataset_id=dataset_id,
        labels={str(index): name for index, name in enumerate(state.names)},
        groups=[JointGroupConfig(id="state", label="State", indices=list(range(len(state.names))))],
    )


async def _load_dataset_config(dataset_id: str, dataset_service: DatasetService) -> JointConfig:
    """Return the saved config, else defaults derived from the dataset, without writing anything."""
    path = _dataset_config_path(dataset_id)
    safe_base = os.path.realpath(_get_base_path())
    resolved = os.path.realpath(str(path))
    if not resolved.startswith(safe_base + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected",
        )
    path = Path(resolved)
    if not await aiofiles.os.path.exists(path):
        derived = await _state_name_config(dataset_id, dataset_service)
        if derived is not None:
            return derived
        defaults = await _load_global_defaults()
        return JointConfig(
            dataset_id=dataset_id,
            labels=defaults.labels,
            groups=defaults.groups,
        )
    async with aiofiles.open(path, encoding="utf-8") as f:
        data = json.loads(await f.read())
        return JointConfig.model_validate(data)


async def _save_dataset_config(dataset_id: str, config: JointConfig) -> None:
    path = _dataset_config_path(dataset_id)
    safe_base = os.path.realpath(_get_base_path())
    resolved = os.path.realpath(str(path))
    if not resolved.startswith(safe_base + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected",
        )
    path = Path(resolved)
    await aiofiles.os.makedirs(path.parent, exist_ok=True)
    content = json.dumps(config.model_dump(), indent=2)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)


@router.get("/{dataset_id}/joint-config")
async def get_joint_config(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    dataset_service: DatasetService = Depends(get_dataset_service),
) -> JointConfig:
    """Get joint configuration for a dataset, deriving labels from its state names when none is saved."""
    return await _load_dataset_config(dataset_id, dataset_service)


@router.put(
    "/{dataset_id}/joint-config",
    dependencies=[Depends(require_csrf_token)],
)
async def update_joint_config(
    dataset_id: str = Depends(path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")),
    body: JointConfigUpdate = ...,
) -> JointConfig:
    """Update joint configuration for a dataset."""
    config = JointConfig(
        dataset_id=dataset_id,
        labels=body.labels,
        groups=body.groups,
    )
    await _save_dataset_config(dataset_id, config)
    return config


@defaults_router.get("/joint-config/defaults")
async def get_joint_config_defaults() -> JointConfig:
    """Get global joint configuration defaults."""
    return await _load_global_defaults()


@defaults_router.put(
    "/joint-config/defaults",
    dependencies=[Depends(require_csrf_token)],
)
async def update_joint_config_defaults(body: JointConfigUpdate = ...) -> JointConfig:
    """Update global joint configuration defaults."""
    config = JointConfig(
        dataset_id="_defaults",
        labels=body.labels,
        groups=body.groups,
    )
    await _save_global_defaults(config)
    return config
