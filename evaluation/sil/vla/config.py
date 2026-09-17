"""Strict checkpoint, observation, action, and episode contracts for VLA evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = 1
PROTOCOL = "physical_ai_vla_policy_v1"
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


def require(condition: bool, message: str) -> None:
    """Reject an invalid contract before it reaches a model or simulator."""
    if not condition:
        raise ValueError(message)


def canonical(value: Any) -> bytes:
    """Encode finite, deterministic JSON for provenance and protocol identities."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    """Hash canonical JSON, rather than an embedded self-hash."""
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    require(len(result) == len(pairs), "Duplicate JSON keys")
    return result


def read_json(path: Path) -> dict[str, Any]:
    """Read a finite JSON object without silently accepting duplicate fields."""
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    canonical(result)
    require(isinstance(result, dict), f"Expected a JSON object: {path}")
    return result


def _object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    require(isinstance(value, dict) and set(value) == keys, f"{label} requires exactly {sorted(keys)}")
    return value


def _text(value: Any, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()) and value.isprintable(), f"Invalid {label}")
    return value


def _integer(value: Any, lower: int, upper: int, label: str) -> int:
    require(type(value) is int and lower <= value <= upper, f"{label} must be an integer in {lower}..{upper}")
    return value


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    require(type(value) in (int, float), f"{label} must be numeric, not boolean")
    require(math.isfinite(value) and (value > 0 if positive else value >= 0), f"Invalid {label}")
    return float(value)


def _path(value: Any, root: Path, label: str) -> Path:
    path = Path(_text(value, label)).expanduser()
    require("://" not in value, f"{label} must be a local path")
    return (root / path).resolve()


def finite_array(value: Any, shape: tuple[int, ...], label: str) -> np.ndarray:
    """Require numeric arrays of the exact declared shape; never pad missing joints."""
    array = np.asarray(value)
    require(array.dtype.kind in "fiu" and array.shape == shape, f"{label} must have numeric shape {shape}")
    result = array.astype(np.float64)
    require(bool(np.isfinite(result).all()), f"{label} contains non-finite values")
    return result


@dataclass(frozen=True)
class VectorMapping:
    """Select/reorder channels, then apply an explicitly declared affine unit conversion."""

    indices: tuple[int, ...]
    scale: tuple[float, ...]
    offset: tuple[float, ...]

    @classmethod
    def parse(cls, value: Any, label: str) -> VectorMapping:
        value = _object(value, {"indices", "scale", "offset"}, label)
        indices = value["indices"]
        require(isinstance(indices, list) and 1 <= len(indices) <= 64, f"Invalid {label}.indices")
        indices = tuple(_integer(index, 0, 63, label) for index in indices)
        require(len(set(indices)) == len(indices), f"Duplicate {label} indices")
        for field in ("scale", "offset"):
            coefficients = value[field]
            require(
                isinstance(coefficients, list) and all(type(number) in (int, float) for number in coefficients),
                f"{label}.{field} must contain numbers, not booleans",
            )
        scale = finite_array(value["scale"], (len(indices),), f"{label}.scale")
        offset = finite_array(value["offset"], (len(indices),), f"{label}.offset")
        require(bool(np.all(scale != 0)), f"{label}.scale must not erase channels")
        return cls(indices, tuple(scale.tolist()), tuple(offset.tolist()))

    def apply(self, value: np.ndarray) -> np.ndarray:
        """Convert a vector or batch without clipping or normalization-statistic substitution."""
        array = np.asarray(value)
        require(array.ndim in (1, 2) and array.shape[-1] > max(self.indices), "Channel mapping exceeds input width")
        require(array.dtype.kind in "fiu" and bool(np.isfinite(array).all()), "Invalid mapping input")
        result = array[..., list(self.indices)] * np.asarray(self.scale) + np.asarray(self.offset)
        require(bool(np.isfinite(result).all()), "Mapped vector is not finite")
        return result


@dataclass(frozen=True)
class PolicyConfig:
    """Backend output is decoded through its checkpoint transforms before the explicit action mapping."""

    backend: str
    checkpoint: Path
    checkpoint_sha256: str
    training_config: str | None
    state_key: str
    prompt_key: str
    image_keys: dict[str, str]
    state_mapping: VectorMapping
    action_mapping: VectorMapping
    delta_indices: tuple[int, ...]
    action_horizon: int


@dataclass(frozen=True)
class TaskConfig:
    """Explicit task owner dependencies; the generic runner never imports experiment archives."""

    adapter: str
    producer_root: Path
    expert_config: Path
    stage: Path
    reference_root: Path
    runtime_assets: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationConfig:
    """Shared model/simulator protocol, with evaluation settings independent of model defaults."""

    path: Path
    source: dict[str, Any]
    sha256: str
    policy: PolicyConfig
    task: TaskConfig
    channels: tuple[str, ...]
    image_shapes: dict[str, tuple[int, int, int]]
    instruction: str
    control_hz: int
    execution_horizon: int
    episodes: tuple[tuple[str, int], ...]
    max_steps: int
    timeout_seconds: float
    minimum_free_gib: float
    joint_limit_behavior: str

    def metadata(self) -> dict[str, Any]:
        """Require the same checkpoint, channels, timing, and mappings at both process boundaries."""
        return {
            "protocol": PROTOCOL,
            "config_sha256": self.sha256,
            "checkpoint_sha256": self.policy.checkpoint_sha256,
            "backend": self.policy.backend,
            "channels": list(self.channels),
            "action_representation": "absolute_joint_positions",
            "units": "rad",
            "control_hz": self.control_hz,
            "action_horizon": self.policy.action_horizon,
            "execution_horizon": self.execution_horizon,
            "image_shapes": {key: list(shape) for key, shape in self.image_shapes.items()},
            "episodes": [{"split": split, "seed": seed} for split, seed in self.episodes],
        }

    def validate_observation(self, state: Any, images: Any) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Allow only measured proprioception and declared RGB cameras on the policy boundary."""
        state = finite_array(state, (len(self.channels),), "Measured state")
        require(isinstance(images, dict) and set(images) == set(self.image_shapes), "Camera set differs from contract")
        result = {}
        for key, shape in self.image_shapes.items():
            image = np.asarray(images[key])
            require(image.shape == shape and image.dtype == np.uint8, f"Camera {key} must be uint8 RGB {shape}")
            result[key] = np.array(image, order="C", copy=True)
        return state.copy(), result

    def decode_actions(self, actions: Any, raw_state: np.ndarray) -> np.ndarray:
        """Map backend output to absolute simulator targets, anchoring declared deltas once per chunk."""
        raw = np.asarray(actions)
        require(
            raw.ndim == 2 and raw.shape[0] == self.policy.action_horizon and 1 <= raw.shape[1] <= 64,
            "Unexpected policy action horizon or width",
        )
        raw_state = finite_array(raw_state, (len(self.channels),), "Measured action anchor")
        result = self.policy.action_mapping.apply(raw)
        require(result.shape == (self.policy.action_horizon, len(self.channels)), "Action channel count differs")
        if self.policy.delta_indices:
            indices = list(self.policy.delta_indices)
            result[:, indices] += raw_state[indices]
        require(bool(np.isfinite(result).all()), "Decoded action overflow")
        return result


def load_config(path: Path) -> EvaluationConfig:
    """Parse configuration on CPU; do not import model runtimes, execute plugins, or inspect weights."""
    path = path.expanduser().resolve(strict=True)
    source = read_json(path)
    _object(
        source, {"schema_version", "instruction", "channels", "image_shapes", "policy", "task", "evaluation"}, "config"
    )
    _integer(source["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION, "schema_version")
    channels = source["channels"]
    require(isinstance(channels, list) and 1 <= len(channels) <= 64, "Declare ordered joint channels")
    channels = tuple(_text(value, "channel") for value in channels)
    require(len(set(channels)) == len(channels), "Joint channels must be unique")
    shapes = source["image_shapes"]
    require(isinstance(shapes, dict) and 1 <= len(shapes) <= 8, "Declare one to eight cameras")
    images = {}
    for key, shape in shapes.items():
        require(re.fullmatch(r"[a-z][a-z0-9_]*", key) is not None, "Camera IDs must be safe identifiers")
        require(isinstance(shape, list) and len(shape) == 3, "Camera shape requires height, width, channels")
        images[key] = (
            _integer(shape[0], 2, 4096, "image height"),
            _integer(shape[1], 2, 4096, "image width"),
            _integer(shape[2], 3, 3, "RGB channels"),
        )
        require(shape[0] % 2 == shape[1] % 2 == 0, "Video dimensions must be even")
    require(sum(math.prod(shape) for shape in images.values()) < MAX_MESSAGE_BYTES // 2, "Camera payload exceeds limit")
    policy = _object(
        source["policy"],
        {
            "backend",
            "checkpoint",
            "checkpoint_sha256",
            "training_config",
            "state_key",
            "prompt_key",
            "image_keys",
            "state_mapping",
            "action_mapping",
            "delta_indices",
            "action_horizon",
        },
        "policy",
    )
    require(policy["backend"] in ("rho", "openpi"), "Supported policy backends: rho, openpi")
    require(
        isinstance(policy["checkpoint_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", policy["checkpoint_sha256"]) is not None,
        "Supply checkpoint tree SHA-256",
    )
    if policy["backend"] == "openpi":
        _text(policy["training_config"], "OpenPI registered training_config")
    else:
        require(policy["training_config"] is None, "Rho reads configuration from the checkpoint")
    image_keys = policy["image_keys"]
    require(isinstance(image_keys, dict) and set(image_keys) == set(images), "Map every camera to a model input key")
    keys = [*image_keys.values(), policy["state_key"], policy["prompt_key"]]
    keys = [_text(key, "model input key") for key in keys]
    require(len(set(keys)) == len(keys), "Model input keys must not alias")
    state_mapping = VectorMapping.parse(policy["state_mapping"], "state_mapping")
    action_mapping = VectorMapping.parse(policy["action_mapping"], "action_mapping")
    require(max(state_mapping.indices) < len(channels), "State mapping exceeds measured channels")
    require(len(action_mapping.indices) == len(channels), "Action mapping must cover every simulator channel")
    delta = policy["delta_indices"]
    require(isinstance(delta, list), "delta_indices must be an explicit list")
    delta = tuple(_integer(value, 0, len(channels) - 1, "delta index") for value in delta)
    require(len(set(delta)) == len(delta), "Duplicate delta indices")
    require(not delta or policy["backend"] != "rho", "Rho's checkpoint transform already restores absolute actions")
    horizon = _integer(policy["action_horizon"], 1, 1000, "action_horizon")
    task = _object(
        source["task"],
        {"adapter", "producer_root", "expert_config", "stage", "reference_root", "runtime_assets"},
        "task",
    )
    _text(task["adapter"], "task adapter")
    require(isinstance(task["runtime_assets"], list), "Explicitly list unpinned runtime materials")
    runtime_assets = tuple(_text(value, "runtime asset") for value in task["runtime_assets"])
    require(len(set(runtime_assets)) == len(runtime_assets), "Duplicate runtime assets")
    evaluation = _object(
        source["evaluation"],
        {
            "control_hz",
            "execution_horizon",
            "seeds",
            "max_steps",
            "timeout_seconds",
            "minimum_free_gib",
            "joint_limit_behavior",
        },
        "evaluation",
    )
    seeds = evaluation["seeds"]
    require(
        isinstance(seeds, dict) and bool(seeds) and set(seeds) <= {"train", "validation", "test", "unverified"},
        "Declare seed groups; split membership is caller supplied",
    )
    episodes = []
    for split, values in seeds.items():
        require(isinstance(values, list) and bool(values), "Seed groups must be nonempty lists")
        episodes.extend((split, _integer(value, 0, 2**32 - 1, "seed")) for value in values)
    require(
        len(episodes) <= 10000 and len({seed for _, seed in episodes}) == len(episodes),
        "Seeds overlap or exceed budget",
    )
    require(evaluation["joint_limit_behavior"] in {"reject", "clip"}, "Select joint_limit_behavior reject or clip")
    return EvaluationConfig(
        path,
        source,
        digest(source),
        PolicyConfig(
            policy["backend"],
            _path(policy["checkpoint"], path.parent, "checkpoint"),
            policy["checkpoint_sha256"],
            policy["training_config"],
            policy["state_key"],
            policy["prompt_key"],
            dict(image_keys),
            state_mapping,
            action_mapping,
            delta,
            horizon,
        ),
        TaskConfig(
            task["adapter"],
            *(
                _path(task[key], path.parent, key)
                for key in ("producer_root", "expert_config", "stage", "reference_root")
            ),
            runtime_assets,
        ),
        channels,
        images,
        _text(source["instruction"], "instruction"),
        _integer(evaluation["control_hz"], 1, 1000, "control_hz"),
        _integer(evaluation["execution_horizon"], 1, horizon, "execution_horizon"),
        tuple(episodes),
        _integer(evaluation["max_steps"], 1, 100000, "max_steps"),
        _number(evaluation["timeout_seconds"], "timeout_seconds", positive=True),
        _number(evaluation["minimum_free_gib"], "minimum_free_gib"),
        evaluation["joint_limit_behavior"],
    )
