"""Read-only local and Azure source workspaces."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Protocol

from ..models.reviews import SourceFileIdentity, SourceIdentity
from .blob_dataset import BlobDatasetProvider


class SourceWorkspace(Protocol):
    """Provide a complete read-only dataset root for one operation."""

    def open(self, dataset_id: str) -> AbstractAsyncContextManager[Path]: ...


class LocalSourceWorkspace:
    """Resolve datasets beneath a configured local source root."""

    def __init__(self, source_root: Path) -> None:
        self._source_root = source_root.resolve()

    @asynccontextmanager
    async def open(self, dataset_id: str) -> AsyncIterator[Path]:
        dataset_root = (self._source_root / dataset_id).resolve()
        if not dataset_root.is_relative_to(self._source_root) or dataset_root == self._source_root:
            raise ValueError("Dataset source path escapes the configured root")
        if not dataset_root.is_dir():
            raise FileNotFoundError(dataset_root)
        yield dataset_root


class AzureSourceWorkspace:
    """Materialize complete Azure datasets into operation-owned directories."""

    def __init__(self, provider: BlobDatasetProvider, *, temporary_root: Path | None = None) -> None:
        self._provider = provider
        self._temporary_root = temporary_root

    @asynccontextmanager
    async def open(self, dataset_id: str) -> AsyncIterator[Path]:
        if self._temporary_root is not None:
            self._temporary_root.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="dataviewer-source-", dir=self._temporary_root))
        try:
            if not await self._provider.materialize_dataset_to_local(dataset_id, root):
                raise RuntimeError(f"Could not materialize source dataset: {dataset_id}")
            yield root
        finally:
            await asyncio.to_thread(shutil.rmtree, root, True)


async def resolve_source_identity(workspace: SourceWorkspace, source: SourceIdentity) -> SourceIdentity:
    """Recompute a recorded source identity from the current source bytes."""
    async with workspace.open(source.dataset_id) as root:
        return await asyncio.to_thread(_resolve_source_identity, root, source)


def _resolve_source_identity(root: Path, source: SourceIdentity) -> SourceIdentity:
    files: list[SourceFileIdentity] = []
    for expected in source.files:
        path = root / expected.relative_path
        try:
            payload = path.read_bytes()
        except OSError:
            return source.model_copy(update={"source_digest": "0" * 64})
        files.append(
            SourceFileIdentity(
                relative_path=expected.relative_path,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.relative_path.encode())
        digest.update(str(file.size_bytes).encode())
        digest.update(file.sha256.encode())
    return source.model_copy(update={"source_digest": digest.hexdigest(), "files": tuple(files)})
