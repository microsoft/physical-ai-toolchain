"""
Unit tests for local filesystem storage adapter.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from multiprocessing.connection import Connection
from multiprocessing.synchronize import Barrier
from pathlib import Path
from unittest.mock import patch

import pytest

from src.api.models.annotations import TaskCompletenessRating
from src.api.routers.labels import DatasetLabelsFile, LocalLabelStorage
from src.api.storage.local import LocalStorageAdapter, RevisionConflictError, StorageError
from src.api.validation import validate_path_containment

from .conftest import create_test_annotation


def _competing_local_writer(base: str, kind: str, barrier: Barrier, result: Connection, name: str) -> None:
    async def write() -> list[bool]:
        outcomes = []
        for index in range(8):
            dataset = f"race-{index}"
            if kind == "labels":
                storage = LocalLabelStorage(base)
                current = await storage.load_versioned(dataset)
                value = DatasetLabelsFile(dataset_id=dataset, episodes={"0": [name]})
            else:
                storage = LocalStorageAdapter(base)
                current = await storage.get_annotation_versioned(dataset, 0)
                value = create_test_annotation(episode_index=0)
                value.annotations[0].notes = name
            await asyncio.to_thread(barrier.wait, 10)
            try:
                if kind == "labels":
                    await storage.save(dataset, value, if_match=current.etag, if_none_match=current.etag is None)
                else:
                    await storage.save_annotation(
                        dataset, 0, value, if_match=current.etag, if_none_match=current.etag is None
                    )
                outcomes.append(True)
            except RevisionConflictError:
                outcomes.append(False)
        return outcomes

    try:
        result.send(asyncio.run(write()))
    finally:
        result.close()


@pytest.mark.parametrize("kind", ["labels", "annotations"])
@pytest.mark.parametrize("existing", [False, True])
def test_given_two_processes_when_writing_same_revision_then_only_one_wins(
    tmp_path: Path, kind: str, existing: bool
) -> None:
    async def seed() -> None:
        for index in range(8):
            dataset = f"race-{index}"
            if kind == "labels":
                await LocalLabelStorage(str(tmp_path)).save(dataset, DatasetLabelsFile(dataset_id=dataset))
            else:
                await LocalStorageAdapter(str(tmp_path)).save_annotation(
                    dataset, 0, create_test_annotation(episode_index=0)
                )

    if existing:
        asyncio.run(seed())
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    channels = [context.Pipe(duplex=False) for _ in range(2)]
    processes = [
        context.Process(target=_competing_local_writer, args=(str(tmp_path), kind, barrier, channel[1], str(index)))
        for index, channel in enumerate(channels)
    ]

    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
        outcomes = [channel[0].recv() for channel in channels]
        assert all(sum(winners) == 1 for winners in zip(*outcomes, strict=True))
        assert not list(tmp_path.rglob("*.tmp"))
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join()
        for channel in channels:
            for connection in channel:
                connection.close()


class TestLocalStorageAdapter:
    """Tests for LocalStorageAdapter."""

    @pytest.fixture(autouse=True)
    def _set_adapter(
        self,
        dataset_id: str,
        local_storage_adapter: LocalStorageAdapter,
        tmp_path: Path,
    ) -> None:
        self.temp_dir = tmp_path
        self.adapter = local_storage_adapter
        self.dataset_id = dataset_id

    async def test_get_annotation_not_found(self):
        """Test getting a non-existent annotation returns None."""
        result = await self.adapter.get_annotation(self.dataset_id, 0)
        assert result is None

    async def test_save_and_get_annotation(self):
        """Test saving and retrieving an annotation."""
        annotation = create_test_annotation(episode_index=5)

        # Save annotation
        await self.adapter.save_annotation(self.dataset_id, 5, annotation)

        # Verify file exists
        expected_path = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes" / "episode_000005.json"
        assert expected_path.exists()

        # Retrieve annotation
        result = await self.adapter.get_annotation(self.dataset_id, 5)
        assert result is not None
        assert result.episode_index == 5
        assert result.annotations[0].task_completeness.rating == TaskCompletenessRating.SUCCESS

    async def test_save_overwrites_existing(self):
        """Test that saving an annotation overwrites existing one."""
        # Save initial annotation
        annotation1 = create_test_annotation(episode_index=1)
        await self.adapter.save_annotation(self.dataset_id, 1, annotation1)

        # Save updated annotation
        annotation2 = create_test_annotation(episode_index=1)
        annotation2.annotations[0].notes = "Updated notes"
        await self.adapter.save_annotation(self.dataset_id, 1, annotation2)

        # Retrieve and verify updated
        result = await self.adapter.get_annotation(self.dataset_id, 1)
        assert result.annotations[0].notes == "Updated notes"

    async def test_conditional_save_rejects_stale_revision_without_modifying_data(self):
        annotation = create_test_annotation(episode_index=1)
        initial_etag = await self.adapter.save_annotation(self.dataset_id, 1, annotation, if_none_match=True)
        updated = create_test_annotation(episode_index=1)
        updated.annotations[0].notes = "Current notes"
        current_etag = await self.adapter.save_annotation(self.dataset_id, 1, updated, if_match=initial_etag)
        stale = create_test_annotation(episode_index=1)
        stale.annotations[0].notes = "Stale notes"

        with pytest.raises(RevisionConflictError) as exc_info:
            await self.adapter.save_annotation(self.dataset_id, 1, stale, if_match=initial_etag)

        assert exc_info.value.current_etag == current_etag
        versioned = await self.adapter.get_annotation_versioned(self.dataset_id, 1)
        assert versioned.etag == current_etag
        assert versioned.value is not None
        assert versioned.value.annotations[0].notes == "Current notes"

    async def test_create_only_save_rejects_existing_resource(self):
        annotation = create_test_annotation(episode_index=2)
        current_etag = await self.adapter.save_annotation(self.dataset_id, 2, annotation, if_none_match=True)

        with pytest.raises(RevisionConflictError) as exc_info:
            await self.adapter.save_annotation(self.dataset_id, 2, annotation, if_none_match=True)

        assert exc_info.value.current_etag == current_etag

    async def test_versioned_read_rejects_invalid_json(self):
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        annotations_dir.mkdir(parents=True)
        (annotations_dir / "episode_000003.json").write_text("{invalid json")

        with pytest.raises(StorageError, match="Invalid JSON"):
            await self.adapter.get_annotation_versioned(self.dataset_id, 3)

    async def test_versioned_read_wraps_unexpected_failure(self):
        with (
            patch.object(self.adapter, "_read_content", side_effect=RuntimeError("read failed")),
            pytest.raises(StorageError, match="Failed to read annotation file"),
        ):
            await self.adapter.get_annotation_versioned(self.dataset_id, 3)

    async def test_conditional_delete_rejects_missing_resource(self):
        with pytest.raises(RevisionConflictError) as exc_info:
            await self.adapter.delete_annotation(self.dataset_id, 3, if_match='"revision"')

        assert exc_info.value.current_etag is None

    async def test_conditional_delete_rejects_stale_revision(self):
        annotation = create_test_annotation(episode_index=3)
        current_etag = await self.adapter.save_annotation(self.dataset_id, 3, annotation)

        with pytest.raises(RevisionConflictError) as exc_info:
            await self.adapter.delete_annotation(self.dataset_id, 3, if_match='"stale"')

        assert exc_info.value.current_etag == current_etag
        assert await self.adapter.get_annotation(self.dataset_id, 3) is not None

    async def test_conditional_delete_accepts_current_revision(self):
        annotation = create_test_annotation(episode_index=3)
        current_etag = await self.adapter.save_annotation(self.dataset_id, 3, annotation)

        deleted = await self.adapter.delete_annotation(self.dataset_id, 3, if_match=current_etag)

        assert deleted is True
        assert await self.adapter.get_annotation(self.dataset_id, 3) is None

    async def test_list_annotated_episodes_empty(self):
        """Test listing episodes when no annotations exist."""
        result = await self.adapter.list_annotated_episodes(self.dataset_id)
        assert result == []

    async def test_list_annotated_episodes(self):
        """Test listing episodes with annotations."""
        # Create several annotations
        for idx in [3, 1, 5, 2]:
            annotation = create_test_annotation(episode_index=idx)
            await self.adapter.save_annotation(self.dataset_id, idx, annotation)

        # List should return sorted indices
        result = await self.adapter.list_annotated_episodes(self.dataset_id)
        assert result == [1, 2, 3, 5]

    async def test_delete_annotation(self):
        """Test deleting an annotation."""
        # Save annotation
        annotation = create_test_annotation(episode_index=10)
        await self.adapter.save_annotation(self.dataset_id, 10, annotation)

        # Verify exists
        assert await self.adapter.get_annotation(self.dataset_id, 10) is not None

        # Delete
        result = await self.adapter.delete_annotation(self.dataset_id, 10)
        assert result is True

        # Verify deleted
        assert await self.adapter.get_annotation(self.dataset_id, 10) is None

    async def test_delete_annotation_not_found(self):
        """Test deleting a non-existent annotation returns False."""
        result = await self.adapter.delete_annotation(self.dataset_id, 999)
        assert result is False

    async def test_invalid_json_raises_error(self):
        """Test that invalid JSON raises StorageError."""
        # Create invalid JSON file
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        annotations_dir.mkdir(parents=True)
        invalid_file = annotations_dir / "episode_000001.json"
        invalid_file.write_text("{invalid json")

        with pytest.raises(StorageError):
            await self.adapter.get_annotation(self.dataset_id, 1)

    async def test_atomic_write(self):
        """Test that writes are atomic (no partial files)."""
        annotation = create_test_annotation(episode_index=1)
        await self.adapter.save_annotation(self.dataset_id, 1, annotation)

        # Verify no temp files left behind
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        temp_files = list(annotations_dir.glob("*.tmp"))
        assert len(temp_files) == 0

    async def test_multiple_datasets(self):
        """Test that different datasets are isolated."""
        # Save to two datasets
        annotation1 = create_test_annotation(episode_index=1)
        annotation2 = create_test_annotation(episode_index=1)

        await self.adapter.save_annotation("dataset-a", 1, annotation1)
        await self.adapter.save_annotation("dataset-b", 1, annotation2)

        # Verify isolation
        result_a = await self.adapter.list_annotated_episodes("dataset-a")
        result_b = await self.adapter.list_annotated_episodes("dataset-b")

        assert result_a == [1]
        assert result_b == [1]

        # Delete from one doesn't affect other
        await self.adapter.delete_annotation("dataset-a", 1)
        assert await self.adapter.get_annotation("dataset-a", 1) is None
        assert await self.adapter.get_annotation("dataset-b", 1) is not None

    async def test_save_uses_async_tempfile(self):
        """Verify save_annotation delegates sync I/O to asyncio.to_thread."""
        annotation = create_test_annotation(episode_index=0)
        with patch("src.api.storage.local.asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            await self.adapter.save_annotation(self.dataset_id, 0, annotation)
            assert mock_to_thread.call_count >= 1

    async def test_get_versioned_delegates_path_resolution(self):
        """Versioned reads delegate filesystem path resolution to a worker thread."""
        with patch("src.api.storage.local.asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
            await self.adapter.get_annotation_versioned(self.dataset_id, 0)

        assert any(call.args[0] == self.adapter._get_annotation_path for call in mock_to_thread.call_args_list)
        assert any(call.args[0] == validate_path_containment for call in mock_to_thread.call_args_list)

    async def test_path_traversal_rejected(self):
        """Verify dataset_id with path traversal components raises StorageError."""
        annotation = create_test_annotation(episode_index=0)
        with pytest.raises(StorageError, match="path traversal detected"):
            await self.adapter.save_annotation("../../etc", 0, annotation)

    async def test_ensure_directory_oserror_wrapped(self):
        """makedirs OSError is wrapped as StorageError during save."""
        annotation = create_test_annotation(episode_index=0)

        async def _raise(*_args, **_kwargs):
            raise OSError("disk full")

        with (
            patch("src.api.storage.local.aiofiles.os.makedirs", side_effect=_raise),
            pytest.raises(StorageError, match="Failed to create directory"),
        ):
            await self.adapter.save_annotation(self.dataset_id, 0, annotation)

    async def test_get_annotation_invalid_json_explicit(self):
        """Malformed JSON triggers the JSONDecodeError branch."""
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        annotations_dir.mkdir(parents=True)
        (annotations_dir / "episode_000002.json").write_text("not json at all {{{")

        with pytest.raises(StorageError, match="Invalid JSON"):
            await self.adapter.get_annotation(self.dataset_id, 2)

    async def test_get_annotation_read_failure(self):
        """Unexpected read errors are wrapped as StorageError."""
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        annotations_dir.mkdir(parents=True)
        (annotations_dir / "episode_000003.json").write_text("{}")

        with (
            patch("src.api.storage.local.aiofiles.open", side_effect=RuntimeError("boom")),
            pytest.raises(StorageError, match="Failed to read annotation file"),
        ):
            await self.adapter.get_annotation(self.dataset_id, 3)

    async def test_save_cleans_temp_file_on_replace_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When os.replace fails, the temp file is cleaned and StorageError raised."""
        annotation = create_test_annotation(episode_index=4)

        def fail_replace(source: Path, target: Path) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr("evaluation.vlm_judge.curation_storage.os.replace", fail_replace)
        with pytest.raises(StorageError, match="Failed to save annotation file"):
            await self.adapter.save_annotation(self.dataset_id, 4, annotation)

        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        leftover = list(annotations_dir.glob("*.tmp"))
        assert leftover == []

    async def test_list_skips_malformed_filename(self):
        """Files matching the prefix/suffix but with non-numeric index are skipped."""
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        annotations_dir.mkdir(parents=True)
        (annotations_dir / "episode_abcdef.json").write_text("{}")
        (annotations_dir / "episode_000007.json").write_text("{}")

        result = await self.adapter.list_annotated_episodes(self.dataset_id)
        assert result == [7]

    async def test_list_listdir_failure_wrapped(self):
        """listdir failures are wrapped as StorageError."""
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        annotations_dir.mkdir(parents=True)

        original_to_thread = asyncio.to_thread

        async def fake_to_thread(func, *args, **kwargs):
            if func is os.listdir:
                raise OSError("listdir failed")
            return await original_to_thread(func, *args, **kwargs)

        with (
            patch("src.api.storage.local.asyncio.to_thread", side_effect=fake_to_thread),
            pytest.raises(StorageError, match="Failed to list annotations"),
        ):
            await self.adapter.list_annotated_episodes(self.dataset_id)

    async def test_delete_failure_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Filesystem deletion failures preserve the file and become StorageError."""
        annotation = create_test_annotation(episode_index=8)
        await self.adapter.save_annotation(self.dataset_id, 8, annotation)

        def fail_unlink(path: Path, *, missing_ok: bool = False) -> None:
            raise OSError("remove failed")

        monkeypatch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(StorageError, match="Failed to delete annotation file"):
            await self.adapter.delete_annotation(self.dataset_id, 8)
        assert await self.adapter.get_annotation(self.dataset_id, 8) == annotation

    async def test_save_cleanup_skipped_when_temp_already_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An already removed temporary file does not mask the publication error."""
        annotation = create_test_annotation(episode_index=9)

        def fail_replace(source: Path, target: Path) -> None:
            source.unlink()
            raise OSError("replace failed")

        monkeypatch.setattr("evaluation.vlm_judge.curation_storage.os.replace", fail_replace)
        with pytest.raises(StorageError, match="replace failed"):
            await self.adapter.save_annotation(self.dataset_id, 9, annotation)

        assert not list(self.temp_dir.rglob("*.tmp"))

    async def test_list_annotated_episodes_empty_directory(self):
        """An existing but empty annotations directory returns []."""
        annotations_dir = Path(self.temp_dir) / self.dataset_id / "annotations" / "episodes"
        annotations_dir.mkdir(parents=True)

        result = await self.adapter.list_annotated_episodes(self.dataset_id)
        assert result == []
