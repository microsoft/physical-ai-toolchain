"""Input validation dependencies for the dataviewer API."""

import os
import re
from pathlib import Path

from fastapi import HTTPException
from fastapi import Path as PathParam

SAFE_DATASET_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,254}$"
SAFE_CAMERA_NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$"

_DATASET_ID_RE = re.compile(SAFE_DATASET_ID_PATTERN)
_CAMERA_NAME_RE = re.compile(SAFE_CAMERA_NAME_PATTERN)


def validated_dataset_id(
    dataset_id: str = PathParam(..., pattern=SAFE_DATASET_ID_PATTERN),
) -> str:
    """FastAPI dependency that validates and sanitizes dataset_id path parameters."""
    dataset_id = dataset_id.replace("\r", "").replace("\n", "")
    if "\x00" in dataset_id or dataset_id in (".", "..") or "/" in dataset_id or "\\" in dataset_id:
        raise HTTPException(status_code=400, detail=f"Invalid dataset_id: '{dataset_id}'")
    if not _DATASET_ID_RE.match(dataset_id):
        raise HTTPException(status_code=400, detail=f"Invalid dataset_id: '{dataset_id}'")
    return dataset_id


def validated_camera_name(
    camera: str = PathParam(..., pattern=SAFE_CAMERA_NAME_PATTERN),
) -> str:
    """FastAPI dependency that validates and sanitizes camera name path parameters."""
    camera = camera.replace("\r", "").replace("\n", "")
    if "\x00" in camera or camera in (".", "..") or "/" in camera or "\\" in camera:
        raise HTTPException(status_code=400, detail=f"Invalid camera name: '{camera}'")
    if not _CAMERA_NAME_RE.match(camera):
        raise HTTPException(status_code=400, detail=f"Invalid camera name: '{camera}'")
    return camera


def validate_path_containment(path: Path, base_path: Path) -> Path:
    """Verify a path resolves within the expected base directory.

    Returns the normalized, validated path directly so that static analysis
    tools recognize the normpath+startswith sanitizer pattern on the same
    data-flow value.
    """
    safe_base = os.path.realpath(str(base_path))
    normalized = os.path.normpath(os.path.realpath(str(path)))
    if not normalized.startswith(safe_base + os.sep) and normalized != safe_base:
        raise HTTPException(
            status_code=400,
            detail="Path traversal detected: resolved path escapes base directory",
        )
    return Path(normalized)
