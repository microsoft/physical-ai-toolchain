"""Disk-backed cache for VLM-judge results.

Idempotency follows the existing checkpoint-upload pattern in the repo: a
SHA256 of ``(video_paths, instruction, judge_model, prompt_version, agent_config)``
keys a JSON file on disk. Cache hits are returned verbatim and skip all VLM
inference.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger("evaluation.vlm_judge")


class JudgeCache:
    """Filesystem-backed JSON cache for ``JudgeResult.to_dict()`` payloads."""

    def __init__(self, root: Path | None) -> None:
        self._root = Path(root) if root is not None else None
        if self._root is not None:
            self._root.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._root is not None

    def key(
        self,
        *,
        video_paths: Mapping[str, Path | str],
        instruction: str,
        judge_model: str,
        prompt_version: str,
        agent_config: object | None = None,
    ) -> str:
        """Return a stable hex digest for the given judgement input."""
        payload = {
            "videos": _video_fingerprints(video_paths),
            "instruction": instruction,
            "judge_model": judge_model,
            "prompt_version": prompt_version,
            "agent_config": _serialise_config(agent_config),
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        if self._root is None:
            return None
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            _LOGGER.warning("Corrupt cache entry %s; ignoring", path)
            return None

    def put(self, key: str, payload: dict[str, Any]) -> None:
        if self._root is None:
            return
        path = self._path_for(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True))
        tmp.replace(path)

    def _path_for(self, key: str) -> Path:
        assert self._root is not None
        return self._root / f"{key}.json"


def _video_fingerprints(video_paths: Mapping[str, Path | str]) -> list[dict[str, object]]:
    """Cheap-but-stable fingerprint based on path, size, and mtime."""
    out: list[dict[str, object]] = []
    for view in sorted(video_paths):
        path = Path(video_paths[view])
        try:
            stat = path.stat()
            size = int(stat.st_size)
            mtime = int(stat.st_mtime_ns)
        except OSError:
            size = -1
            mtime = -1
        out.append({"view": view, "path": str(path), "size": size, "mtime_ns": mtime})
    return out


def _serialise_config(config: object | None) -> object:
    if config is None:
        return None
    if is_dataclass(config) and not isinstance(config, type):
        return asdict(config)
    if isinstance(config, Mapping):
        return dict(config)
    return repr(config)
