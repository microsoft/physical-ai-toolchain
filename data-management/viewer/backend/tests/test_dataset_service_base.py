"""Unit tests for the DatasetFormatHandler protocol and trajectory builder."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.api.models.datasources import DatasetInfo, EpisodeData, EpisodeMeta, TrajectoryPoint
from src.api.services.dataset_service.base import DatasetFormatHandler, build_trajectory


class _FakeHandler:
    """Concrete handler used to exercise runtime_checkable Protocol semantics."""

    def can_handle(self, dataset_path: Path) -> bool:
        return True

    def has_loader(self, dataset_id: str) -> bool:
        return False

    def discover(self, dataset_id: str, dataset_path: Path) -> DatasetInfo | None:
        return None

    def get_loader(self, dataset_id: str, dataset_path: Path) -> bool:
        return True

    def list_episodes(self, dataset_id: str) -> tuple[list[int], dict[int, dict]]:
        return [], {}

    def load_episode(
        self,
        dataset_id: str,
        episode_idx: int,
        dataset_info: DatasetInfo | None = None,
    ) -> EpisodeData | None:
        return None

    def get_trajectory(self, dataset_id: str, episode_idx: int) -> list[TrajectoryPoint]:
        return []

    def get_frame_image(
        self,
        dataset_id: str,
        episode_idx: int,
        frame_idx: int,
        camera: str,
    ) -> bytes | None:
        return None

    def get_cameras(self, dataset_id: str, episode_idx: int) -> list[str]:
        return []

    def get_video_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        return None


class TestProtocolConformance:
    def test_fake_handler_satisfies_protocol(self):
        assert isinstance(_FakeHandler(), DatasetFormatHandler)

    def test_arbitrary_object_does_not_satisfy(self):
        assert not isinstance(object(), DatasetFormatHandler)


class TestProtocolMethodBodies:
    """Invoke Protocol method bodies directly so the `pass` statements execute."""

    def test_protocol_pass_bodies_execute(self):
        handler = _FakeHandler()
        path = Path(".")
        assert DatasetFormatHandler.can_handle(handler, path) is None
        assert DatasetFormatHandler.has_loader(handler, "ds") is None
        assert DatasetFormatHandler.discover(handler, "ds", path) is None
        assert DatasetFormatHandler.get_loader(handler, "ds", path) is None
        assert DatasetFormatHandler.list_episodes(handler, "ds") is None
        assert DatasetFormatHandler.load_episode(handler, "ds", 0) is None
        assert DatasetFormatHandler.load_episode(handler, "ds", 0, None) is None
        assert DatasetFormatHandler.get_trajectory(handler, "ds", 0) is None
        assert DatasetFormatHandler.get_frame_image(handler, "ds", 0, 0, "cam") is None
        assert DatasetFormatHandler.get_cameras(handler, "ds", 0) is None
        assert DatasetFormatHandler.get_video_path(handler, "ds", 0, "cam") is None


class TestBuildTrajectory:
    def test_minimal_inputs_use_defaults(self):
        timestamps = np.array([0.0, 0.1], dtype=np.float64)
        positions = np.zeros((2, 6), dtype=np.float64)
        points = build_trajectory(length=2, timestamps=timestamps, joint_positions=positions)
        assert len(points) == 2
        assert points[0].frame == 0
        assert points[1].frame == 1
        assert points[0].joint_velocities == [0.0] * 6
        assert points[0].end_effector_pose == [0.0] * 6
        assert points[0].gripper_state == 0.0

    def test_optional_arrays_propagate(self):
        timestamps = np.array([0.0], dtype=np.float64)
        positions = np.array([[1.0, 2.0]], dtype=np.float64)
        velocities = np.array([[0.5, 0.5]], dtype=np.float64)
        ee = np.array([[1, 2, 3, 4, 5, 6]], dtype=np.float64)
        gripper = np.array([0.7], dtype=np.float64)
        frames = np.array([42], dtype=np.int64)
        points = build_trajectory(
            length=1,
            timestamps=timestamps,
            joint_positions=positions,
            joint_velocities=velocities,
            end_effector_poses=ee,
            gripper_states=gripper,
            frame_indices=frames,
        )
        assert points[0].frame == 42
        assert points[0].joint_positions == [1.0, 2.0]
        assert points[0].joint_velocities == [0.5, 0.5]
        assert points[0].end_effector_pose == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        assert points[0].gripper_state == pytest.approx(0.7)

    def test_clamp_gripper_bounds_value(self):
        timestamps = np.array([0.0, 0.1], dtype=np.float64)
        positions = np.zeros((2, 6), dtype=np.float64)
        gripper = np.array([-0.5, 1.5], dtype=np.float64)
        points = build_trajectory(
            length=2,
            timestamps=timestamps,
            joint_positions=positions,
            gripper_states=gripper,
            clamp_gripper=True,
        )
        assert points[0].gripper_state == 0.0
        assert points[1].gripper_state == 1.0


class TestEpisodeDataDefaults:
    def test_episode_data_constructs(self):
        ep = EpisodeData(meta=EpisodeMeta(index=0, length=1, task_index=0))
        assert ep.cameras == []
        assert ep.trajectory_data == []
