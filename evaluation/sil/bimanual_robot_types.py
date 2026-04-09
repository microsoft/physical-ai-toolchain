"""Bimanual robot observation and command types for dual-arm VLA inference.

Extends the single-arm UR10e types in :mod:`robot_types` with dual-arm
support for bimanual manipulation tasks. Designed for TwinVLA and RoboTwin
2.0 style datasets where actions are 20-dimensional end-effector commands
(2 arms x 10D: 3 pos + 6 rot_6d + 1 gripper).

Observation layout::

    left.joint_positions   (6,)  UR5e kinematic chain
    left.gripper_position  (1,)  normalized [0..1]
    right.joint_positions  (6,)  UR5e kinematic chain
    right.gripper_position (1,)  normalized [0..1]
    proprioception         (14,) concatenated flat vector

Action layout (TwinVLA 20D EEF)::

    left_eef_pos     (3,)  xyz delta
    left_eef_rot6d   (6,)  rotation-6d representation
    left_gripper     (1,)  gripper command
    right_eef_pos    (3,)  xyz delta
    right_eef_rot6d  (6,)  rotation-6d representation
    right_gripper    (1,)  gripper command
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

# UR5e constants
UR5E_NUM_JOINTS: int = 6
BIMANUAL_NUM_JOINTS: int = UR5E_NUM_JOINTS * 2

# TwinVLA action space: 2 arms x (3 pos + 6 rot_6d + 1 gripper) = 20
_EEF_DIM: int = 10
TWINVLA_ACTION_DIM: int = _EEF_DIM * 2

# Control frequency for VLA inference
VLA_CONTROL_HZ: int = 30


class UR5eJointName(StrEnum):
    """UR5e joint names in kinematic order."""

    SHOULDER_PAN = "shoulder_pan_joint"
    SHOULDER_LIFT = "shoulder_lift_joint"
    ELBOW = "elbow_joint"
    WRIST_1 = "wrist_1_joint"
    WRIST_2 = "wrist_2_joint"
    WRIST_3 = "wrist_3_joint"


UR5E_JOINT_ORDER: tuple[UR5eJointName, ...] = tuple(UR5eJointName)


@dataclass(slots=True)
class SingleArmObservation:
    """Observation from one arm of a bimanual setup.

    Attributes:
        joint_positions: Joint angles in radians, shape ``(6,)``.
        gripper_position: Gripper opening, normalized ``[0, 1]``. ``0`` is
            fully closed, ``1`` is fully open.
    """

    joint_positions: np.ndarray
    gripper_position: float

    def __post_init__(self) -> None:
        if self.joint_positions.shape != (UR5E_NUM_JOINTS,):
            raise ValueError(
                f"joint_positions shape must be ({UR5E_NUM_JOINTS},), "
                f"got {self.joint_positions.shape}"
            )

    @property
    def flat(self) -> np.ndarray:
        """Concatenated state vector, shape ``(7,)``."""
        return np.concatenate([self.joint_positions, [self.gripper_position]])


@dataclass(slots=True)
class BimanualObservation:
    """Timestep observation for a bimanual (2x UR5e) cell.

    Attributes:
        left: Left arm state.
        right: Right arm state.
        color_images: Camera frames keyed by camera name. Typical keys are
            ``"head"``, ``"left_wrist"``, ``"right_wrist"``.
        language_instruction: Natural language task instruction for VLA
            conditioning (e.g. ``"Pick up the box and place it on the belt"``).
        timestamp_s: Monotonic timestamp in seconds.
    """

    left: SingleArmObservation
    right: SingleArmObservation
    color_images: dict[str, np.ndarray] | None = None
    language_instruction: str | None = None
    timestamp_s: float = 0.0

    @property
    def proprioception(self) -> np.ndarray:
        """Flat proprioception vector, shape ``(14,)``.

        Layout: ``[left_joints(6), left_gripper(1), right_joints(6), right_gripper(1)]``.
        """
        return np.concatenate([self.left.flat, self.right.flat])


@dataclass(slots=True)
class BimanualAction:
    """20-dimensional EEF action for bimanual control.

    Each arm contributes 10 dims: ``[pos(3), rot_6d(6), gripper(1)]``.

    Attributes:
        left_eef_pos: Left arm position delta, shape ``(3,)``.
        left_eef_rot6d: Left arm rotation in 6D representation, shape ``(6,)``.
        left_gripper: Left gripper command, scalar.
        right_eef_pos: Right arm position delta, shape ``(3,)``.
        right_eef_rot6d: Right arm rotation in 6D representation, shape ``(6,)``.
        right_gripper: Right gripper command, scalar.
    """

    left_eef_pos: np.ndarray
    left_eef_rot6d: np.ndarray
    left_gripper: float
    right_eef_pos: np.ndarray
    right_eef_rot6d: np.ndarray
    right_gripper: float

    @classmethod
    def from_twinvla(cls, raw: np.ndarray) -> BimanualAction:
        """Parse a 20D TwinVLA action vector into structured fields.

        Expected layout:
        ``[left_pos(3), left_rot6d(6), left_grip(1),
          right_pos(3), right_rot6d(6), right_grip(1)]``
        """
        if raw.shape != (TWINVLA_ACTION_DIM,):
            raise ValueError(
                f"Expected shape ({TWINVLA_ACTION_DIM},), got {raw.shape}"
            )
        return cls(
            left_eef_pos=raw[0:3],
            left_eef_rot6d=raw[3:9],
            left_gripper=float(raw[9]),
            right_eef_pos=raw[10:13],
            right_eef_rot6d=raw[13:19],
            right_gripper=float(raw[19]),
        )

    @property
    def combined(self) -> np.ndarray:
        """Flat action vector, shape ``(20,)``."""
        return np.concatenate([
            self.left_eef_pos,
            self.left_eef_rot6d,
            [self.left_gripper],
            self.right_eef_pos,
            self.right_eef_rot6d,
            [self.right_gripper],
        ])
