#!/usr/bin/env python3
"""Run a local LeRobot ACT policy for bounded simulated UR10E exchanges."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rclpy
import torch
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, JointState
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

_CANONICAL_JOINT_NAMES = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)
_IMAGE_HEIGHT = 480
_IMAGE_WIDTH = 848
_IMAGE_CHANNELS = 3
_STAMP = tuple[int, int]
_LOCAL_POLICY_REVISION: str | None = None


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--steps", type=_positive_int, required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args(arguments)


def _stamp(message_stamp: object) -> _STAMP:
    return int(message_stamp.sec), int(message_stamp.nanosec)  # type: ignore[attr-defined]


class ActPolicyNode(Node):
    """Join stamped UR10E observations and publish one ACT action per pair."""

    def __init__(self, policy_path: Path, device: str, steps: int, run_id: str) -> None:
        super().__init__("local_lerobot_policy")

        if not policy_path.is_dir():
            raise NotADirectoryError(policy_path)

        from lerobot.policies.act.modeling_act import ACTPolicy
        from lerobot.processor.pipeline import PolicyProcessorPipeline

        self._policy = ACTPolicy.from_pretrained(str(policy_path), revision=_LOCAL_POLICY_REVISION)
        self._policy.to(device)
        self._policy.eval()
        device_override = {"device_processor": {"device": device}}
        self._preprocessor = PolicyProcessorPipeline.from_pretrained(
            str(policy_path),
            "policy_preprocessor.json",
            revision=_LOCAL_POLICY_REVISION,
            overrides=device_override,
        )
        self._postprocessor = PolicyProcessorPipeline.from_pretrained(
            str(policy_path),
            "policy_postprocessor.json",
            revision=_LOCAL_POLICY_REVISION,
            overrides=device_override,
        )
        self._policy.reset()

        self._steps = steps
        self._published_steps = 0
        self._joint_states: dict[_STAMP, JointState] = {}
        self._images: dict[_STAMP, Image] = {}
        self._published_stamps: set[_STAMP] = set()

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        topic_prefix = f"/hil/lerobot/{run_id}"
        self.create_subscription(JointState, f"{topic_prefix}/joint_state", self._on_joint_state, qos)
        self.create_subscription(Image, f"{topic_prefix}/image", self._on_image, qos)
        self._action_publisher = self.create_publisher(JointTrajectory, f"{topic_prefix}/action", qos)

    def _on_joint_state(self, message: JointState) -> None:
        stamp = _stamp(message.header.stamp)
        self._joint_states[stamp] = message
        self._publish_if_complete(stamp)

    def _on_image(self, message: Image) -> None:
        stamp = _stamp(message.header.stamp)
        self._images[stamp] = message
        self._publish_if_complete(stamp)

    def _publish_if_complete(self, stamp: _STAMP) -> None:
        if self._published_steps >= self._steps or stamp in self._published_stamps:
            return

        joint_state = self._joint_states.get(stamp)
        image = self._images.get(stamp)
        if joint_state is None or image is None:
            return

        state = self._joint_state_tensor(joint_state)
        color_image = self._image_tensor(image)
        observation = {
            "observation.state": state,
            "observation.images.color": color_image,
        }
        processed_observation = self._preprocessor(observation)
        with torch.inference_mode():
            action = self._policy.select_action(processed_observation)
        processed_action = self._postprocessor({"action": action})
        action_tensor = processed_action["action"]
        if tuple(action_tensor.shape) != (1, len(_CANONICAL_JOINT_NAMES)):
            raise ValueError(
                f"ACT action must have shape (1, {len(_CANONICAL_JOINT_NAMES)}), got {tuple(action_tensor.shape)}"
            )

        trajectory = JointTrajectory()
        trajectory.header.stamp.sec = stamp[0]
        trajectory.header.stamp.nanosec = stamp[1]
        trajectory.joint_names = list(_CANONICAL_JOINT_NAMES)
        point = JointTrajectoryPoint()
        point.positions = action_tensor[0].detach().to(device="cpu", dtype=torch.float32).tolist()
        trajectory.points = [point]
        self._action_publisher.publish(trajectory)

        self._published_stamps.add(stamp)
        self._published_steps += 1
        del self._joint_states[stamp]
        del self._images[stamp]
        if self._published_steps == self._steps:
            rclpy.shutdown()

    def _joint_state_tensor(self, message: JointState) -> torch.Tensor:
        if tuple(message.name) != _CANONICAL_JOINT_NAMES:
            raise ValueError(f"joint state names must be {_CANONICAL_JOINT_NAMES}, got {tuple(message.name)}")
        if len(message.position) != len(_CANONICAL_JOINT_NAMES):
            raise ValueError(f"joint state must contain {len(_CANONICAL_JOINT_NAMES)} positions")
        return torch.tensor(message.position, dtype=torch.float32)

    def _image_tensor(self, message: Image) -> torch.Tensor:
        if message.encoding != "rgb8":
            raise ValueError(f"image encoding must be rgb8, got {message.encoding}")
        if (message.height, message.width) != (_IMAGE_HEIGHT, _IMAGE_WIDTH):
            raise ValueError(f"image dimensions must be {_IMAGE_HEIGHT}x{_IMAGE_WIDTH}")
        expected_row_bytes = _IMAGE_WIDTH * _IMAGE_CHANNELS
        if message.step < expected_row_bytes:
            raise ValueError(f"image step must be at least {expected_row_bytes}, got {message.step}")
        expected_data_size = message.height * message.step
        if len(message.data) != expected_data_size:
            raise ValueError(f"image data size must be {expected_data_size}, got {len(message.data)}")

        rows = np.frombuffer(message.data, dtype=np.uint8).reshape(message.height, message.step)
        image = rows[:, :expected_row_bytes].reshape(message.height, message.width, _IMAGE_CHANNELS).copy()
        return torch.from_numpy(image).permute(2, 0, 1).to(dtype=torch.float32).div(255.0)


def main(arguments: list[str] | None = None) -> None:
    args = _parse_args(arguments)
    rclpy.init(args=None)
    node = ActPolicyNode(args.policy_path, args.device, args.steps, args.run_id)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
