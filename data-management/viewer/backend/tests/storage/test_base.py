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


class _SuperAdapter(StorageAdapter):
    """Adapter exposing the default abstract method bodies through public calls."""

    async def get_annotation(self, dataset_id: str, episode_index: int) -> EpisodeAnnotationFile | None:
        return await super().get_annotation(dataset_id, episode_index)

    async def save_annotation(self, dataset_id: str, episode_index: int, annotation: EpisodeAnnotationFile) -> None:
        return await super().save_annotation(dataset_id, episode_index, annotation)

    async def list_annotated_episodes(self, dataset_id: str) -> list[int]:
        return await super().list_annotated_episodes(dataset_id)

    async def delete_annotation(self, dataset_id: str, episode_index: int) -> bool:
        return await super().delete_annotation(dataset_id, episode_index)


class TestStorageAdapterContract:
    def test_cannot_instantiate_abstract_directly(self) -> None:
        with pytest.raises(TypeError):
            StorageAdapter()  # type: ignore[abstract]


class TestStorageAdapterDefaults:
    @pytest.mark.asyncio
    async def test_close_default_is_noop(self) -> None:
        adapter = _FakeAdapter()
        result = await adapter.close()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_annotations_batch_default_uses_get_annotation(self) -> None:
        adapter = _FakeAdapter()
        ann = create_test_annotation(0)
        await adapter.save_annotation("ds", 0, ann)
        result = await adapter.get_annotations_batch("ds", [0, 1])
        assert result == {0: ann, 1: None}

    @pytest.mark.asyncio
    async def test_abstract_default_bodies_return_none_to_subclasses(self) -> None:
        adapter = _SuperAdapter()
        annotation = create_test_annotation(0)

        assert await adapter.get_annotation("ds", 0) is None
        assert await adapter.save_annotation("ds", 0, annotation) is None
        assert await adapter.list_annotated_episodes("ds") is None
        assert await adapter.delete_annotation("ds", 0) is None


class TestStorageError:
    def test_message_only(self) -> None:
        err = StorageError("boom")
        assert str(err) == "boom"
        assert err.cause is None

    def test_with_cause_chain(self) -> None:
        original = ValueError("disk full")
        err = StorageError("save failed", cause=original)
        assert err.cause is original
        assert str(err) == "save failed"
