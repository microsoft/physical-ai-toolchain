"""Tests for HDF5 export functionality.

Covers:
- Unit tests for frame filtering, insertion, and edit parsing
- Integration tests for the full export pipeline with synthetic HDF5 data
- API endpoint tests for the export router
"""

import json
import os
from pathlib import Path

import h5py
import numpy as np
import pytest
from fastapi.testclient import TestClient

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
# Unit Tests: Frame Filtering
# ============================================================================


class TestGetValidIndices:
    """Tests for HDF5Exporter._get_valid_indices."""

    def test_all_frames_valid(self, exporter: HDF5Exporter):
        result = exporter._get_valid_indices(10, None)
        assert result == list(range(10))

    def test_some_frames_removed(self, exporter: HDF5Exporter):
        result = exporter._get_valid_indices(10, {2, 5, 7})
        assert result == [0, 1, 3, 4, 6, 8, 9]

    def test_all_frames_removed(self, exporter: HDF5Exporter):
        result = exporter._get_valid_indices(5, {0, 1, 2, 3, 4})
        assert result == []

    def test_removed_frames_beyond_range(self, exporter: HDF5Exporter):
        result = exporter._get_valid_indices(5, {10, 20})
        assert result == list(range(5))

    def test_empty_removed_set(self, exporter: HDF5Exporter):
        result = exporter._get_valid_indices(5, set())
        assert result == list(range(5))


class TestComputeInsertions:
    """Tests for HDF5Exporter._compute_insertions."""

    def test_single_insertion(self, exporter: HDF5Exporter):
        data = np.array([[0.0, 0.0], [2.0, 2.0], [4.0, 4.0]])
        insertions = [FrameInsertion(after_frame_index=0, interpolation_factor=0.5)]
        valid = [0, 1, 2]

        result = exporter._compute_insertions(data, insertions, valid)

        assert len(result) == 1
        pos, row = result[0]
        assert pos == 0
        np.testing.assert_allclose(row, [1.0, 1.0])

    def test_insertion_at_removed_frame_skipped(self, exporter: HDF5Exporter):
        data = np.array([[0.0], [1.0], [2.0], [3.0]])
        insertions = [FrameInsertion(after_frame_index=1, interpolation_factor=0.5)]
        valid = [0, 2, 3]  # frame 1 removed

        result = exporter._compute_insertions(data, insertions, valid)

        assert len(result) == 0

    def test_insertion_at_last_frame_skipped(self, exporter: HDF5Exporter):
        data = np.array([[0.0], [1.0], [2.0]])
        insertions = [FrameInsertion(after_frame_index=2, interpolation_factor=0.5)]
        valid = [0, 1, 2]

        result = exporter._compute_insertions(data, insertions, valid)

        assert len(result) == 0


class TestApplyInsertions:
    """Tests for HDF5Exporter._apply_insertions."""

    def test_single_row_insert(self, exporter: HDF5Exporter):
        data = np.array([[1.0], [3.0]])
        insertions = [(0, np.array([2.0]))]

        result = exporter._apply_insertions(data, insertions)

        assert result.shape == (3, 1)
        np.testing.assert_allclose(result, [[1.0], [2.0], [3.0]])

    def test_multiple_insertions(self, exporter: HDF5Exporter):
        data = np.array([[0.0], [2.0], [4.0]])
        insertions = [(0, np.array([1.0])), (1, np.array([3.0]))]

        result = exporter._apply_insertions(data, insertions)

        assert result.shape == (5, 1)
        np.testing.assert_allclose(result, [[0.0], [1.0], [2.0], [3.0], [4.0]])

    def test_empty_insertions(self, exporter: HDF5Exporter):
        data = np.array([[1.0], [2.0]])

        result = exporter._apply_insertions(data, [])

        np.testing.assert_array_equal(result, data)


# ============================================================================
# Unit Tests: Parse Edit Operations
# ============================================================================


class TestParseEditOperations:
    """Tests for parse_edit_operations()."""

    def test_minimal(self):
        data = {"datasetId": "test", "episodeIndex": 0}
        result = parse_edit_operations(data)

        assert result.dataset_id == "test"
        assert result.episode_index == 0
        assert result.removed_frames is None
        assert result.inserted_frames is None
        assert result.subtasks is None

    def test_with_removed_frames(self):
        data = {"datasetId": "ds", "episodeIndex": 1, "removedFrames": [2, 5, 8]}
        result = parse_edit_operations(data)

        assert result.removed_frames == {2, 5, 8}

    def test_with_global_transform(self):
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

    def test_with_camera_transforms(self):
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

    def test_with_inserted_frames(self):
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

    def test_with_subtasks(self):
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

    def test_export_single_episode_no_edits(self, exporter: HDF5Exporter, hdf5_export_dir: Path):
        result = exporter.export_episode(episode_index=0)

        assert result.success is True
        assert len(result.output_files) >= 2  # .hdf5 + .meta.json

        hdf5_path = hdf5_export_dir / "episode_000000.hdf5"
        assert hdf5_path.exists()

        with h5py.File(hdf5_path, "r") as f:
            assert "data" in f
            assert "qpos" in f["data"]
            assert f["data"]["qpos"].shape[0] == 10  # all frames
            assert "observations" in f
            assert "images" in f["observations"]
            assert "top_camera" in f["observations"]["images"]

        meta_path = hdf5_export_dir / "episode_000000.meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["original_frames"] == 10
        assert meta["output_frames"] == 10

    def test_export_with_frame_removal(self, exporter: HDF5Exporter, hdf5_export_dir: Path):
        edits = EpisodeEditOperations(
            dataset_id="test",
            episode_index=0,
            removed_frames={2, 5, 7},
        )
        result = exporter.export_episode(episode_index=0, edits=edits)

        assert result.success is True

        hdf5_path = hdf5_export_dir / "episode_000000.hdf5"
        with h5py.File(hdf5_path, "r") as f:
            assert f["data"]["qpos"].shape[0] == 7  # 10 - 3 removed

        meta = json.loads((hdf5_export_dir / "episode_000000.meta.json").read_text())
        assert meta["output_frames"] == 7
        assert meta["edits_applied"] is True

    def test_export_with_subtasks(self, exporter: HDF5Exporter, hdf5_export_dir: Path):
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
        assert subtasks_path.exists()
        subtasks = json.loads(subtasks_path.read_text())
        assert len(subtasks) == 2
        assert subtasks[0]["label"] == "Reach"

    def test_export_nonexistent_episode(self, exporter: HDF5Exporter):
        result = exporter.export_episode(episode_index=999)

        assert result.success is False
        assert result.error is not None

    def test_export_multiple_episodes(self, exporter: HDF5Exporter, hdf5_export_dir: Path):
        result = exporter.export_episodes(episode_indices=[0, 1, 2])

        assert result.success is True
        assert result.stats["total_episodes"] == 3

        for ep_idx in range(3):
            assert (hdf5_export_dir / f"episode_{ep_idx:06d}.hdf5").exists()
            assert (hdf5_export_dir / f"episode_{ep_idx:06d}.meta.json").exists()

    def test_export_with_frame_insertion(self, exporter: HDF5Exporter, hdf5_export_dir: Path):
        edits = EpisodeEditOperations(
            dataset_id="test",
            episode_index=0,
            inserted_frames=[
                FrameInsertion(after_frame_index=3, interpolation_factor=0.5),
            ],
        )
        result = exporter.export_episode(episode_index=0, edits=edits)

        assert result.success is True

        hdf5_path = hdf5_export_dir / "episode_000000.hdf5"
        with h5py.File(hdf5_path, "r") as f:
            # 10 original + 1 inserted = 11
            assert f["data"]["qpos"].shape[0] == 11


# ============================================================================
# API Endpoint Tests
# ============================================================================


class TestExportEndpoints:
    """Tests for export router endpoints."""

    @pytest.fixture
    def client(self, tmp_path: Path):
        """TestClient with DATA_DIR pointing to synthetic HDF5 data."""
        # Create dataset structure
        dataset_dir = tmp_path / "test-hdf5-dataset"
        dataset_dir.mkdir()
        for ep_idx in range(3):
            create_test_hdf5(dataset_dir / f"episode_{ep_idx:06d}.hdf5")

        os.environ["DATA_DIR"] = str(tmp_path)

        import src.api.config as config_mod
        import src.api.services.annotation_service as ann_mod
        import src.api.services.dataset_service as ds_mod

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None

        from src.api.main import app

        try:
            with TestClient(app) as c:
                yield c
        finally:
            config_mod._app_config = None
            ds_mod._dataset_service = None
            ann_mod._annotation_service = None

    def test_export_preview(self, client: TestClient):
        response = client.get(
            "/api/datasets/test-hdf5-dataset/export/preview",
            params={"episode_indices": "0,1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "episodeCount" in data
        assert data["episodeCount"] == 2

    def test_export_nonexistent_dataset(self, client: TestClient, tmp_path: Path):
        output_path = str(tmp_path / "output")
        response = client.post(
            "/api/datasets/nonexistent/export",
            json={
                "episodeIndices": [0],
                "outputPath": output_path,
            },
        )
        assert response.status_code == 404

    def test_export_path_traversal_rejected(self, client: TestClient):
        response = client.post(
            "/api/datasets/test-hdf5-dataset/export",
            json={
                "episodeIndices": [0],
                "outputPath": "/tmp/evil-path",
            },
        )
        assert response.status_code == 400
        assert "traversal" in response.json()["detail"].lower()

    def test_export_relative_path_traversal_rejected(self, client: TestClient, tmp_path: Path):
        response = client.post(
            "/api/datasets/test-hdf5-dataset/export",
            json={
                "episodeIndices": [0],
                "outputPath": str(tmp_path / "../../etc/evil"),
            },
        )
        assert response.status_code == 400

    def test_export_success(self, client: TestClient, tmp_path: Path):
        output_path = str(tmp_path / "export-output")
        response = client.post(
            "/api/datasets/test-hdf5-dataset/export",
            json={
                "episodeIndices": [0],
                "outputPath": output_path,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["outputFiles"]) >= 2
