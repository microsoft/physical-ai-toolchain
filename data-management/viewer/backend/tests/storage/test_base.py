"""Unit tests for the StorageAdapter abstract contract."""

from __future__ import annotations

import pytest

from src.api.models.annotations import EpisodeAnnotationFile
from src.api.storage.base import StorageAdapter, StorageError

from .conftest import create_test_annotation


class _FakeAdapter(StorageAdapter):
    """Minimal concrete adapter exercising only the abstract methods."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, int], EpisodeAnnotationFile] = {}

    async def get_annotation(self, dataset_id: str, episode_index: int) -> EpisodeAnnotationFile | None:
        return self._store.get((dataset_id, episode_index))

    async def save_annotation(self, dataset_id: str, episode_index: int, annotation: EpisodeAnnotationFile) -> None:
        self._store[(dataset_id, episode_index)] = annotation

    async def list_annotated_episodes(self, dataset_id: str) -> list[int]:
        return sorted(idx for ds, idx in self._store if ds == dataset_id)

    async def delete_annotation(self, dataset_id: str, episode_index: int) -> bool:
        return self._store.pop((dataset_id, episode_index), None) is not None


class TestStorageAdapterContract:
    def test_cannot_instantiate_abstract_directly(self):
        with pytest.raises(TypeError):
            StorageAdapter()  # type: ignore[abstract]

    @pytest.mark.asyncio
    async def test_close_default_is_noop(self):
        adapter = _FakeAdapter()
        assert await adapter.close() is None

    @pytest.mark.asyncio
    async def test_get_annotations_batch_default_uses_get_annotation(self):
        adapter = _FakeAdapter()
        ann = create_test_annotation(0)
        await adapter.save_annotation("ds", 0, ann)
        result = await adapter.get_annotations_batch("ds", [0, 1])
        assert result[0] is ann
        assert result[1] is None


class TestStorageError:
    def test_message_only(self):
        err = StorageError("boom")
        assert str(err) == "boom"
        assert err.cause is None

    def test_with_cause_chain(self):
        original = ValueError("disk full")
        err = StorageError("save failed", cause=original)
        assert err.cause is original
        assert "save failed" in str(err)
