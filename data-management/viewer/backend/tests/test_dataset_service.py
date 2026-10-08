"""Public behavior tests for DatasetService with synthetic datasets."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from urllib.parse import parse_qs, urlsplit

import pytest

from src.api.services.dataset_service import DatasetService

_DATASET_ID = "synthetic"
_CAMERA = "observation.images.front"


def _write_dataset(base_path: Path, dataset_id: str = _DATASET_ID) -> Path:
    dataset = base_path.joinpath(*dataset_id.split("--"))
    (dataset / "meta").mkdir(parents=True)
    (dataset / "data" / "chunk-000").mkdir(parents=True)
    (dataset / "videos" / "chunk-000" / _CAMERA).mkdir(parents=True)
    info = {
        "codebase_version": "v2.1",
        "robot_type": "synthetic-arm",
        "total_episodes": 3,
        "total_frames": 9,
        "total_tasks": 2,
        "total_chunks": 1,
        "chunks_size": 1000,
        "fps": 20.0,
        "splits": {"train": "0:3"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": {
            "observation.state": {"dtype": "float32", "shape": [3], "names": ["a", "b", "c"]},
            "action": {"dtype": "float32", "shape": [3]},
            _CAMERA: {"dtype": "video", "shape": [48, 64, 3]},
        },
    }
    (dataset / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    with (dataset / "meta" / "episodes.jsonl").open("w", encoding="utf-8") as episodes_file:
        for episode_index, task_index in ((0, 0), (1, 1), (2, 0)):
            episodes_file.write(
                json.dumps(
                    {
                        "episode_index": episode_index,
                        "length": 3,
                        "task_index": task_index,
                    }
                )
                + "\n"
            )
    with (dataset / "meta" / "tasks.jsonl").open("w", encoding="utf-8") as tasks_file:
        tasks_file.write(json.dumps({"task_index": 0, "task": "pick"}) + "\n")
        tasks_file.write(json.dumps({"task_index": 1, "task": "place"}) + "\n")

    for episode_index, task_index in ((0, 0), (1, 1), (2, 0)):
        table = pa.table(
            {
                "frame_index": [0, 1, 2],
                "timestamp": [0.0, 0.05, 0.1],
                "episode_index": [episode_index] * 3,
                "task_index": [task_index] * 3,
                "observation.state": [
                    [float(episode_index), 1.0, 2.0],
                    [float(episode_index + 1), 2.0, 3.0],
                    [float(episode_index + 2), 3.0, 4.0],
                ],
                "action": [[0.4, 0.5, 0.6]] * 3,
            }
        )
        pq.write_table(table, dataset / "data" / "chunk-000" / f"episode_{episode_index:06d}.parquet")
        video_path = dataset / "videos" / "chunk-000" / _CAMERA / f"episode_{episode_index:06d}.mp4"
        video_path.write_bytes(f"video-{episode_index}".encode())
        os.utime(video_path, (1, 1))
    return dataset


@pytest.fixture
def service(tmp_path: Path) -> DatasetService:
    _write_dataset(tmp_path)
    return DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)


class TestDatasetDiscovery:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("fresh", [False, True])
    async def test_given_blob_generation_when_loading_edits_then_only_requested_episode_data_is_synced(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fresh: bool,
    ) -> None:
        import shutil
        from unittest.mock import AsyncMock, MagicMock

        remote = _write_dataset(tmp_path / "remote")
        provider = MagicMock()

        async def sync(dataset_id: str, path: Path, episode_index: int) -> bool:
            shutil.copytree(remote / "meta", path / "meta", dirs_exist_ok=True)
            relative = Path("data") / "chunk-000" / f"episode_{episode_index:06d}.parquet"
            (path / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(remote / relative, path / relative)
            return True

        provider.sync_episode_to_local = AsyncMock(side_effect=sync)
        datasets = DatasetService(base_path=str(tmp_path / "local"), blob_provider=provider)
        datasets._blob_dataset_ids.add(_DATASET_ID)
        revision = AsyncMock(return_value=("source", "generation-one"))
        monkeypatch.setattr(datasets, "get_source_revision", revision)
        try:
            first = await datasets.get_episode(_DATASET_ID, 0, fresh=fresh)
            second = await datasets.get_episode(_DATASET_ID, 1, fresh=fresh)
            assert first is not None and second is not None
            assert provider.sync_episode_to_local.await_count == 2
            provider.sync_dataset_to_local.assert_not_called()
            repeated = await datasets.get_episode(_DATASET_ID, 0, fresh=fresh)
            assert repeated is not None
            assert provider.sync_episode_to_local.await_count == 2
            manifest = remote / "meta" / "info.json"
            metadata = json.loads(manifest.read_text())
            metadata["features"]["observation.images.wrist"] = metadata["features"].pop(_CAMERA)
            manifest.write_text(json.dumps(metadata))
            revision.return_value = ("source", "generation-two")
            refreshed = await datasets.get_episode(_DATASET_ID, 0, fresh=fresh)
            assert refreshed is not None and refreshed.cameras == ["observation.images.wrist"]
            assert provider.sync_episode_to_local.await_count == 3
        finally:
            datasets.cleanup_temp_dirs()

    @pytest.mark.asyncio
    async def test_given_replaced_manifest_when_loading_fresh_then_cached_loader_is_not_reused(
        self,
        service: DatasetService,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def annotated_episodes(dataset_id: str) -> list[int]:
            return [0]

        monkeypatch.setattr(service._storage, "list_annotated_episodes", annotated_episodes)
        await service.list_datasets()
        initial = await service.get_episode(_DATASET_ID, 0, fresh=True)
        assert initial is not None and initial.cameras == [_CAMERA]
        assert initial.meta.has_annotations
        assert (initial.source_id, initial.source_revision) == await service.get_source_revision(_DATASET_ID, 0)
        manifest = tmp_path / _DATASET_ID / "meta" / "info.json"
        metadata = json.loads(manifest.read_text())
        new_camera = "observation.images.wrist"
        metadata["features"][new_camera] = metadata["features"].pop(_CAMERA)
        manifest.write_text(json.dumps(metadata))
        refreshed = await service.get_episode(_DATASET_ID, 0, fresh=True)
        assert refreshed is not None and refreshed.cameras == [new_camera]
        assert refreshed.source_id == initial.source_id
        assert refreshed.source_revision != initial.source_revision

    @pytest.mark.asyncio
    async def test_given_source_replacement_when_resolving_identity_then_revision_changes(
        self,
        service: DatasetService,
        tmp_path: Path,
    ) -> None:
        source_id, revision = await service.get_source_revision(_DATASET_ID, 0)
        restarted = DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)
        assert await restarted.get_source_revision(_DATASET_ID, 0) == (source_id, revision)
        curation = tmp_path / _DATASET_ID / "annotations"
        curation.mkdir()
        (curation / "unrelated.json").write_text("{}")
        assert await service.get_source_revision(_DATASET_ID, 0) == (source_id, revision)

        manifest = tmp_path / _DATASET_ID / "meta" / "info.json"
        replacement = manifest.with_suffix(".replacement")
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)

        changed_source, changed_revision = await service.get_source_revision(_DATASET_ID, 0)
        assert changed_source == source_id
        assert changed_revision != revision
        assert str(tmp_path) not in source_id

    @pytest.mark.asyncio
    async def test_given_different_local_roots_when_resolving_then_sources_are_distinct(
        self,
        service: DatasetService,
        tmp_path: Path,
    ) -> None:
        other_root = tmp_path / "other-source"
        _write_dataset(other_root)
        other = DatasetService(base_path=str(other_root), episode_cache_capacity=0)
        assert (await service.get_source_revision(_DATASET_ID, 0))[0] != (
            await other.get_source_revision(_DATASET_ID, 0)
        )[0]

    @pytest.mark.asyncio
    async def test_lists_synthetic_dataset(self, service):
        datasets = await service.list_datasets()

        assert len(datasets) == 1
        assert datasets[0].model_dump() == {
            "id": _DATASET_ID,
            "name": "synthetic (synthetic-arm)",
            "group": None,
            "total_episodes": 3,
            "fps": 20.0,
            "features": {
                "observation.state": {
                    "dtype": "float32",
                    "shape": [3],
                    "names": ["a", "b", "c"],
                },
                "action": {"dtype": "float32", "shape": [3], "names": None},
                _CAMERA: {"dtype": "video", "shape": [48, 64, 3], "names": None},
            },
            "tasks": [
                {"task_index": 0, "description": "pick"},
                {"task_index": 1, "description": "place"},
            ],
        }

    @pytest.mark.asyncio
    async def test_gets_dataset_without_prior_listing(self, service):
        dataset = await service.get_dataset(_DATASET_ID)

        assert dataset is not None
        assert dataset.id == _DATASET_ID
        assert dataset.total_episodes == 3

    @pytest.mark.asyncio
    async def test_missing_dataset_returns_none(self, service):
        assert await service.get_dataset("missing") is None

    @pytest.mark.asyncio
    async def test_rejects_traversal_through_public_lookup(self, service):
        assert await service.get_dataset("../escape") is None
        assert await service.get_dataset("a--b--c--d--e--f") is None

    @pytest.mark.asyncio
    async def test_reports_format_capabilities_after_discovery(self, service):
        await service.get_dataset(_DATASET_ID)

        assert service.dataset_is_lerobot(_DATASET_ID) is True
        assert service.dataset_has_hdf5(_DATASET_ID) is False
        assert service.has_lerobot_support() is True

    def test_dataset_contract_requires_matching_artifact_hashes(self, accepted_dataset_path: Path) -> None:
        dataset_id = accepted_dataset_path.name
        descriptor = json.loads((accepted_dataset_path / "accepted-dataset.json").read_text(encoding="utf-8"))
        contract_service = DatasetService(base_path=str(accepted_dataset_path.parent))
        contract = contract_service.get_dataset_contract(dataset_id)
        assert contract is not None
        assert contract.model_dump() == {
            "dataset_id": dataset_id,
            "output_adapter_id": "lerobot_v3",
            "output_adapter_version": "0.6.0",
            "viewer_adapter_id": "dataviewer_v1",
            "profile_id": "profile-alpha",
            "profile_sha256": "a" * 64,
            "capture_provenance_sha256": descriptor["artifacts"]["capture_provenance"]["sha256"],
            "export_validation_sha256": descriptor["artifacts"]["export_validation"]["sha256"],
            "capture_features": descriptor["capture_features"],
            "sensors": descriptor["sensors"],
        }

        (accepted_dataset_path / "capture-provenance.json").write_text("changed", encoding="utf-8")
        assert contract_service.get_dataset_contract(dataset_id) is None


class TestNestedDatasetDiscovery:
    @pytest.mark.asyncio
    async def test_discovers_nested_dataset_and_group(self, tmp_path):
        _write_dataset(tmp_path, "project--recordings--session")
        service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)

        datasets = await service.list_datasets()

        assert [dataset.id for dataset in datasets] == ["project--recordings--session"]
        assert datasets[0].group == "project--recordings"

    @pytest.mark.asyncio
    async def test_ignores_dataset_beyond_maximum_depth(self, tmp_path):
        _write_dataset(tmp_path, "a--b--c--d--e--f")
        service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)

        assert await service.list_datasets() == []


class TestEpisodeListing:
    @pytest.mark.asyncio
    async def test_lists_exact_episode_metadata(self, service):
        await service.get_dataset(_DATASET_ID)

        episodes = await service.list_episodes(_DATASET_ID)

        assert [episode.model_dump() for episode in episodes] == [
            {"index": 0, "length": 3, "task_index": 0, "has_annotations": False},
            {"index": 1, "length": 3, "task_index": 1, "has_annotations": False},
            {"index": 2, "length": 3, "task_index": 0, "has_annotations": False},
        ]

    @pytest.mark.asyncio
    async def test_applies_pagination(self, service):
        await service.get_dataset(_DATASET_ID)

        episodes = await service.list_episodes(_DATASET_ID, offset=1, limit=1)

        assert [episode.index for episode in episodes] == [1]

    @pytest.mark.asyncio
    async def test_filters_by_task_and_annotation_state(self, service):
        await service.get_dataset(_DATASET_ID)

        task_episodes = await service.list_episodes(_DATASET_ID, task_index=0)
        annotated_episodes = await service.list_episodes(_DATASET_ID, has_annotations=True)

        assert [episode.index for episode in task_episodes] == [0, 2]
        assert annotated_episodes == []


class TestEpisodeData:
    @pytest.mark.asyncio
    async def test_gets_complete_episode(self, service):
        await service.get_dataset(_DATASET_ID)

        episode = await service.get_episode(_DATASET_ID, 1)

        assert episode is not None
        assert episode.meta.model_dump() == {
            "index": 1,
            "length": 3,
            "task_index": 1,
            "has_annotations": False,
        }
        assert episode.cameras == [_CAMERA]
        assert set(episode.video_urls) == {_CAMERA}
        video_url = urlsplit(episode.video_urls[_CAMERA])
        assert video_url.path == f"/api/datasets/{_DATASET_ID}/episodes/1/video/{_CAMERA}"
        query = parse_qs(video_url.query)
        assert set(query) == {"v"}
        assert len(query["v"]) == 1
        assert len(query["v"][0]) == 64
        assert set(query["v"][0]) <= set("0123456789abcdef")
        assert [point.frame for point in episode.trajectory_data] == [0, 1, 2]
        assert [point.timestamp for point in episode.trajectory_data] == pytest.approx([0.0, 0.05, 0.1])
        assert episode.trajectory_data[0].joint_positions == [1.0, 1.0, 2.0]
        assert episode.trajectory_data[0].joint_velocities == pytest.approx([20.0, 20.0, 20.0])
        assert episode.trajectory_data[0].end_effector_pose == [0.4, 0.5, 0.6]

    @pytest.mark.asyncio
    async def test_out_of_range_episode_returns_none(self, service):
        await service.get_dataset(_DATASET_ID)

        assert await service.get_episode(_DATASET_ID, 3) is None

    @pytest.mark.asyncio
    async def test_gets_trajectory_and_cameras(self, service):
        await service.get_dataset(_DATASET_ID)

        trajectory = await service.get_episode_trajectory(_DATASET_ID, 0)
        cameras = await service.get_episode_cameras(_DATASET_ID, 0)

        assert len(trajectory) == 3
        assert [point.frame for point in trajectory] == [0, 1, 2]
        assert cameras == [_CAMERA]

    @pytest.mark.asyncio
    async def test_public_cache_invalidation_forces_reload(self, tmp_path):
        dataset_path = _write_dataset(tmp_path)
        service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=4)
        await service.get_dataset(_DATASET_ID)
        first = await service.get_episode(_DATASET_ID, 0)
        assert first is not None
        parquet_path = dataset_path / "data" / "chunk-000" / "episode_000000.parquet"
        table = pq.read_table(parquet_path)
        table = table.set_column(
            table.schema.get_field_index("observation.state"),
            "observation.state",
            pa.array([[9.0, 9.0, 9.0]] * 3),
        )
        pq.write_table(table, parquet_path)

        cached = await service.get_episode(_DATASET_ID, 0)
        invalidated = service.invalidate_episode_cache(_DATASET_ID, 0)
        reloaded = await service.get_episode(_DATASET_ID, 0)

        assert cached is first
        assert invalidated == 1
        assert reloaded is not None
        assert reloaded.trajectory_data[0].joint_positions == [9.0, 9.0, 9.0]


class TestMediaPaths:
    @pytest.mark.asyncio
    async def test_resolves_video_path_inside_dataset(self, service):
        await service.get_dataset(_DATASET_ID)

        video_path = service.get_video_file_path(_DATASET_ID, 0, _CAMERA)

        assert video_path is not None
        assert Path(video_path).read_bytes() == b"video-0"
        assert service.is_safe_video_path(video_path) is True

    @pytest.mark.asyncio
    async def test_missing_camera_returns_none(self, service):
        await service.get_dataset(_DATASET_ID)

        assert service.get_video_file_path(_DATASET_ID, 0, "missing") is None

    def test_rejects_video_path_outside_base(self, service, tmp_path):
        outside_path = tmp_path.parent / "outside.mp4"

        assert service.is_safe_video_path(str(outside_path)) is False
