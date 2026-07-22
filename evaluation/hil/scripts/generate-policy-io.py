#!/usr/bin/env python3
"""Generate policy_io.json from a live Isaac Lab Anymal-C task and JIT policy."""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

from isaaclab.app import AppLauncher

_TASK_ID = "Isaac-Velocity-Rough-Anymal-C-v0"
_OBSERVATION_GROUP = "policy"

_PARSER = argparse.ArgumentParser(description=__doc__)
_PARSER.add_argument("--policy-path", type=Path, required=True)
_PARSER.add_argument("--output", type=Path, default=None)
AppLauncher.add_app_launcher_args(_PARSER)
_ARGS = _PARSER.parse_args()
_ARGS.headless = True
_APP_LAUNCHER = AppLauncher(_ARGS)
simulation_app = _APP_LAUNCHER.app

import gymnasium as gym
import isaaclab_tasks  # noqa: F401
import torch
from isaaclab_tasks.utils.parse_cfg import parse_env_cfg

from training.rl.simulation_shutdown import prepare_for_shutdown


def _normalize_dtype(value: object, field: str) -> str:
    if value not in ("float32", "torch.float32"):
        raise ValueError(f"{field} must be float32, got {value}")
    return "float32"


def _normalize_shape(value: object, field: str) -> list[int]:
    if not isinstance(value, list | tuple) or not value:
        raise ValueError(f"{field} must be a non-empty shape")
    shape = list(value)
    if any(isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0 for dimension in shape):
        raise ValueError(f"{field} dimensions must be positive integers")
    return shape


def _ordered_terms(raw_terms: object, active_names: object, field: str) -> list[dict[str, object]]:
    if not isinstance(raw_terms, list) or not raw_terms:
        raise ValueError(f"{field} must be a non-empty term list")
    if not isinstance(active_names, list) or len(active_names) != len(raw_terms):
        raise ValueError(f"{field} active manager names must match the descriptor term count")

    terms: list[dict[str, object]] = []
    for index, (raw_term, active_name) in enumerate(zip(raw_terms, active_names, strict=True)):
        if not isinstance(raw_term, dict):
            raise ValueError(f"{field}[{index}] must be an object")
        if not isinstance(active_name, str) or not active_name:
            raise ValueError(f"{field}[{index}] active manager name must be a non-empty string")
        terms.append(
            {
                "name": active_name,
                "dtype": _normalize_dtype(raw_term.get("dtype"), f"{field}[{index}].dtype"),
                "shape": _normalize_shape(raw_term.get("shape"), f"{field}[{index}].shape"),
            }
        )
    return terms


def _dimension(terms: list[dict[str, object]]) -> int:
    return sum(math.prod(term["shape"]) for term in terms)  # type: ignore[arg-type]


def _write_json(output_path: Path, descriptor: dict[str, object]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    try:
        temporary_path.write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary_path, output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def main() -> None:
    policy_path = _ARGS.policy_path.resolve()
    if not policy_path.is_file():
        raise FileNotFoundError(policy_path)
    output_path = (_ARGS.output or policy_path.with_name("policy_io.json")).resolve()
    if output_path.parent != policy_path.parent:
        raise ValueError("policy_io.json must be adjacent to the policy artifact")

    env: Any | None = None
    try:
        env_cfg = parse_env_cfg(_TASK_ID, _ARGS.device, num_envs=1, use_fabric=True)
        env_cfg.scene.num_envs = 1
        env = gym.make(_TASK_ID, cfg=env_cfg, render_mode=None)
        env.reset()

        descriptors = env.unwrapped.get_IO_descriptors
        raw_observations = descriptors["observations"]
        if not isinstance(raw_observations, dict) or _OBSERVATION_GROUP not in raw_observations:
            raise ValueError(f"environment descriptors must contain the {_OBSERVATION_GROUP} observation group")
        observation_terms = _ordered_terms(
            raw_observations[_OBSERVATION_GROUP],
            env.unwrapped.observation_manager.active_terms[_OBSERVATION_GROUP],
            "observation_terms",
        )
        action_terms = _ordered_terms(
            descriptors["actions"],
            env.unwrapped.action_manager.active_terms,
            "action_terms",
        )

        observation_dimension = _dimension(observation_terms)
        action_dimension = _dimension(action_terms)
        policy = torch.jit.load(str(policy_path), map_location="cpu")
        policy.eval()
        with torch.inference_mode():
            output = policy(torch.zeros((1, observation_dimension), dtype=torch.float32))
        if not isinstance(output, torch.Tensor) or tuple(output.shape) != (1, action_dimension):
            actual_shape = tuple(output.shape) if isinstance(output, torch.Tensor) else type(output).__name__
            raise ValueError(f"JIT policy output must have shape (1, {action_dimension}), got {actual_shape}")

        descriptor = {
            "schema_version": "1",
            "policy_file": policy_path.name,
            "task_id": _TASK_ID,
            "observation_group": _OBSERVATION_GROUP,
            "control_period_s": float(env.unwrapped.step_dt),
            "observation_terms": observation_terms,
            "action_terms": action_terms,
        }
        _write_json(output_path, descriptor)
        print(f"Wrote {output_path}")
        print(f"Observation dimension: {observation_dimension}")
        print(f"Action dimension: {action_dimension}")
    finally:
        if env is not None:
            prepare_for_shutdown()
            env.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        simulation_app.close()
