"""Policy runner that bridges RobotObservation → ACT policy → JointPositionCommand.

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

import numpy as np
import torch

from .robot_types import (
    NUM_JOINTS,
    JointPositionCommand,
    RobotObservation,
)


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


class PolicyRunner:
    """Stateful wrapper around a LeRobot ACT policy for real-time control.

    Handles model loading, observation preprocessing/normalization, action
    postprocessing/unnormalization, and the internal action-chunk queue.
    """

    def __init__(
        self,
        policy,
        preprocessor,
        postprocessor,
        device: str,
    ) -> None:
        self._policy = policy
        self._preprocessor = preprocessor
        self._postprocessor = postprocessor
        self._device = device
        self._metrics = InferenceMetrics()

    @classmethod
    def from_pretrained(
        cls,
        repo_id: str,
        device: str = "cuda",
    ) -> PolicyRunner:
        """Load a trained ACT policy and its normalization processors.

        Args:
            repo_id: HuggingFace repo ID or local path containing
                ``config.json``, ``model.safetensors``, and processor JSONs.
            device: Target device (``cuda``, ``cpu``, ``mps``).
        """
        from lerobot.policies.act.modeling_act import ACTPolicy
        from lerobot.processor.pipeline import PolicyProcessorPipeline

        device = _resolve_device(device)

        policy = ACTPolicy.from_pretrained(repo_id)
        policy.to(device)

        device_override = {"device_processor": {"device": device}}
        preprocessor = PolicyProcessorPipeline.from_pretrained(
            repo_id,
            "policy_preprocessor.json",
            overrides=device_override,
        )
        postprocessor = PolicyProcessorPipeline.from_pretrained(
            repo_id,
            "policy_postprocessor.json",
            overrides=device_override,
        )

        return cls(policy, preprocessor, postprocessor, device)

    def reset(self) -> None:
        """Call at the start of each episode to clear the action queue."""
        self._policy.reset()
        self._metrics = InferenceMetrics()

    def step(self, obs: RobotObservation) -> JointPositionCommand:
        """Run one inference step and return a joint position command.

        The ACT policy internally manages action chunking: it predicts
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

        # Build observation tensors (unbatched; preprocessor adds batch dim)
        t0 = time.monotonic()
        obs_dict = {
            "observation.state": torch.from_numpy(obs.joint_positions.astype(np.float32)),
            "observation.images.color": (torch.from_numpy(obs.color_image.astype(np.float32)).permute(2, 0, 1) / 255.0),
        }
        obs_dict = self._preprocessor(obs_dict)
        t1 = time.monotonic()

        with torch.inference_mode():
            action = self._policy.select_action(obs_dict)
        t2 = time.monotonic()

        action = self._postprocessor({"action": action})
        action_np = action["action"].squeeze(0).cpu().numpy()

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
