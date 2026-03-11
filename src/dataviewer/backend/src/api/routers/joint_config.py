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
from pydantic import BaseModel, Field

from ..csrf import require_csrf_token
from ..validation import validate_path_containment, validated_dataset_id

logger = logging.getLogger(__name__)

router = APIRouter()
defaults_router = APIRouter()


class JointGroupConfig(BaseModel):
    """A named group of joint indices."""

    id: str
    label: str
    indices: list[int] = Field(default_factory=list)


class JointConfig(BaseModel):
    """Joint labels and groupings for a dataset."""

    dataset_id: str
    labels: dict[str, str] = Field(default_factory=dict)
    groups: list[JointGroupConfig] = Field(default_factory=list)


class JointConfigUpdate(BaseModel):
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
    return os.environ.get("HMI_DATA_PATH", "./data")


def _dataset_config_path(dataset_id: str) -> Path:
    base = Path(_get_base_path())
    path = base / dataset_id / "meta" / "joint_config.json"
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


async def _load_dataset_config(dataset_id: str) -> JointConfig:
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
        defaults = await _load_global_defaults()
        config = JointConfig(
            dataset_id=dataset_id,
            labels=defaults.labels,
            groups=defaults.groups,
        )
        await _save_dataset_config(dataset_id, config)
        return config
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
async def get_joint_config(dataset_id: str = Depends(validated_dataset_id)) -> JointConfig:
    """Get joint configuration for a dataset. Auto-creates from defaults if missing."""
    return await _load_dataset_config(dataset_id)


@router.put(
    "/{dataset_id}/joint-config",
    dependencies=[Depends(require_csrf_token)],
)
async def update_joint_config(
    dataset_id: str = Depends(validated_dataset_id),
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
