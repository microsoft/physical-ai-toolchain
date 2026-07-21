#!/usr/bin/env python3
"""Run a local JIT policy for bounded Isaac Lab vector exchanges."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rclpy
import torch
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Float32MultiArray

_TASK_ID = "Isaac-Velocity-Rough-Anymal-C-v0"
_OBSERVATION_GROUP = "policy"
_DESCRIPTOR_FIELDS = {
    "schema_version",
    "policy_file",
    "task_id",
    "observation_group",
    "control_period_s",
    "observation_terms",
    "action_terms",
}
_TERM_FIELDS = {"name", "dtype", "shape"}


@dataclass(frozen=True)
class PolicyTerm:
    """One ordered float32 vector term from the policy descriptor."""

    name: str
    shape: tuple[int, ...]

    @property
    def dimension(self) -> int:
        return math.prod(self.shape)


@dataclass(frozen=True)
class PolicyIoDescriptor:
    """Validated dimensions and ordered terms for one JIT policy."""

    schema_version: str
    policy_file: str
    control_period_s: float
    observation_terms: tuple[PolicyTerm, ...]
    action_terms: tuple[PolicyTerm, ...]

    @property
    def observation_dimension(self) -> int:
        return sum(term.dimension for term in self.observation_terms)

    @property
    def action_dimension(self) -> int:
        return sum(term.dimension for term in self.action_terms)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, required=True)
    parser.add_argument("--io-descriptor", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--steps", type=_positive_int, required=True)
    parser.add_argument("--run-id", required=True)
    return parser.parse_args(arguments)


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{field} must be positive")
    return float(value)


def _parse_terms(value: object, field: str) -> tuple[PolicyTerm, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array")

    terms: list[PolicyTerm] = []
    for index, raw_term in enumerate(value):
        if not isinstance(raw_term, dict) or set(raw_term) != _TERM_FIELDS:
            raise ValueError(f"{field}[{index}] must contain exactly {_TERM_FIELDS}")
        name = _require_string(raw_term["name"], f"{field}[{index}].name")
        if raw_term["dtype"] != "float32":
            raise ValueError(f"{field}[{index}].dtype must be float32")
        raw_shape = raw_term["shape"]
        if not isinstance(raw_shape, list) or not raw_shape:
            raise ValueError(f"{field}[{index}].shape must be a non-empty array")
        has_invalid_dimension = any(
            isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0 for dimension in raw_shape
        )
        if has_invalid_dimension:
            raise ValueError(f"{field}[{index}].shape dimensions must be positive integers")
        terms.append(PolicyTerm(name=name, shape=tuple(raw_shape)))
    return tuple(terms)


def _load_descriptor(descriptor_path: Path, policy_path: Path) -> PolicyIoDescriptor:
    if not descriptor_path.is_file():
        raise FileNotFoundError(descriptor_path)
    if descriptor_path.parent != policy_path.parent:
        raise ValueError("io descriptor must be adjacent to the policy artifact")

    raw_descriptor: Any = json.loads(descriptor_path.read_text(encoding="utf-8"))
    if not isinstance(raw_descriptor, dict) or set(raw_descriptor) != _DESCRIPTOR_FIELDS:
        raise ValueError(f"policy_io.json must contain exactly {_DESCRIPTOR_FIELDS}")

    schema_version = _require_string(raw_descriptor["schema_version"], "schema_version")
    policy_file = _require_string(raw_descriptor["policy_file"], "policy_file")
    if policy_file != policy_path.name:
        raise ValueError(f"policy_file must match {policy_path.name}, got {policy_file}")
    if raw_descriptor["task_id"] != _TASK_ID:
        raise ValueError(f"task_id must be {_TASK_ID}")
    if raw_descriptor["observation_group"] != _OBSERVATION_GROUP:
        raise ValueError(f"observation_group must be {_OBSERVATION_GROUP}")

    return PolicyIoDescriptor(
        schema_version=schema_version,
        policy_file=policy_file,
        control_period_s=_require_positive_number(raw_descriptor["control_period_s"], "control_period_s"),
        observation_terms=_parse_terms(raw_descriptor["observation_terms"], "observation_terms"),
        action_terms=_parse_terms(raw_descriptor["action_terms"], "action_terms"),
    )


class JitPolicyNode(Node):
    """Run one descriptor-ordered JIT inference per incoming observation."""

    def __init__(
        self,
        policy_path: Path,
        descriptor: PolicyIoDescriptor,
        device: str,
        steps: int,
        run_id: str,
    ) -> None:
        super().__init__("local_jit_policy")

        if not policy_path.is_file():
            raise FileNotFoundError(policy_path)
        self._descriptor = descriptor
        self._device = device
        self._steps = steps
        self._published_steps = 0
        self._expected_sequence = 1
        self._policy = torch.jit.load(str(policy_path), map_location=device)
        self._policy.eval()

        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        topic_prefix = f"/hil/isaaclab/{run_id}"
        self.create_subscription(Float32MultiArray, f"{topic_prefix}/observation", self._on_observation, qos)
        self._action_publisher = self.create_publisher(Float32MultiArray, f"{topic_prefix}/action", qos)

    def _on_observation(self, message: Float32MultiArray) -> None:
        if self._published_steps >= self._steps:
            return
        if message.layout.data_offset != self._expected_sequence:
            raise ValueError(
                f"observation sequence must be {self._expected_sequence}, got {message.layout.data_offset}"
            )

        observation = np.asarray(message.data, dtype=np.float32)
        expected_shape = (self._descriptor.observation_dimension,)
        if observation.shape != expected_shape:
            raise ValueError(f"observation must have shape {expected_shape}, got {observation.shape}")

        input_tensor = torch.from_numpy(observation).to(device=self._device, dtype=torch.float32).unsqueeze(0)
        with torch.inference_mode():
            action = self._policy(input_tensor)
        if not isinstance(action, torch.Tensor):
            raise TypeError("JIT policy output must be a tensor")
        expected_action_shape = (1, self._descriptor.action_dimension)
        if tuple(action.shape) != expected_action_shape:
            raise ValueError(f"JIT policy output must have shape {expected_action_shape}, got {tuple(action.shape)}")

        response = Float32MultiArray()
        response.layout.data_offset = message.layout.data_offset
        response.data = action[0].detach().to(device="cpu", dtype=torch.float32).tolist()
        self._action_publisher.publish(response)
        self._published_steps += 1
        self._expected_sequence += 1
        if self._published_steps == self._steps:
            rclpy.shutdown()


def main(arguments: list[str] | None = None) -> None:
    args = _parse_args(arguments)
    if not args.policy_path.is_file():
        raise FileNotFoundError(args.policy_path)
    descriptor = _load_descriptor(args.io_descriptor, args.policy_path)
    rclpy.init(args=None)
    node = JitPolicyNode(args.policy_path, descriptor, args.device, args.steps, args.run_id)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
