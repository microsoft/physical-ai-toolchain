#!/usr/bin/env python3
"""Run bounded simulated UR10E exchanges against a remote ACT policy host."""

# ruff: noqa: E402
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

_CANONICAL_JOINT_NAMES = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)
_CONFIG_FIELDS = {
    "usd_path",
    "articulation_prim_path",
    "camera_prim_path",
    "joint_names",
    "reset_joint_positions",
    "image_width",
    "image_height",
    "image_encoding",
    "physics_dt",
    "rendering_dt",
    "seed",
    "steps",
    "response_timeout_s",
}
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


_PARSER = argparse.ArgumentParser(description=__doc__)
_PARSER.add_argument("--config", type=Path, required=True)
_PARSER.add_argument("--policy-id", required=True)
_PARSER.add_argument("--output-dir", type=Path, required=True)
_PARSER.add_argument("--run-id", required=True)
AppLauncher.add_app_launcher_args(_PARSER)
_ARGS = _PARSER.parse_args()
_APP_LAUNCHER = AppLauncher(_ARGS)
simulation_app = _APP_LAUNCHER.app

import omni.graph.core as og
import omni.replicator.core as rep
import omni.usd
from isaacsim.core.api import SimulationContext
from isaacsim.core.experimental.utils.stage import is_stage_loading
from isaacsim.core.prims import Articulation

from training.rl.simulation_shutdown import prepare_for_shutdown


@dataclass(frozen=True)
class Ur10eSceneConfig:
    """Validated explicit input contract for the user-provided UR10E scene."""

    usd_path: Path
    articulation_prim_path: str
    camera_prim_path: str
    joint_names: tuple[str, ...]
    reset_joint_positions: tuple[float, ...]
    image_width: int
    image_height: int
    image_encoding: str
    physics_dt: float
    rendering_dt: float
    seed: int
    steps: int
    response_timeout_s: float


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_positive_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be a positive number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{field} must be a positive number")
    return parsed


def _require_positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _load_config(config_path: Path) -> Ur10eSceneConfig:
    if not config_path.is_file():
        raise FileNotFoundError(config_path)

    raw_config: Any = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict) or set(raw_config) != _CONFIG_FIELDS:
        raise ValueError(f"config must contain exactly {_CONFIG_FIELDS}")

    raw_joint_names = raw_config["joint_names"]
    if not isinstance(raw_joint_names, list) or tuple(raw_joint_names) != _CANONICAL_JOINT_NAMES:
        raise ValueError(f"joint_names must be {list(_CANONICAL_JOINT_NAMES)}")

    raw_reset_positions = raw_config["reset_joint_positions"]
    if not isinstance(raw_reset_positions, list) or len(raw_reset_positions) != len(_CANONICAL_JOINT_NAMES):
        raise ValueError(f"reset_joint_positions must contain {len(_CANONICAL_JOINT_NAMES)} values")
    if any(
        isinstance(position, bool) or not isinstance(position, int | float) or not math.isfinite(float(position))
        for position in raw_reset_positions
    ):
        raise ValueError("reset_joint_positions must contain finite numeric values")

    if raw_config["image_width"] != 848 or raw_config["image_height"] != 480:
        raise ValueError("image dimensions must be 848x480")
    if raw_config["image_encoding"] != "rgb8":
        raise ValueError("image_encoding must be rgb8")
    if isinstance(raw_config["seed"], bool) or not isinstance(raw_config["seed"], int):
        raise ValueError("seed must be an integer")

    return Ur10eSceneConfig(
        usd_path=Path(_require_string(raw_config["usd_path"], "usd_path")),
        articulation_prim_path=_require_string(raw_config["articulation_prim_path"], "articulation_prim_path"),
        camera_prim_path=_require_string(raw_config["camera_prim_path"], "camera_prim_path"),
        joint_names=tuple(raw_joint_names),
        reset_joint_positions=tuple(float(position) for position in raw_reset_positions),
        image_width=raw_config["image_width"],
        image_height=raw_config["image_height"],
        image_encoding=raw_config["image_encoding"],
        physics_dt=_require_positive_float(raw_config["physics_dt"], "physics_dt"),
        rendering_dt=_require_positive_float(raw_config["rendering_dt"], "rendering_dt"),
        seed=raw_config["seed"],
        steps=_require_positive_int(raw_config["steps"], "steps"),
        response_timeout_s=_require_positive_float(raw_config["response_timeout_s"], "response_timeout_s"),
    )


def _substeps_per_exchange(config: Ur10eSceneConfig) -> int:
    ratio = config.rendering_dt / config.physics_dt
    substeps = round(ratio)
    if substeps <= 0 or not math.isclose(ratio, substeps, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("rendering_dt / physics_dt must be an integer")
    return substeps


def _timestamp(simulated_seconds: float) -> tuple[int, int]:
    seconds = int(simulated_seconds)
    nanoseconds = round((simulated_seconds - seconds) * 1_000_000_000)
    if nanoseconds == 1_000_000_000:
        return seconds + 1, 0
    return seconds, nanoseconds


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


class Ur10eRos2Graph:
    """Manually ticked native ROS 2 graph for one simulated UR10E exchange."""

    _GRAPH_PATH = "/HilLerobotRos2Graph"

    def __init__(self, run_id: str) -> None:
        self._topic_prefix = f"/hil/lerobot/{run_id}"
        og.Controller.edit(
            {"graph_path": self._GRAPH_PATH, "evaluator_name": "execution"},
            {
                og.Controller.Keys.CREATE_NODES: [
                    ("PublishImpulse", "omni.graph.action.OnImpulseEvent"),
                    ("SubscribeImpulse", "omni.graph.action.OnImpulseEvent"),
                    ("JointStatePublisher", "isaacsim.ros2.bridge.ROS2Publisher"),
                    ("ImagePublisher", "isaacsim.ros2.bridge.ROS2Publisher"),
                    ("ActionSubscriber", "isaacsim.ros2.bridge.ROS2Subscriber"),
                ],
                og.Controller.Keys.CONNECT: [
                    ("PublishImpulse.outputs:execOut", "JointStatePublisher.inputs:execIn"),
                    ("PublishImpulse.outputs:execOut", "ImagePublisher.inputs:execIn"),
                    ("SubscribeImpulse.outputs:execOut", "ActionSubscriber.inputs:execIn"),
                ],
            },
        )
        self._configure_message(
            "JointStatePublisher",
            "sensor_msgs",
            "JointState",
            f"{self._topic_prefix}/joint_state",
        )
        self._configure_message("ImagePublisher", "sensor_msgs", "Image", f"{self._topic_prefix}/image")
        self._configure_message(
            "ActionSubscriber",
            "trajectory_msgs",
            "JointTrajectory",
            f"{self._topic_prefix}/action",
        )
        simulation_app.update()

    def _attribute(self, node_name: str, attribute_name: str) -> Any:
        return og.Controller.attribute(f"{self._GRAPH_PATH}/{node_name}.{attribute_name}")

    def _set(self, node_name: str, attribute_name: str, value: object) -> None:
        og.Controller.set(self._attribute(node_name, attribute_name), value)

    def _get(self, node_name: str, attribute_name: str) -> object:
        return og.Controller.get(self._attribute(node_name, attribute_name))

    def _configure_message(self, node_name: str, package: str, message_name: str, topic_name: str) -> None:
        self._set(node_name, "inputs:messagePackage", package)
        self._set(node_name, "inputs:messageSubfolder", "msg")
        self._set(node_name, "inputs:messageName", message_name)
        self._set(node_name, "inputs:topicName", topic_name)
        self._set(node_name, "inputs:nodeNamespace", "")
        self._set(node_name, "inputs:queueSize", 1)
        self._set(node_name, "inputs:qosProfile", _QOS_PROFILE)

    def _tick(self, impulse_name: str) -> None:
        og.Controller.set(self._attribute(impulse_name, "state:enableImpulse"), True)
        simulation_app.update()

    def publish_observation(
        self,
        stamp: tuple[int, int],
        joint_names: tuple[str, ...],
        joint_positions: np.ndarray,
        image: np.ndarray,
    ) -> None:
        seconds, nanoseconds = stamp
        self._set("JointStatePublisher", "inputs:header:stamp:sec", seconds)
        self._set("JointStatePublisher", "inputs:header:stamp:nanosec", nanoseconds)
        self._set("JointStatePublisher", "inputs:name", list(joint_names))
        self._set("JointStatePublisher", "inputs:position", joint_positions.astype(np.float64).tolist())
        self._set("ImagePublisher", "inputs:header:stamp:sec", seconds)
        self._set("ImagePublisher", "inputs:header:stamp:nanosec", nanoseconds)
        self._set("ImagePublisher", "inputs:height", int(image.shape[0]))
        self._set("ImagePublisher", "inputs:width", int(image.shape[1]))
        self._set("ImagePublisher", "inputs:encoding", "rgb8")
        self._set("ImagePublisher", "inputs:is_bigendian", 0)
        self._set("ImagePublisher", "inputs:step", int(image.shape[1] * image.shape[2]))
        self._set("ImagePublisher", "inputs:data", image.reshape(-1))
        self._tick("PublishImpulse")

    def _trajectory_message(self) -> tuple[tuple[int, int], tuple[object, ...]] | None:
        raw_points = self._get("ActionSubscriber", "outputs:points")
        if not isinstance(raw_points, list | tuple) or not raw_points:
            return None
        stamp = (
            int(self._get("ActionSubscriber", "outputs:header:stamp:sec")),
            int(self._get("ActionSubscriber", "outputs:header:stamp:nanosec")),
        )
        return stamp, tuple(raw_points)

    def action_snapshot(self) -> tuple[tuple[int, int], tuple[object, ...]] | None:
        return self._trajectory_message()

    def wait_for_delta(
        self,
        stamp: tuple[int, int],
        timeout_s: float,
        baseline: tuple[tuple[int, int], tuple[object, ...]] | None,
    ) -> np.ndarray:
        deadline = time.monotonic() + timeout_s
        while True:
            self._tick("SubscribeImpulse")
            message = self._trajectory_message()
            if message is not None and message != baseline:
                response_stamp, points = message
                if response_stamp != stamp:
                    raise ValueError(f"action stamp must equal {stamp}, got {response_stamp}")
                raw_joint_names = self._get("ActionSubscriber", "outputs:joint_names")
                if tuple(raw_joint_names) != _CANONICAL_JOINT_NAMES:
                    raise ValueError(f"action joint_names must be {_CANONICAL_JOINT_NAMES}")
                if len(points) != 1:
                    raise ValueError("action must contain exactly one trajectory point")
                raw_point = points[0]
                if not isinstance(raw_point, str):
                    raw_point = str(raw_point)
                point: Any = json.loads(raw_point)
                if not isinstance(point, dict):
                    raise ValueError("trajectory point must be a JSON object")
                raw_positions = point.get("positions")
                if not isinstance(raw_positions, list) or len(raw_positions) != len(_CANONICAL_JOINT_NAMES):
                    raise ValueError(f"trajectory point must contain {len(_CANONICAL_JOINT_NAMES)} positions")
                if any(
                    isinstance(position, bool)
                    or not isinstance(position, int | float)
                    or not math.isfinite(float(position))
                    for position in raw_positions
                ):
                    raise ValueError("trajectory positions must be finite numeric values")
                return np.asarray(raw_positions, dtype=np.float64)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting {timeout_s} seconds for ACT action")


def _load_stage(config: Ur10eSceneConfig) -> object:
    if not config.usd_path.is_file():
        raise FileNotFoundError(config.usd_path)
    context = omni.usd.get_context()
    context.open_stage(str(config.usd_path))
    while is_stage_loading():
        simulation_app.update()
    stage = context.get_stage()
    if stage is None:
        raise RuntimeError(f"failed to load USD stage {config.usd_path}")
    if not stage.GetPrimAtPath(config.articulation_prim_path).IsValid():
        raise ValueError(f"articulation prim does not exist: {config.articulation_prim_path}")
    if not stage.GetPrimAtPath(config.camera_prim_path).IsValid():
        raise ValueError(f"camera prim does not exist: {config.camera_prim_path}")
    return stage


def _bind_camera(config: Ur10eSceneConfig) -> Any:
    render_product = rep.create.render_product(
        config.camera_prim_path,
        resolution=(config.image_width, config.image_height),
    )
    rgb_annotator = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb_annotator.attach([render_product])
    simulation_app.update()
    return rgb_annotator


def _read_rgb(annotator: Any, config: Ur10eSceneConfig) -> np.ndarray:
    image = np.asarray(annotator.get_data())
    if image.shape == (config.image_height, config.image_width, 4) and image.dtype == np.uint8:
        image = image[:, :, :3]
    expected_shape = (config.image_height, config.image_width, 3)
    if image.shape != expected_shape or image.dtype != np.uint8:
        raise ValueError(
            f"camera RGB output must have shape {expected_shape} and dtype uint8, got {image.shape} {image.dtype}"
        )
    return image


def _write_summary(output_dir: Path, summary: dict[str, object], temporary_path: Path) -> None:
    temporary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_path, output_dir / "summary.json")


def main() -> None:
    output_dir = _ARGS.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.unlink(missing_ok=True)
    temporary_path = output_dir / f".summary-{os.getpid()}.json"

    try:
        config = _load_config(_ARGS.config)
        if not _ARGS.run_id:
            raise ValueError("run_id must be non-empty")
        if not _ARGS.policy_id:
            raise ValueError("policy_id must be non-empty")
        substeps = _substeps_per_exchange(config)
        _load_stage(config)
        rep.set_global_seed(config.seed)

        simulation_context = SimulationContext(physics_dt=config.physics_dt, rendering_dt=config.rendering_dt)
        simulation_context.reset()
        articulation = Articulation(prim_paths_expr=config.articulation_prim_path)
        articulation.initialize()
        if tuple(articulation.dof_names) != config.joint_names:
            raise ValueError(
                f"articulation joint names must be {config.joint_names}, got {tuple(articulation.dof_names)}"
            )

        reset_positions = np.asarray(config.reset_joint_positions, dtype=np.float64).reshape(1, -1)
        articulation.set_joint_positions(reset_positions)
        articulation.set_joint_velocities(np.zeros_like(reset_positions))
        simulation_context.step(render=True)
        camera = _bind_camera(config)
        for _ in range(5):
            simulation_context.step(render=True)
        graph = Ur10eRos2Graph(_ARGS.run_id)

        latencies: list[float] = []
        completed_steps = 0
        simulated_seconds = 0.0
        started_at = _utc_now()
        wall_start = time.monotonic()
        for _ in range(config.steps):
            stamp = _timestamp(simulated_seconds)
            current_positions = np.asarray(articulation.get_joint_positions(), dtype=np.float64).reshape(1, -1)
            if current_positions.shape != (1, len(config.joint_names)):
                raise ValueError(f"articulation joint positions must have shape (1, {len(config.joint_names)})")
            image = _read_rgb(camera, config)
            action_baseline = graph.action_snapshot()
            observation_started = time.monotonic()
            graph.publish_observation(stamp, config.joint_names, current_positions[0], image)
            action_delta = graph.wait_for_delta(stamp, config.response_timeout_s, action_baseline)
            latencies.append(time.monotonic() - observation_started)
            articulation.set_joint_position_targets(current_positions + action_delta)
            for substep in range(substeps):
                simulation_context.step(render=substep == substeps - 1)
            simulated_seconds += config.rendering_dt
            completed_steps += 1

        wall_seconds = time.monotonic() - wall_start
        final_positions = np.asarray(articulation.get_joint_positions(), dtype=np.float64).reshape(-1)
        if final_positions.shape != (len(config.joint_names),):
            raise ValueError(f"final joint positions must have shape ({len(config.joint_names)},)")
        summary = {
            "schema_version": "1",
            "mode": "t0-target-policy-hardware-hil",
            "framework": "lerobot-act",
            "run_id": _ARGS.run_id,
            "policy": {"id": _ARGS.policy_id},
            "task_or_scene": {
                "usd_path": str(config.usd_path),
                "articulation_prim_path": config.articulation_prim_path,
                "camera_prim_path": config.camera_prim_path,
            },
            "seed": config.seed,
            "requested_steps": config.steps,
            "completed_steps": completed_steps,
            "started_at": started_at,
            "finished_at": _utc_now(),
            "outcomes": {
                "reset_count": 1,
                "final_joint_positions": final_positions.tolist(),
                "termination_reason": "step-limit",
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


if __name__ == "__main__":
    try:
        main()
    finally:
        prepare_for_shutdown()
        simulation_app.close()
