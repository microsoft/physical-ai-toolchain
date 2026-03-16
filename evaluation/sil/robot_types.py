"""Robot observation and command types for LeRobot ACT policy inference.

Defines the semantic interface between a robot control system (ROS2, RTDE,
etc.) and the trained ACT policy. These types map directly to the LeRobot
dataset features used during training:

    observation.state      -> RobotObservation.joint_positions  (6 joints)
    observation.images.color -> RobotObservation.color_image    (480x848 RGB)
    action                 -> JointPositionCommand.positions    (6 joints)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np


class JointName(StrEnum):
    """UR10E joint names in kinematic order."""

    SHOULDER_PAN = "shoulder_pan_joint"
    SHOULDER_LIFT = "shoulder_lift_joint"
    ELBOW = "elbow_joint"
    WRIST_1 = "wrist_1_joint"
    WRIST_2 = "wrist_2_joint"
    WRIST_3 = "wrist_3_joint"


# Ordered joint names matching the dataset feature index (joint_0..joint_5).
JOINT_ORDER: tuple[JointName, ...] = tuple(JointName)

NUM_JOINTS: int = len(JOINT_ORDER)

# Training image resolution (height, width, channels).
IMAGE_HEIGHT: int = 480
IMAGE_WIDTH: int = 848
IMAGE_CHANNELS: int = 3

# Control frequency the policy was trained at.
CONTROL_HZ: int = 30


@dataclass(slots=True)
class RobotObservation:
    """A single timestep observation from the UR10E cell.

    Attributes:
        joint_positions: Joint angles in radians, shape ``(6,)``, ordered
            by :data:`JOINT_ORDER`.
        color_image: RGB camera frame, shape ``(480, 848, 3)``, dtype
            ``uint8``, value range ``[0, 255]``.  ``None`` when no camera
            frame is available yet (first-frame edge case).
        timestamp_s: Monotonic timestamp in seconds (from the robot or
            ROS clock).
    """

    joint_positions: np.ndarray
    color_image: np.ndarray | None = None
    timestamp_s: float = 0.0

    def __post_init__(self) -> None:
        if self.joint_positions.shape != (NUM_JOINTS,):
            raise ValueError(f"joint_positions shape must be ({NUM_JOINTS},), got {self.joint_positions.shape}")
        if self.color_image is not None:
            expected = (IMAGE_HEIGHT, IMAGE_WIDTH, IMAGE_CHANNELS)
            if self.color_image.shape != expected:
                raise ValueError(f"color_image shape must be {expected}, got {self.color_image.shape}")


@dataclass(slots=True)
class JointPositionCommand:
    """Position-control command to send to the UR10E.

    Attributes:
        positions: Target joint angles in radians, shape ``(6,)``, ordered
            by :data:`JOINT_ORDER`.
        timestamp_s: Timestamp of the observation that produced this command.
    """

    positions: np.ndarray
    timestamp_s: float = 0.0

    def __post_init__(self) -> None:
        if self.positions.shape != (NUM_JOINTS,):
            raise ValueError(f"positions shape must be ({NUM_JOINTS},), got {self.positions.shape}")

    def as_absolute(self, current: np.ndarray) -> JointPositionCommand:
        """Interpret ``positions`` as deltas and return absolute targets.

        The ACT policy predicts per-step joint deltas. This helper adds
        them to the current joint state to produce an absolute position
        command suitable for the UR driver's position interface.
        """
        return JointPositionCommand(
            positions=current + self.positions,
            timestamp_s=self.timestamp_s,
        )


@dataclass(slots=True)
class RobotState:
    """Aggregate mutable state for the inference control loop.

    Holds the latest observation, the pending action queue, and episode
    bookkeeping.  This is the object a ROS2 node or control script maintains
    across ticks.

    Attributes:
        observation: Most recent observation from the robot.
        episode_step: Current step index within the episode.
        is_episode_active: Whether the episode is running.
        action_queue: Buffered future commands from the latest ACT chunk.
    """

    observation: RobotObservation | None = None
    episode_step: int = 0
    is_episode_active: bool = False
    action_queue: list[JointPositionCommand] = field(default_factory=list)

    def clear_episode(self) -> None:
        """Reset state for a new episode."""
        self.episode_step = 0
        self.is_episode_active = False
        self.action_queue.clear()
