from __future__ import annotations

import atexit
import functools
import logging
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_REPORT_EVERY = 100
_REGISTRY: _TimingRegistry | None = None


class _TimingRegistry:
    def __init__(self, report_every: int) -> None:
        self._report_every = report_every
        self._durations: dict[str, list[float]] = defaultdict(list)
        self._control_iterations = 0
        self._reported_iterations = 0
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._active = False
        self._lock = threading.Lock()

    def start_control_window(self) -> None:
        with self._lock:
            self._durations.clear()
            self._control_iterations = 0
            self._reported_iterations = 0
            self._started_at = time.perf_counter()
            self._finished_at = None
            self._active = True

    def finish_control_window(self) -> None:
        with self._lock:
            self._finished_at = time.perf_counter()
            self._active = False
        self.report()

    def record(self, stage: str, duration_s: float) -> None:
        with self._lock:
            if not self._active:
                return
            self._durations[stage].append(duration_s)

    def complete_control_iteration(self) -> None:
        should_report = False
        with self._lock:
            if not self._active:
                return
            self._control_iterations += 1
            should_report = self._control_iterations % self._report_every == 0
        if should_report:
            self.report()

    def report(self) -> None:
        with self._lock:
            if self._control_iterations == self._reported_iterations:
                return
            control_iterations = self._control_iterations
            if self._started_at is None:
                return
            finished_at = self._finished_at or time.perf_counter()
            elapsed_s = finished_at - self._started_at
            durations = {stage: values.copy() for stage, values in self._durations.items()}
            self._reported_iterations = control_iterations

        achieved_hz = control_iterations / elapsed_s if elapsed_s > 0 else 0.0
        logger.info(
            "ROLLOUT_TIMING iterations=%d elapsed_s=%.3f achieved_hz=%.2f",
            control_iterations,
            elapsed_s,
            achieved_hz,
        )
        for stage in sorted(durations):
            values_ms = sorted(duration * 1000 for duration in durations[stage])
            logger.info(
                "ROLLOUT_TIMING stage=%s calls=%d mean_ms=%.3f p50_ms=%.3f p95_ms=%.3f max_ms=%.3f",
                stage,
                len(values_ms),
                sum(values_ms) / len(values_ms),
                _percentile(values_ms, 0.50),
                _percentile(values_ms, 0.95),
                values_ms[-1],
            )


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = (len(sorted_values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = index - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _timed(registry: _TimingRegistry, stage: str, function: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            registry.record(stage, time.perf_counter() - started_at)

    return wrapper


def _wrap_method(registry: _TimingRegistry, cls: type, method_name: str, stage: str) -> None:
    original = getattr(cls, method_name)
    if getattr(original, "_rollout_timing_wrapped", False):
        return
    wrapped = _timed(registry, stage, original)
    wrapped._rollout_timing_wrapped = True
    setattr(cls, method_name, wrapped)


def _parse_report_every() -> int:
    value = os.environ.get("ROLLOUT_TIMING_REPORT_EVERY", str(_DEFAULT_REPORT_EVERY))
    try:
        report_every = int(value)
    except ValueError as error:
        raise ValueError("ROLLOUT_TIMING_REPORT_EVERY must be an integer") from error
    if report_every <= 0:
        raise ValueError("ROLLOUT_TIMING_REPORT_EVERY must be positive")
    return report_every


def _install_profiler() -> _TimingRegistry:
    global _REGISTRY
    if _REGISTRY is not None:
        return _REGISTRY

    from lerobot.cameras.opencv.camera_opencv import OpenCVCamera
    from lerobot.motors.motors_bus import SerialMotorsBus
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.rollout.inference import sync as sync_inference
    from lerobot.rollout.robot_wrapper import ThreadSafeRobot
    from lerobot.rollout.strategies import base as base_strategy
    from lerobot.rollout.strategies import core as strategy_core
    from raw_observation_inference import RawObservationSyncInferenceEngine

    registry = _TimingRegistry(_parse_report_every())

    _wrap_method(registry, SerialMotorsBus, "sync_read", "serial_read")
    _wrap_method(registry, SerialMotorsBus, "sync_write", "serial_write")
    _wrap_method(registry, OpenCVCamera, "read_latest", "camera_read_latest")
    _wrap_method(registry, ThreadSafeRobot, "get_observation", "robot_observation")
    _wrap_method(registry, ThreadSafeRobot, "send_action", "robot_send_action")
    if os.environ.get("ROLLOUT_RAW_OBSERVATION_OFFLOAD", "false").lower() == "true":
        _wrap_method(registry, RawObservationSyncInferenceEngine, "get_action", "inference_total")
    else:
        _wrap_method(registry, sync_inference.SyncInferenceEngine, "get_action", "inference_total")
        _wrap_method(registry, ACTPolicy, "select_action", "policy_select_action_rpc")
        sync_inference.prepare_observation_for_inference = _timed(
            registry,
            "prepare_observation",
            sync_inference.prepare_observation_for_inference,
        )
    strategy_core.build_dataset_frame = _timed(
        registry,
        "build_dataset_frame",
        strategy_core.build_dataset_frame,
    )
    base_strategy.RolloutStrategy._process_observation_and_notify = _timed(
        registry,
        "observation_processor",
        base_strategy.RolloutStrategy._process_observation_and_notify,
    )

    original_send_next_action = strategy_core.send_next_action
    timed_send_next_action = _timed(registry, "action_dispatch", original_send_next_action)

    @functools.wraps(timed_send_next_action)
    def send_next_action(*args: Any, **kwargs: Any) -> Any:
        try:
            return timed_send_next_action(*args, **kwargs)
        finally:
            registry.complete_control_iteration()

    strategy_core.send_next_action = send_next_action
    base_strategy.send_next_action = send_next_action

    original_run = base_strategy.BaseStrategy.run

    @functools.wraps(original_run)
    def run(*args: Any, **kwargs: Any) -> Any:
        registry.start_control_window()
        try:
            return original_run(*args, **kwargs)
        finally:
            registry.finish_control_window()

    base_strategy.BaseStrategy.run = run
    atexit.register(registry.report)
    _REGISTRY = registry
    return registry


def main() -> None:
    from raw_observation_inference import install_raw_observation_offload

    install_raw_observation_offload()
    _install_profiler()

    from lerobot.scripts.lerobot_rollout import main as rollout_main

    rollout_main()


if __name__ == "__main__":
    main()
