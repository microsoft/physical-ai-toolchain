"""Bounded SO-101 policy rollout control."""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from collections.abc import Callable
from threading import Event
from typing import Any, Protocol

from .teleoperate import JointTelemetry, LoopMetrics


class Follower(Protocol):
    action_features: dict[str, Any]

    def get_observation(self) -> dict[str, float]: ...

    def send_action(self, action: dict[str, float]) -> dict[str, float]: ...


class Policy(Protocol):
    def predict(self, observation: dict[str, Any], task: str) -> list[list[float]]: ...

    def close(self) -> None: ...


class PolicyControlError(RuntimeError):
    """Raised when a policy rollout cannot safely continue."""


class PolicyControlLoop:
    """Execute bounded action chunks against fresh observations."""

    def __init__(
        self,
        follower: Follower,
        policy: Policy,
        *,
        task: str,
        fps: int,
        max_duration_s: float,
        max_relative_target: float,
        stop_event: Event | None = None,
        read_cameras: Callable[[], dict[str, Any]],
        clock: Callable[[], float] = time.perf_counter,
        on_metrics: Callable[[LoopMetrics], None] | None = None,
        on_telemetry: Callable[[JointTelemetry], None] | None = None,
        on_step: Callable[[dict[str, float]], None] | None = None,
    ) -> None:
        if fps <= 0 or max_duration_s <= 0 or max_relative_target <= 0:
            raise ValueError("fps, max_duration_s, and max_relative_target must be greater than zero")
        self.follower = follower
        self.policy = policy
        self.task = task
        self.fps = fps
        self.max_duration_s = max_duration_s
        self.max_relative_target = max_relative_target
        self.stop_event = stop_event or Event()
        self.read_cameras = read_cameras
        self.clock = clock
        self.on_metrics = on_metrics
        self.on_telemetry = on_telemetry
        self.on_step = on_step

    def run(self, *, max_iterations: int | None = None) -> None:
        period = 1.0 / self.fps
        started_at = self.clock()
        deadline = started_at
        window_start = started_at
        iterations = 0
        overruns = 0
        durations: deque[float] = deque(maxlen=self.fps)
        chunk: list[list[float]] = []
        chunk_index = 0
        action_keys = list(self.follower.action_features)
        try:
            while not self.stop_event.is_set():
                if self.clock() - started_at >= self.max_duration_s:
                    return
                if max_iterations is not None and iterations >= max_iterations:
                    return
                iteration_start = self.clock()
                observation = self.follower.get_observation()
                if chunk_index >= len(chunk):
                    chunk = self.policy.predict(
                        {
                            "state": [float(observation[key]) for key in action_keys],
                            "images": self.read_cameras(),
                        },
                        self.task,
                    )
                    self._validate_chunk(chunk, len(action_keys))
                    chunk_index = 0
                predicted = chunk[chunk_index]
                chunk_index += 1
                action = {
                    key: self._clamp(float(observation[key]), float(predicted[index]), self.max_relative_target)
                    for index, key in enumerate(action_keys)
                }
                sent_action = self.follower.send_action(action)
                if self.on_step is not None:
                    self.on_step(sent_action)
                if self.on_telemetry is not None:
                    self.on_telemetry(
                        JointTelemetry(
                            elapsed_s=self.clock() - started_at,
                            leader={},
                            follower={key: float(value) for key, value in observation.items()},
                            commanded={key: float(value) for key, value in sent_action.items()},
                        )
                    )
                iterations += 1
                duration = self.clock() - iteration_start
                durations.append(duration)
                if duration > period:
                    overruns += 1
                if self.on_metrics is not None and len(durations) == self.fps:
                    ordered = sorted(durations)
                    percentile_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
                    self.on_metrics(
                        LoopMetrics(
                            target_hz=float(self.fps),
                            actual_hz=len(durations) / max(self.clock() - window_start, 1e-9),
                            loop_p50_ms=statistics.median(durations) * 1_000,
                            loop_p95_ms=ordered[percentile_index] * 1_000,
                            loop_max_ms=max(durations) * 1_000,
                            overruns=overruns,
                        )
                    )
                    durations.clear()
                    overruns = 0
                    window_start = self.clock()
                deadline += period
                remaining = deadline - self.clock()
                if remaining > 0:
                    self.stop_event.wait(remaining)
        except Exception as error:
            self.stop_event.set()
            if isinstance(error, PolicyControlError):
                raise
            raise PolicyControlError(str(error)) from error

    @staticmethod
    def _validate_chunk(chunk: list[list[float]], action_dim: int) -> None:
        if not chunk:
            raise PolicyControlError("Policy returned an empty action chunk")
        if any(len(action) != action_dim for action in chunk):
            raise PolicyControlError("Policy action chunk has an invalid shape")
        if not all(math.isfinite(float(value)) for action in chunk for value in action):
            raise PolicyControlError("Policy action chunk contains a non-finite value")

    @staticmethod
    def _clamp(current: float, target: float, maximum_delta: float) -> float:
        return max(current - maximum_delta, min(current + maximum_delta, target))
