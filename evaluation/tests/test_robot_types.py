"""Unit tests for ``sil.robot_types``."""

from __future__ import annotations

import numpy as np
import pytest
from sil.robot_types import (
    IMAGE_CHANNELS,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    JOINT_ORDER,
    NUM_JOINTS,
    JointName,
    JointPositionCommand,
    RobotObservation,
    RobotState,
)


class TestJointName:
    def test_enum_members(self) -> None:
        expected = {
            "SHOULDER_PAN",
            "SHOULDER_LIFT",
            "ELBOW",
            "WRIST_1",
            "WRIST_2",
            "WRIST_3",
        }
        assert {member.name for member in JointName} == expected

    def test_joint_order_length(self) -> None:
        assert len(JOINT_ORDER) == NUM_JOINTS
        assert NUM_JOINTS == 6

    def test_string_values(self) -> None:
        for member in JointName:
            assert member.value.endswith("_joint")


class TestRobotObservation:
    def test_valid_construction(self, joint_positions: np.ndarray) -> None:
        obs = RobotObservation(joint_positions=joint_positions)
        assert obs.joint_positions.shape == (NUM_JOINTS,)
        assert obs.color_image is None
        assert obs.timestamp_s == 0.0

    def test_with_color_image(self, joint_positions: np.ndarray, color_image: np.ndarray) -> None:
        obs = RobotObservation(joint_positions=joint_positions, color_image=color_image)
        assert obs.color_image is not None
        assert obs.color_image.shape == (IMAGE_HEIGHT, IMAGE_WIDTH, IMAGE_CHANNELS)

    def test_invalid_joint_shape(self) -> None:
        with pytest.raises(ValueError, match="joint_positions shape"):
            RobotObservation(joint_positions=np.zeros(3))

    def test_invalid_image_shape(self, joint_positions: np.ndarray) -> None:
        with pytest.raises(ValueError, match="color_image shape"):
            RobotObservation(
                joint_positions=joint_positions,
                color_image=np.zeros((10, 10, 3), dtype=np.uint8),
            )

    def test_none_color_image(self, joint_positions: np.ndarray) -> None:
        obs = RobotObservation(joint_positions=joint_positions, color_image=None)
        assert obs.color_image is None


class TestJointPositionCommand:
    def test_valid_construction(self, joint_positions: np.ndarray) -> None:
        cmd = JointPositionCommand(positions=joint_positions, timestamp_s=1.5)
        assert cmd.positions.shape == (NUM_JOINTS,)
        assert cmd.timestamp_s == 1.5

    def test_invalid_shape(self) -> None:
        with pytest.raises(ValueError, match="positions shape"):
            JointPositionCommand(positions=np.zeros(4))

    def test_as_absolute(self, rng: np.random.Generator) -> None:
        deltas = rng.normal(0, 0.1, size=(NUM_JOINTS,))
        current = rng.normal(0, 1.0, size=(NUM_JOINTS,))
        cmd = JointPositionCommand(positions=deltas)
        absolute = cmd.as_absolute(current)
        np.testing.assert_allclose(absolute.positions, current + deltas)

    def test_as_absolute_preserves_timestamp(self, joint_positions: np.ndarray) -> None:
        cmd = JointPositionCommand(positions=joint_positions, timestamp_s=3.14)
        absolute = cmd.as_absolute(np.zeros(NUM_JOINTS))
        assert absolute.timestamp_s == 3.14


class TestRobotState:
    def test_default_state(self) -> None:
        state = RobotState()
        assert state.observation is None
        assert state.episode_step == 0
        assert state.is_episode_active is False
        assert state.action_queue == []

    def test_clear_episode(self, joint_positions: np.ndarray) -> None:
        state = RobotState(
            observation=RobotObservation(joint_positions=joint_positions),
            episode_step=42,
            is_episode_active=True,
        )
        state.clear_episode()
        assert state.episode_step == 0
        assert state.is_episode_active is False

    def test_clear_episode_empties_queue(self, joint_positions: np.ndarray) -> None:
        state = RobotState(
            action_queue=[
                JointPositionCommand(positions=joint_positions),
                JointPositionCommand(positions=joint_positions),
            ],
        )
        state.clear_episode()
        assert state.action_queue == []
