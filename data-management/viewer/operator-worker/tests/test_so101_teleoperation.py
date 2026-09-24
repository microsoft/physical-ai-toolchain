from __future__ import annotations

from threading import Event
from typing import ClassVar

import pytest

from operator_worker.teleoperate import JointTelemetry, LoopMetrics, TeleoperationError, TeleoperationLoop


class FakeLeader:
    action_features: ClassVar[dict[str, type[float]]] = {"joint.pos": float}

    def get_action(self) -> dict[str, float]:
        return {"joint.pos": 1.0}


class FakeFollower:
    def __init__(self) -> None:
        self.actions: list[dict[str, float]] = []

    def get_observation(self) -> dict[str, float]:
        return {"joint.pos": 0.0}

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self.actions.append(action)
        return action


def test_given_bounded_loop_when_run_then_rate_telemetry_and_explicit_stop_are_supported() -> None:
    stop_event = Event()
    metrics: list[LoopMetrics] = []
    telemetry: list[JointTelemetry] = []
    follower = FakeFollower()
    loop = TeleoperationLoop(
        FakeLeader(),
        follower,
        fps=2,
        max_duration_s=5.0,
        stop_event=stop_event,
        expected_action_keys={"joint.pos"},
        on_metrics=metrics.append,
        on_telemetry=telemetry.append,
        on_step=lambda *_args: stop_event.set() if len(follower.actions) >= 2 else None,
    )

    loop.run()

    assert len(follower.actions) == 2
    assert len(metrics) == 1
    assert telemetry[0].commanded == {"joint.pos": 1.0}


def test_given_nonfinite_action_when_run_then_no_motion_command_is_sent() -> None:
    class InvalidLeader:
        def get_action(self) -> dict[str, float]:
            return {"joint.pos": float("nan")}

    follower = FakeFollower()
    loop = TeleoperationLoop(
        InvalidLeader(),
        follower,
        fps=30,
        max_duration_s=1.0,
        expected_action_keys={"joint.pos"},
    )

    with pytest.raises(TeleoperationError, match="non-finite"):
        loop.run()

    assert follower.actions == []
