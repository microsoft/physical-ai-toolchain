"""Interprocess conditional local curation persistence."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from physical_ai_curation.models import DatasetLabelsFile
from physical_ai_curation.storage import RevisionConflictError, VersionedValue, content_etag


@contextmanager
def _resource_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path.with_name(f"{path.name}.lock"), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "r+b") as lock:
        if os.name == "nt":
            import msvcrt

            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _check_revision(path: Path, if_match: str | None, if_none_match: bool) -> bool:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        content = None
    current_etag = content_etag(content) if content is not None else None
    if (if_none_match and content is not None) or (if_match is not None and if_match != current_etag):
        raise RevisionConflictError(current_etag)
    return content is not None


def write_conditional(path: Path, content: str, *, if_match: str | None = None, if_none_match: bool = False) -> str:
    with _resource_lock(path):
        _check_revision(path, if_match, if_none_match)
        encoded = content.encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return content_etag(encoded)


def delete_conditional(path: Path, *, if_match: str | None = None) -> bool:
    with _resource_lock(path):
        if not _check_revision(path, if_match, False):
            return False
        path.unlink()
        return True


class LocalCurationStorage:
    """Use explicitly configured roots and the same conditional lock as the viewer."""

    def __init__(self, roots: dict[str, Path]) -> None:
        self.roots = {identifier: root.resolve() for identifier, root in roots.items()}

    def _path(self, dataset_id: str) -> Path:
        root = self.roots[dataset_id]
        path = (root / "meta" / "episode_labels.json").resolve()
        path.relative_to(root)
        return path

    async def load_versioned(self, dataset_id: str) -> VersionedValue[DatasetLabelsFile]:
        try:
            content = await asyncio.to_thread(self._path(dataset_id).read_bytes)
        except FileNotFoundError:
            return VersionedValue(DatasetLabelsFile(dataset_id=dataset_id), None)
        value = DatasetLabelsFile.model_validate_json(content)
        if value.dataset_id != dataset_id:
            raise ValueError("Curation dataset identity mismatch")
        return VersionedValue(value, content_etag(content))

    async def save(
        self,
        dataset_id: str,
        labels_file: DatasetLabelsFile,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        return await asyncio.to_thread(
            write_conditional,
            self._path(dataset_id),
            json.dumps(labels_file.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False),
            if_match=if_match,
            if_none_match=if_none_match,
        )
