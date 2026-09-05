"""Deadline-based SO-101 teleoperation loop."""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any, Protocol


class Leader(Protocol):
    def get_action(self) -> dict[str, float]: ...


class Follower(Protocol):
    def get_observation(self) -> dict[str, float]: ...

    def send_action(self, action: dict[str, float]) -> dict[str, float]: ...


class TeleoperationError(RuntimeError):
    """Raised when a control iteration cannot safely complete."""


@dataclass(frozen=True)
class LoopMetrics:
    target_hz: float
    actual_hz: float
    loop_p50_ms: float
    loop_p95_ms: float
    loop_max_ms: float
    overruns: int


@dataclass(frozen=True)
class JointTelemetry:
    elapsed_s: float
    leader: dict[str, float]
    follower: dict[str, float]
    commanded: dict[str, float]


class TeleoperationLoop:
    """Read fresh leader actions and send each action at most once."""

    def __init__(
        self,
        leader: Leader,
        follower: Follower,
        *,
        fps: int,
        stop_event: Event | None = None,
        expected_action_keys: set[str] | None = None,
        observation_processor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        teleop_action_processor: (Callable[[tuple[dict[str, float], dict[str, Any]]], dict[str, float]] | None) = None,
        robot_action_processor: (Callable[[tuple[dict[str, float], dict[str, Any]]], dict[str, float]] | None) = None,
        clock: Callable[[], float] = time.perf_counter,
        max_action_age_s: float | None = None,
        max_consecutive_overruns: int = 3,
        on_metrics: Callable[[LoopMetrics], None] | None = None,
        telemetry_hz: int = 10,
        on_telemetry: Callable[[JointTelemetry], None] | None = None,
        extra_observation: Callable[[], dict[str, Any]] | None = None,
        on_step: (Callable[[dict[str, Any], dict[str, float], dict[str, float]], None] | None) = None,
    ) -> None:
        if fps <= 0:
            raise ValueError("fps must be greater than zero")
        self.leader = leader
        self.follower = follower
        self.fps = fps
        self.stop_event = stop_event or Event()
        self.expected_action_keys = expected_action_keys
        self.observation_processor = observation_processor or (lambda value: value)
        self.teleop_action_processor = teleop_action_processor or (lambda value: value[0])
        self.robot_action_processor = robot_action_processor or (lambda value: value[0])
        self.clock = clock
        self.max_action_age_s = max_action_age_s or (1.0 / fps)
        self.max_consecutive_overruns = max_consecutive_overruns
        self.on_metrics = on_metrics
        self.telemetry_interval = max(1, fps // max(1, telemetry_hz))
        self.on_telemetry = on_telemetry
        self.extra_observation = extra_observation
        self.on_step = on_step

    def run(self, *, max_iterations: int | None = None) -> None:
        period = 1.0 / self.fps
        deadline = self.clock()
        iterations = 0
        consecutive_overruns = 0
        total_overruns = 0
        session_start = self.clock()
        window_start = session_start
        durations: deque[float] = deque(maxlen=self.fps)
        while not self.stop_event.is_set():
            if max_iterations is not None and iterations >= max_iterations:
                return
            try:
                iteration_start = self.clock()
                raw_observation = self.follower.get_observation()
                if self.extra_observation is not None:
                    raw_observation.update(self.extra_observation())
                observation = self.observation_processor(raw_observation)
                action_started = self.clock()
                raw_action = self.leader.get_action()
                teleop_action = self.teleop_action_processor((raw_action, observation))
                action = self.robot_action_processor((teleop_action, observation))
                if self.clock() - action_started > self.max_action_age_s:
                    raise TeleoperationError("Leader action became stale before send")
                self._validate_action(action)
                sent_action = self.follower.send_action(action)
                if self.on_step is not None:
                    self.on_step(observation, raw_action, sent_action)
                if self.on_telemetry is not None and iterations % self.telemetry_interval == 0:
                    self.on_telemetry(
                        JointTelemetry(
                            elapsed_s=self.clock() - session_start,
                            leader={key: float(value) for key, value in raw_action.items()},
                            follower={
                                key: float(value)
                                for key, value in observation.items()
                                if isinstance(value, (int, float))
                            },
                            commanded={key: float(value) for key, value in sent_action.items()},
                        )
                    )
            except Exception as error:
                self.stop_event.set()
                if isinstance(error, TeleoperationError):
                    raise
                raise TeleoperationError(str(error)) from error
            iterations += 1
            duration = self.clock() - iteration_start
            durations.append(duration)
            if duration > period:
                consecutive_overruns += 1
                total_overruns += 1
                if consecutive_overruns >= self.max_consecutive_overruns:
                    self.stop_event.set()
                    raise TeleoperationError("Teleoperation loop overrun limit exceeded")
            else:
                consecutive_overruns = 0
            if self.on_metrics is not None and len(durations) == self.fps:
                elapsed = max(self.clock() - window_start, 1e-9)
                ordered = sorted(durations)
                percentile_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
                self.on_metrics(
                    LoopMetrics(
                        target_hz=float(self.fps),
                        actual_hz=len(durations) / elapsed,
                        loop_p50_ms=statistics.median(durations) * 1_000,
                        loop_p95_ms=ordered[percentile_index] * 1_000,
                        loop_max_ms=max(durations) * 1_000,
                        overruns=total_overruns,
                    )
                )
                durations.clear()
                total_overruns = 0
                window_start = self.clock()
            deadline += period
            remaining = deadline - self.clock()
            if remaining > 0:
                self.stop_event.wait(remaining)

    def _validate_action(self, action: dict[str, float]) -> None:
        if self.expected_action_keys is not None and set(action) != self.expected_action_keys:
            raise TeleoperationError("Leader action does not contain the complete expected joint set")
        if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in action.values()):
            raise TeleoperationError("Leader action contains a non-finite value")
