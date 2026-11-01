"""Filesystem adapter for saved curation resources and source freshness."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from physical_ai_curation.saved_input import SavedInputError, SavedResource


def local_source_revision(root: Path, metadata_paths: list[Path] | None = None) -> tuple[str, str]:
    """Fingerprint source metadata without including editable curation resources."""
    root = root.resolve()
    paths = (
        metadata_paths
        if metadata_paths is not None
        else sorted(
            path
            for path in (root / "meta").rglob("*")
            if path.is_file() and path.suffix in {".json", ".jsonl", ".parquet"} and path.name != "episode_labels.json"
        )
    )
    if not paths:
        raise SavedInputError("Source generation is unavailable")
    versions = []
    for path in paths:
        path = path.resolve()
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        versions.append([relative, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    return (
        hashlib.sha256(f"local:{root}".encode()).hexdigest(),
        hashlib.sha256(json.dumps(versions).encode()).hexdigest(),
    )


class LocalSavedInputReader:
    """Read the documented local curation layout beneath one configured dataset root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _read(self, relative: Path) -> SavedResource:
        path = (self.root / relative).resolve()
        path.relative_to(self.root)
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            return SavedResource(None, None)
        value = json.loads(content)
        if not isinstance(value, dict):
            raise SavedInputError("Invalid saved curation resource")
        return SavedResource(value, f'"{hashlib.sha256(content).hexdigest()}"')

    async def annotation(self, dataset_id: str, episode_index: int) -> SavedResource:
        return await asyncio.to_thread(self._read, Path("annotations/episodes") / f"episode_{episode_index:06d}.json")

    async def edits(
        self, dataset_id: str, episode_index: int, principal_scope_id: str, source: tuple[str, str]
    ) -> SavedResource:
        namespace = hashlib.sha256(
            json.dumps([source[0], principal_scope_id], separators=(",", ":")).encode()
        ).hexdigest()
        resource = await asyncio.to_thread(
            self._read, Path("annotations/edits") / namespace / f"episode_{episode_index:06d}.json"
        )
        descriptor = (resource.value or {}).get("saved_edits", {}).get(source[0], {}).get(principal_scope_id)
        if descriptor:
            if (
                descriptor.get("dataset_id"),
                descriptor.get("episode_index"),
                descriptor.get("source_id"),
                descriptor.get("author_id"),
            ) != (dataset_id, episode_index, source[0], principal_scope_id):
                raise SavedInputError("Saved edit scope mismatch")
            if descriptor.get("source_revision") != source[1]:
                raise SavedInputError("Saved edit source revision changed")
        return SavedResource(descriptor, resource.etag)

    async def source_revision(self, dataset_id: str, episode_index: int) -> tuple[str, str]:
        return await asyncio.to_thread(local_source_revision, self.root)
