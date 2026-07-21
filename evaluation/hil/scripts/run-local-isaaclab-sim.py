#!/usr/bin/env python3
"""Run bounded Isaac Lab Anymal-C exchanges against a remote JIT policy host."""

# ruff: noqa: E402, I001
from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from isaaclab.app import AppLauncher

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
_QOS_PROFILE = json.dumps(
    {
        "history": "keepLast",
        "depth": 1,
        "reliability": "bestEffort",
        "durability": "volatile",
        "deadline": 0.0,
        "lifespan": 0.0,
        "liveliness": "systemDefault",
        "leaseDuration": 0.0,
    }
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


_PARSER = argparse.ArgumentParser(description=__doc__)
_PARSER.add_argument("--policy-path", type=Path, required=True)
_PARSER.add_argument("--io-descriptor", type=Path, required=True)
_PARSER.add_argument("--output-dir", type=Path, required=True)
_PARSER.add_argument("--run-id", required=True)
_PARSER.add_argument("--steps", type=_positive_int, required=True)
_PARSER.add_argument("--response-timeout-s", type=_positive_float, required=True)
_PARSER.add_argument("--seed", type=int, required=True)
AppLauncher.add_app_launcher_args(_PARSER)
_ARGS = _PARSER.parse_args()
_APP_LAUNCHER = AppLauncher(_ARGS)
simulation_app = _APP_LAUNCHER.app

import gymnasium as gym
import isaaclab_tasks  # noqa: F401
import omni.graph.core as og
import torch
from isaaclab_tasks.utils.parse_cfg import parse_env_cfg
from training.rl.simulation_shutdown import prepare_for_shutdown


@dataclass(frozen=True)
class PolicyTerm:
    """One ordered float32 term in the JIT policy contract."""

    name: str
    shape: tuple[int, ...]

    @property
    def dimension(self) -> int:
        return math.prod(self.shape)


@dataclass(frozen=True)
class PolicyIoDescriptor:
    """Validated I/O schema for one task-specific JIT artifact."""

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


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{field} must be positive")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


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
        if any(
            isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0 for dimension in raw_shape
        ):
            raise ValueError(f"{field}[{index}].shape dimensions must be positive integers")
        terms.append(PolicyTerm(name=name, shape=tuple(raw_shape)))
    return tuple(terms)


def _load_descriptor(descriptor_path: Path, policy_path: Path) -> PolicyIoDescriptor:
    if not policy_path.is_file():
        raise FileNotFoundError(policy_path)
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _latency_statistics(samples: list[float]) -> dict[str, float | int]:
    milliseconds = np.asarray(samples, dtype=np.float64) * 1_000.0
    return {
        "sample_count": int(milliseconds.size),
        "minimum": float(milliseconds.min()),
        "mean": float(milliseconds.mean()),
        "p95": float(np.percentile(milliseconds, 95)),
        "maximum": float(milliseconds.max()),
    }


class IsaacLabRos2Graph:
    """Manually ticked native ROS 2 graph for descriptor-ordered vectors."""

    _GRAPH_PATH = "/HilIsaacLabRos2Graph"

    def __init__(self, run_id: str) -> None:
        topic_prefix = f"/hil/isaaclab/{run_id}"
        og.Controller.edit(
            {"graph_path": self._GRAPH_PATH, "evaluator_name": "execution"},
            {
                og.Controller.Keys.CREATE_NODES: [
                    ("PublishImpulse", "omni.graph.action.OnImpulseEvent"),
                    ("SubscribeImpulse", "omni.graph.action.OnImpulseEvent"),
                    ("ObservationPublisher", "isaacsim.ros2.bridge.ROS2Publisher"),
                    ("ActionSubscriber", "isaacsim.ros2.bridge.ROS2Subscriber"),
                ],
                og.Controller.Keys.CONNECT: [
                    ("PublishImpulse.outputs:execOut", "ObservationPublisher.inputs:execIn"),
                    ("SubscribeImpulse.outputs:execOut", "ActionSubscriber.inputs:execIn"),
                ],
            },
        )
        self._configure_message("ObservationPublisher", f"{topic_prefix}/observation")
        self._configure_message("ActionSubscriber", f"{topic_prefix}/action")
        simulation_app.update()

    def _attribute(self, node_name: str, attribute_name: str) -> Any:
        return og.Controller.attribute(f"{self._GRAPH_PATH}/{node_name}.{attribute_name}")

    def _set(self, node_name: str, attribute_name: str, value: object) -> None:
        og.Controller.set(self._attribute(node_name, attribute_name), value)

    def _get(self, node_name: str, attribute_name: str) -> object:
        return og.Controller.get(self._attribute(node_name, attribute_name))

    def _configure_message(self, node_name: str, topic_name: str) -> None:
        self._set(node_name, "inputs:messagePackage", "std_msgs")
        self._set(node_name, "inputs:messageSubfolder", "msg")
        self._set(node_name, "inputs:messageName", "Float32MultiArray")
        self._set(node_name, "inputs:topicName", topic_name)
        self._set(node_name, "inputs:nodeNamespace", "")
        self._set(node_name, "inputs:queueSize", 1)
        self._set(node_name, "inputs:qosProfile", _QOS_PROFILE)

    def _tick(self, impulse_name: str) -> None:
        og.Controller.set(self._attribute(impulse_name, "state:enableImpulse"), True)
        simulation_app.update()

    def publish_observation(self, observation: np.ndarray, sequence: int) -> None:
        self._set("ObservationPublisher", "inputs:layout:data_offset", sequence)
        self._set("ObservationPublisher", "inputs:data", observation.astype(np.float32).tolist())
        self._tick("PublishImpulse")

    def _action_data(self, sequence: int) -> tuple[float, ...] | None:
        raw_sequence = self._get("ActionSubscriber", "outputs:layout:data_offset")
        if raw_sequence != sequence:
            return None
        raw_data = self._get("ActionSubscriber", "outputs:data")
        if not isinstance(raw_data, list | tuple) or not raw_data:
            return None
        return tuple(float(value) for value in raw_data)

    def wait_for_action(self, sequence: int, action_dimension: int, timeout_s: float) -> np.ndarray:
        deadline = time.monotonic() + timeout_s
        while True:
            self._tick("SubscribeImpulse")
            action_data = self._action_data(sequence)
            if action_data is not None:
                if len(action_data) != action_dimension:
                    raise ValueError(f"action must contain {action_dimension} values, got {len(action_data)}")
                if not np.isfinite(action_data).all():
                    raise ValueError("action values must be finite")
                return np.asarray(action_data, dtype=np.float32)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting {timeout_s} seconds for JIT action")


def _validate_manager_contract(env: Any, descriptor: PolicyIoDescriptor) -> None:
    unwrapped = env.unwrapped
    observation_manager = unwrapped.observation_manager
    action_manager = unwrapped.action_manager
    observation_names = tuple(observation_manager.active_terms[_OBSERVATION_GROUP])
    descriptor_observation_names = tuple(term.name for term in descriptor.observation_terms)
    if observation_names != descriptor_observation_names:
        raise ValueError(f"observation term names must be {descriptor_observation_names}, got {observation_names}")
    observation_shapes = tuple(
        tuple(int(value) for value in shape) for shape in observation_manager.group_obs_term_dim[_OBSERVATION_GROUP]
    )
    descriptor_observation_shapes = tuple(term.shape for term in descriptor.observation_terms)
    if observation_shapes != descriptor_observation_shapes:
        raise ValueError(f"observation term shapes must be {descriptor_observation_shapes}, got {observation_shapes}")
    if not observation_manager.group_obs_concatenate[_OBSERVATION_GROUP]:
        raise ValueError("policy observation group must concatenate terms")

    action_names = tuple(action_manager.active_terms)
    descriptor_action_names = tuple(term.name for term in descriptor.action_terms)
    if action_names != descriptor_action_names:
        raise ValueError(f"action term names must be {descriptor_action_names}, got {action_names}")
    action_dimensions = tuple(int(value) for value in action_manager.action_term_dim)
    descriptor_action_dimensions = tuple(term.dimension for term in descriptor.action_terms)
    if action_dimensions != descriptor_action_dimensions:
        raise ValueError(f"action term dimensions must be {descriptor_action_dimensions}, got {action_dimensions}")
    if int(action_manager.total_action_dim) != descriptor.action_dimension:
        raise ValueError(f"action dimension must be {descriptor.action_dimension}")


def _policy_observation(observations: object, descriptor: PolicyIoDescriptor) -> np.ndarray:
    if not isinstance(observations, dict) or _OBSERVATION_GROUP not in observations:
        raise ValueError(f"observations must contain {_OBSERVATION_GROUP}")
    policy_observation = observations[_OBSERVATION_GROUP]
    if not isinstance(policy_observation, torch.Tensor):
        raise TypeError("policy observation must be a tensor")
    expected_shape = (1, descriptor.observation_dimension)
    if tuple(policy_observation.shape) != expected_shape:
        raise ValueError(f"policy observation must have shape {expected_shape}, got {tuple(policy_observation.shape)}")
    return policy_observation[0].detach().to(device="cpu", dtype=torch.float32).numpy()


def _write_summary(output_dir: Path, summary: dict[str, object], temporary_path: Path) -> None:
    temporary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_path, output_dir / "summary.json")


def main() -> None:
    output_dir = _ARGS.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.unlink(missing_ok=True)
    temporary_path = output_dir / f".summary-{os.getpid()}.json"
    env: Any | None = None

    try:
        if not _ARGS.run_id:
            raise ValueError("run_id must be non-empty")
        descriptor = _load_descriptor(_ARGS.io_descriptor, _ARGS.policy_path)
        env_cfg = parse_env_cfg(_TASK_ID, _ARGS.device, num_envs=1, use_fabric=True)
        env_cfg.seed = _ARGS.seed
        env_cfg.scene.num_envs = 1
        env = gym.make(_TASK_ID, cfg=env_cfg, render_mode=None)
        _validate_manager_contract(env, descriptor)
        step_dt = float(env.unwrapped.step_dt)
        if not math.isclose(descriptor.control_period_s, step_dt, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(f"control_period_s must equal environment step_dt {step_dt}")

        observations, _ = env.reset(seed=_ARGS.seed)
        graph = IsaacLabRos2Graph(_ARGS.run_id)
        completed_episode_returns: list[float] = []
        current_episode_return = 0.0
        termination_count = 0
        reset_count = 1
        latencies: list[float] = []
        started_at = _utc_now()
        wall_start = time.monotonic()

        for sequence in range(1, _ARGS.steps + 1):
            observation = _policy_observation(observations, descriptor)
            observation_started = time.monotonic()
            graph.publish_observation(observation, sequence)
            action_values = graph.wait_for_action(
                sequence,
                descriptor.action_dimension,
                _ARGS.response_timeout_s,
            )
            latencies.append(time.monotonic() - observation_started)
            action_tensor = torch.as_tensor(action_values, dtype=torch.float32, device=env.unwrapped.device).reshape(
                1,
                descriptor.action_dimension,
            )
            observations, reward, terminated, truncated, _ = env.step(action_tensor)
            current_episode_return += float(reward[0].item())
            completed = bool(terminated[0].item()) or bool(truncated[0].item())
            if completed:
                completed_episode_returns.append(current_episode_return)
                current_episode_return = 0.0
                termination_count += 1
                reset_count += 1

        wall_seconds = time.monotonic() - wall_start
        simulated_seconds = _ARGS.steps * step_dt
        summary = {
            "schema_version": "1",
            "mode": "t0-target-policy-hardware-hil",
            "framework": "isaaclab-jit",
            "run_id": _ARGS.run_id,
            "policy": {
                "path": str(_ARGS.policy_path),
                "file": descriptor.policy_file,
                "schema_version": descriptor.schema_version,
            },
            "task_or_scene": {"task_id": _TASK_ID, "observation_group": _OBSERVATION_GROUP},
            "seed": _ARGS.seed,
            "requested_steps": _ARGS.steps,
            "completed_steps": _ARGS.steps,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "outcomes": {
                "completed_episode_returns": completed_episode_returns,
                "termination_count": termination_count,
                "reset_count": reset_count,
                "final_partial_episode_return": current_episode_return,
            },
            "timing": {
                "round_trip_latency_ms": _latency_statistics(latencies),
                "simulated_seconds": simulated_seconds,
                "wall_seconds": wall_seconds,
                "real_time_factor": simulated_seconds / wall_seconds,
            },
        }
        _write_summary(output_dir, summary, temporary_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        if env is not None:
            prepare_for_shutdown()
            env.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        simulation_app.close()
