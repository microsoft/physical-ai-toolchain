"""Confine local media paths without opening or decoding media."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class MediaInputError(ValueError):
    """Invalid local video path or undecodable episode media."""


def local_media_identity(path: Path) -> str:
    """Fingerprint an already-confined local file without opening or decoding it."""
    metadata = path.stat()
    identity = [
        "local",
        str(path),
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    ]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def validate_media_root(root: Path | str | None) -> Path:
    """Require an explicit existing directory narrower than the filesystem root."""
    if root is None or not str(root).strip():
        raise ValueError("A media root is required")
    resolved = Path(root).resolve()
    if resolved.parent == resolved or not resolved.is_dir():
        raise ValueError("Media root must be an existing non-root directory")
    return resolved


def validate_video_path(path: Path | str, root: Path, *, root_alias: Path | str | None = None) -> Path:
    """Return a canonical regular MP4 inside root without opening the media.

    The caller owns the canonical root and optional configured alias. The alias
    only permits a lexical spelling; resolved paths must remain inside root.
    This is not protection against a trusted local writer replacing a file
    after validation.
    """
    try:
        submitted = Path(path)
        if ".." in submitted.parts or "\x00" in str(path) or "://" in str(path):
            raise MediaInputError("Invalid media input")
        candidate = str(submitted if submitted.is_absolute() else root / submitted)
        root_prefix = str(root) + os.sep
        allowed_prefixes = (root_prefix,)
        if root_alias is not None:
            allowed_prefixes += (str(Path(root_alias).absolute()) + os.sep,)
        if not candidate.startswith(allowed_prefixes):
            raise MediaInputError("Invalid media input")
        resolved = os.path.realpath(candidate)
        if not resolved.startswith(root_prefix):
            raise MediaInputError("Invalid media input")
        validated = Path(resolved)
        if validated.suffix.lower() != ".mp4" or not stat.S_ISREG(validated.stat().st_mode):
            raise MediaInputError("Invalid media input")
        return validated
    except (OSError, RuntimeError, ValueError) as err:
        raise MediaInputError("Invalid media input") from err
