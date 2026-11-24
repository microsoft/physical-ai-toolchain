"""Behavioral coverage for reopening edited HDF5 exports in the viewer."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.api.services.dataset_service.hdf5_handler import HDF5FormatHandler
from src.api.services.hdf5_exporter import EpisodeEditOperations, HDF5Exporter
from tests.test_hdf5_export import create_test_hdf5


def test_exported_episode_preserves_cameras_and_edited_trajectory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "export"
    source.mkdir()
    create_test_hdf5(source / "episode_000000.hdf5", num_frames=120, cameras=["front", "wrist"])
    edits = EpisodeEditOperations(dataset_id="source", episode_index=0, removed_frames={0, 1})
    result = HDF5Exporter(source, destination).export_episode(0, edits)
    assert result.success, result.error

    handler = HDF5FormatHandler()
    assert handler.get_loader("export", destination)
    episode = handler.load_episode("export", 0)
    assert episode is not None
    assert episode.cameras == ["front", "wrist"]
    assert len(episode.trajectory_data) == 118
    np.testing.assert_allclose(episode.trajectory_data[0].timestamp, 2 / 30)
    assert handler.get_frame_image("export", 0, 0, "front") is not None
