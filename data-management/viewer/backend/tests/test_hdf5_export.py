"""Tests for HDF5 export functionality.

Covers:
- Edit operation parsing
- Integration tests for the full export pipeline with synthetic HDF5 data
"""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from src.api.models.datasources import FrameInsertion
from src.api.services.hdf5_exporter import (
    EpisodeEditOperations,
    HDF5Exporter,
    SubtaskSegment,
    parse_edit_operations,
)

# ============================================================================
# Helpers
# ============================================================================


def create_test_hdf5(
    path: Path,
    num_frames: int = 10,
    num_joints: int = 6,
    cameras: list[str] | None = None,
) -> None:
    """Create a minimal HDF5 episode file for testing."""
    cameras = cameras or ["top_camera"]
    rng = np.random.default_rng(42)

    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        data.create_dataset("qpos", data=rng.standard_normal((num_frames, num_joints)))
        data.create_dataset("qvel", data=rng.standard_normal((num_frames, num_joints)))
        data.create_dataset("timestamps", data=np.arange(num_frames, dtype=np.float64) / 30.0)
        data.create_dataset("action", data=rng.standard_normal((num_frames, num_joints)))

        obs = f.create_group("observations")
        img_group = obs.create_group("images")
        for camera in cameras:
            img_group.create_dataset(
                camera,
                data=rng.integers(0, 255, (num_frames, 64, 64, 3), dtype=np.uint8),
            )

        f.attrs["fps"] = 30.0
        f.attrs["task_index"] = 0


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def hdf5_dataset_dir(tmp_path: Path) -> Path:
    """Create a directory with synthetic HDF5 episode files."""
    dataset_dir = tmp_path / "test-hdf5-dataset"
    dataset_dir.mkdir()
    for ep_idx in range(3):
        create_test_hdf5(dataset_dir / f"episode_{ep_idx:06d}.hdf5")
    return dataset_dir


@pytest.fixture
def hdf5_export_dir(tmp_path: Path) -> Path:
    """Create an output directory for exports."""
    export_dir = tmp_path / "export-output"
    export_dir.mkdir()
    return export_dir


@pytest.fixture
def exporter(hdf5_dataset_dir: Path, hdf5_export_dir: Path) -> HDF5Exporter:
    """HDF5Exporter instance with synthetic data."""
    return HDF5Exporter(hdf5_dataset_dir, hdf5_export_dir)


# ============================================================================
# Unit Tests: Parse Edit Operations
# ============================================================================


class TestParseEditOperations:
    """Tests for parse_edit_operations()."""

    def test_minimal(self) -> None:
        data = {"datasetId": "test", "episodeIndex": 0}
        result = parse_edit_operations(data)

        assert result.dataset_id == "test"
        assert result.episode_index == 0
        assert result.removed_frames is None
        assert result.inserted_frames is None
        assert result.subtasks is None

    def test_with_removed_frames(self) -> None:
        data = {"datasetId": "ds", "episodeIndex": 1, "removedFrames": [2, 5, 8]}
        result = parse_edit_operations(data)

        assert result.removed_frames == {2, 5, 8}

    def test_with_global_transform(self) -> None:
        data = {
            "datasetId": "ds",
            "episodeIndex": 0,
            "globalTransform": {
                "crop": {"x": 10, "y": 20, "width": 200, "height": 150},
                "resize": {"width": 224, "height": 224},
            },
        }
        result = parse_edit_operations(data)

        assert result.global_transform is not None
        assert result.global_transform.crop.x == 10
        assert result.global_transform.resize.width == 224

    def test_with_camera_transforms(self) -> None:
        data = {
            "datasetId": "ds",
            "episodeIndex": 0,
            "cameraTransforms": {
                "top_camera": {"crop": {"x": 0, "y": 0, "width": 100, "height": 100}},
            },
        }
        result = parse_edit_operations(data)

        assert result.camera_transforms is not None
        assert "top_camera" in result.camera_transforms
        assert result.camera_transforms["top_camera"].crop.width == 100

    def test_with_inserted_frames(self) -> None:
        data = {
            "datasetId": "ds",
            "episodeIndex": 0,
            "insertedFrames": [
                {"afterFrameIndex": 3, "interpolationFactor": 0.5},
                {"afterFrameIndex": 7},
            ],
        }
        result = parse_edit_operations(data)

        assert result.inserted_frames is not None
        assert len(result.inserted_frames) == 2
        assert result.inserted_frames[0].after_frame_index == 3
        assert result.inserted_frames[1].interpolation_factor == 0.5

    def test_with_subtasks(self) -> None:
        data = {
            "datasetId": "ds",
            "episodeIndex": 0,
            "subtasks": [
                {
                    "id": "st-1",
                    "label": "Pick up",
                    "frameRange": [0, 5],
                    "color": "#ff0000",
                    "source": "manual",
                    "description": "Grasp object",
                },
            ],
        }
        result = parse_edit_operations(data)

        assert result.subtasks is not None
        assert len(result.subtasks) == 1
        assert result.subtasks[0].label == "Pick up"
        assert result.subtasks[0].frame_range == (0, 5)


# ============================================================================
# Integration Tests: Export Pipeline
# ============================================================================


class TestExportEpisode:
    """Integration tests for the full export pipeline."""

    def test_export_single_episode_no_edits(self, exporter: HDF5Exporter, hdf5_export_dir: Path) -> None:
        result = exporter.export_episode(episode_index=0)

        hdf5_path = hdf5_export_dir / "episode_000000.hdf5"
        meta_path = hdf5_export_dir / "episode_000000.meta.json"
        assert result.success is True
        assert result.output_files == [str(hdf5_path), str(meta_path)]

        with h5py.File(hdf5_path, "r") as f:
            assert f["data"]["qpos"].shape == (10, 6)
            assert f["observations"]["images"]["top_camera"].shape == (10, 64, 64, 3)

        meta = json.loads(meta_path.read_text())
        assert (meta["original_frames"], meta["output_frames"], meta["edits_applied"]) == (10, 10, False)

    def test_export_with_frame_removal(
        self,
        exporter: HDF5Exporter,
        hdf5_dataset_dir: Path,
        hdf5_export_dir: Path,
    ) -> None:
        edits = EpisodeEditOperations(
            dataset_id="test",
            episode_index=0,
            removed_frames={2, 5, 7},
        )
        result = exporter.export_episode(episode_index=0, edits=edits)

        hdf5_path = hdf5_export_dir / "episode_000000.hdf5"
        with (
            h5py.File(hdf5_dataset_dir / "episode_000000.hdf5", "r") as source,
            h5py.File(hdf5_path, "r") as exported,
        ):
            expected = source["data"]["qpos"][[0, 1, 3, 4, 6, 8, 9]]
            np.testing.assert_array_equal(exported["data"]["qpos"][:], expected)

        meta = json.loads((hdf5_export_dir / "episode_000000.meta.json").read_text())
        assert result.success is True
        assert meta["output_frames"] == 7
        assert meta["edits_applied"] is True

    def test_export_with_subtasks(self, exporter: HDF5Exporter, hdf5_export_dir: Path) -> None:
        edits = EpisodeEditOperations(
            dataset_id="test",
            episode_index=0,
            subtasks=[
                SubtaskSegment(
                    id="st-1",
                    label="Reach",
                    frame_range=(0, 4),
                    color="#ff0000",
                    source="manual",
                ),
                SubtaskSegment(
                    id="st-2",
                    label="Grasp",
                    frame_range=(5, 9),
                    color="#00ff00",
                    source="manual",
                ),
            ],
        )
        result = exporter.export_episode(episode_index=0, edits=edits)

        assert result.success is True

        subtasks_path = hdf5_export_dir / "episode_000000.subtasks.json"
        subtasks = json.loads(subtasks_path.read_text())
        assert subtasks == [
            {
                "id": "st-1",
                "label": "Reach",
                "frame_range": [0, 4],
                "color": "#ff0000",
                "source": "manual",
                "description": None,
            },
            {
                "id": "st-2",
                "label": "Grasp",
                "frame_range": [5, 9],
                "color": "#00ff00",
                "source": "manual",
                "description": None,
            },
        ]

    def test_export_nonexistent_episode(self, exporter: HDF5Exporter) -> None:
        result = exporter.export_episode(episode_index=999)

        assert result.success is False
        assert result.error and "No HDF5 file found for episode 999" in result.error

    def test_export_multiple_episodes(self, exporter: HDF5Exporter, hdf5_export_dir: Path) -> None:
        result = exporter.export_episodes(episode_indices=[0, 1, 2])

        assert result.success is True
        assert result.stats["total_episodes"] == 3
        assert result.stats["total_frames"] == 30
        assert result.stats["removed_frames"] == 0
        assert result.output_files == [
            str(hdf5_export_dir / f"episode_{ep_idx:06d}{suffix}")
            for ep_idx in range(3)
            for suffix in (".hdf5", ".meta.json")
        ]

    def test_failed_batch_preserves_aggregate_statistics(self, exporter: HDF5Exporter) -> None:
        result = exporter.export_episodes(episode_indices=[0, 999])

        assert result.success is False
        assert result.error is not None
        assert result.output_files
        assert result.stats["total_episodes"] == 2
        assert result.stats["total_frames"] == 10
        assert result.stats["removed_frames"] == 0
        assert result.stats["duration_ms"] >= 0

    def test_export_with_frame_insertion(
        self,
        exporter: HDF5Exporter,
        hdf5_dataset_dir: Path,
        hdf5_export_dir: Path,
    ) -> None:
        edits = EpisodeEditOperations(
            dataset_id="test",
            episode_index=0,
            inserted_frames=[
                FrameInsertion(after_frame_index=3, interpolation_factor=0.5),
            ],
        )
        result = exporter.export_episode(episode_index=0, edits=edits)

        hdf5_path = hdf5_export_dir / "episode_000000.hdf5"
        with (
            h5py.File(hdf5_dataset_dir / "episode_000000.hdf5", "r") as source,
            h5py.File(hdf5_path, "r") as exported,
        ):
            source_positions = source["data"]["qpos"][:]
            exported_positions = exported["data"]["qpos"][:]
            assert exported_positions.shape == (11, 6)
            np.testing.assert_allclose(
                exported_positions[4],
                (source_positions[3] + source_positions[4]) / 2,
            )
            np.testing.assert_array_equal(exported_positions[:4], source_positions[:4])
            np.testing.assert_array_equal(exported_positions[5:], source_positions[4:])
        assert result.success is True

    @pytest.mark.parametrize(
        ("after_frame_index", "removed_frames"),
        [
            (3, {3}),
            (9, None),
        ],
    )
    def test_export_skips_insertion_without_a_following_valid_frame(
        self,
        exporter: HDF5Exporter,
        hdf5_dataset_dir: Path,
        hdf5_export_dir: Path,
        after_frame_index: int,
        removed_frames: set[int] | None,
    ) -> None:
        edits = EpisodeEditOperations(
            dataset_id="test",
            episode_index=0,
            removed_frames=removed_frames,
            inserted_frames=[FrameInsertion(after_frame_index=after_frame_index, interpolation_factor=0.5)],
        )

        result = exporter.export_episode(episode_index=0, edits=edits)

        with (
            h5py.File(hdf5_dataset_dir / "episode_000000.hdf5", "r") as source,
            h5py.File(hdf5_export_dir / "episode_000000.hdf5", "r") as exported,
        ):
            expected = np.delete(source["data"]["qpos"][:], list(removed_frames or []), axis=0)
            np.testing.assert_array_equal(exported["data"]["qpos"][:], expected)
        assert result.success is True
