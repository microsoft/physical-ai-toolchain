"""Policy runner that bridges RobotObservation to a LeRobot policy and command.

Framework-agnostic: works with ROS2, RTDE, or any source that can populate
a :class:`RobotObservation` and consume a :class:`JointPositionCommand`.

Usage::

    runner = PolicyRunner.from_pretrained("alizaidi/hve-robo-act-train", device="cuda")
    runner.reset()
    cmd = runner.step(observation)
    # send cmd.positions to the robot
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch

from .hf_revision import resolve_hf_revision
from .robot_types import (
    NUM_JOINTS,
    JointPositionCommand,
    RobotObservation,
)

SUPPORTED_POLICY_TYPES = frozenset({"act", "diffusion", "pi0", "pi0_fast"})
VLA_POLICY_TYPES = frozenset({"pi0", "pi0_fast"})


def _resolve_device(requested: str) -> str:
    if requested == "cuda" and torch.cuda.is_available():
        return "cuda"
    if requested in ("cuda", "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@dataclass
class InferenceMetrics:
    """Lightweight counters for a single episode."""

    steps: int = 0
    total_inference_s: float = 0.0
    total_preprocess_s: float = 0.0
    chunk_queries: int = 0

    @property
    def avg_inference_ms(self) -> float:
        return (self.total_inference_s / max(self.steps, 1)) * 1000

    @property
    def avg_preprocess_ms(self) -> float:
        return (self.total_preprocess_s / max(self.steps, 1)) * 1000


@dataclass(frozen=True)
class PolicyBundle:
    """Loaded policy and its persisted preprocessing pipelines."""

    policy: Any
    preprocessor: Any
    postprocessor: Any
    device: str
    policy_type: str


def load_pretrained_policy(
    repo_id: str,
    policy_type: str = "act",
    device: str = "cuda",
    revision: str | None = None,
) -> PolicyBundle:
    """Load a supported LeRobot policy and its persisted processors."""
    normalized_policy_type = policy_type.strip().lower()
    if normalized_policy_type not in SUPPORTED_POLICY_TYPES:
        supported = ", ".join(sorted(SUPPORTED_POLICY_TYPES))
        raise ValueError(f"Unsupported policy type: {policy_type} (use: {supported})")

    from lerobot.processor.pipeline import PolicyProcessorPipeline

    resolved_device = _resolve_device(device)
    revision = resolve_hf_revision(repo_id, revision, revision_name="revision")

    if normalized_policy_type == "act":
        from lerobot.policies.act.modeling_act import ACTPolicy

        policy_class = ACTPolicy
    else:
        from lerobot.policies.factory import get_policy_class

        policy_class = get_policy_class(normalized_policy_type)

    policy = policy_class.from_pretrained(repo_id, revision=revision)
    policy.to(resolved_device)

    device_override = {"device_processor": {"device": resolved_device}}
    if normalized_policy_type in VLA_POLICY_TYPES:
        from lerobot.policies.factory import make_pre_post_processors

        preprocessor, postprocessor = make_pre_post_processors(
            policy.config,
            pretrained_path=repo_id,
            pretrained_revision=revision,
            preprocessor_overrides=device_override,
            postprocessor_overrides=device_override,
        )
    else:
        preprocessor = PolicyProcessorPipeline.from_pretrained(
            repo_id,
            "policy_preprocessor.json",
            revision=revision,
            overrides=device_override,
        )
        postprocessor = PolicyProcessorPipeline.from_pretrained(
            repo_id,
            "policy_postprocessor.json",
            revision=revision,
            overrides=device_override,
        )

    return PolicyBundle(
        policy=policy,
        preprocessor=preprocessor,
        postprocessor=postprocessor,
        device=resolved_device,
        policy_type=normalized_policy_type,
    )


def postprocess_action(postprocessor: Any, action: torch.Tensor, policy_type: str) -> torch.Tensor:
    """Apply the processor input/output convention used by a policy family."""
    if policy_type in VLA_POLICY_TYPES:
        return postprocessor(action)
    return postprocessor({"action": action})["action"]


class PolicyRunner:
    """Stateful wrapper around a LeRobot policy for real-time control.

    Handles model loading, observation preprocessing/normalization, action
    postprocessing/unnormalization, and the internal action-chunk queue.
    """

    def __init__(
        self,
        policy: Any,
        preprocessor: Any,
        postprocessor: Any,
        device: str,
        policy_type: str = "act",
    ) -> None:
        self._policy = policy
        self._preprocessor = preprocessor
        self._postprocessor = postprocessor
        self._device = device
        self._policy_type = policy_type
        self._metrics = InferenceMetrics()

    @classmethod
    def from_pretrained(
        cls,
        repo_id: str,
        policy_type: str = "act",
        device: str = "cuda",
        revision: str | None = None,
    ) -> PolicyRunner:
        """Load a trained policy and its normalization processors.

        Args:
            repo_id: HuggingFace repo ID or local path containing
                ``config.json``, ``model.safetensors``, and processor JSONs.
            policy_type: LeRobot policy architecture.
            device: Target device (``cuda``, ``cpu``, ``mps``).
            revision: Immutable 40-hex commit SHA to pin the
                download. Ignored for local paths; a 40-hex SHA is required for
                remote repositories to prevent an upstream repo from silently shipping new
                weights.
        """
        bundle = load_pretrained_policy(
            repo_id=repo_id,
            policy_type=policy_type,
            device=device,
            revision=revision,
        )
        return cls(
            bundle.policy,
            bundle.preprocessor,
            bundle.postprocessor,
            bundle.device,
            bundle.policy_type,
        )

    def reset(self) -> None:
        """Call at the start of each episode to clear the action queue."""
        self._policy.reset()
        self._metrics = InferenceMetrics()

    def step(self, obs: RobotObservation) -> JointPositionCommand:
        """Run one inference step and return a joint position command.

        The policy internally manages action chunking: it predicts
        ``chunk_size`` future actions on the first call, then pops from
        the queue on subsequent calls until the queue is empty.

        Args:
            obs: Current robot observation with joint state and camera image.

        Returns:
            Joint position deltas to apply.
        """
        if obs.color_image is None:
            return JointPositionCommand(
                positions=np.zeros(NUM_JOINTS, dtype=np.float32),
                timestamp_s=obs.timestamp_s,
            )

        if self._policy_type in VLA_POLICY_TYPES and not obs.task.strip():
            raise ValueError(f"{self._policy_type} observations require a non-empty task description")

        t0 = time.monotonic()
        obs_dict: dict[str, Any] = {
            "observation.state": torch.from_numpy(obs.joint_positions.astype(np.float32)),
            "observation.images.color": (torch.from_numpy(obs.color_image.astype(np.float32)).permute(2, 0, 1) / 255.0),
        }
        if self._policy_type in VLA_POLICY_TYPES:
            obs_dict["task"] = obs.task
        obs_dict = self._preprocessor(obs_dict)
        t1 = time.monotonic()

        with torch.inference_mode():
            action = self._policy.select_action(obs_dict)
        t2 = time.monotonic()

        action = postprocess_action(self._postprocessor, action, self._policy_type)
        action_np = action.squeeze(0).cpu().numpy()

        self._metrics.steps += 1
        self._metrics.total_preprocess_s += t1 - t0
        self._metrics.total_inference_s += t2 - t1

        return JointPositionCommand(
            positions=action_np,
            timestamp_s=obs.timestamp_s,
        )

    @property
    def metrics(self) -> InferenceMetrics:
        return self._metrics

    @property
    def device(self) -> str:
        return self._device
