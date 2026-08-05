from __future__ import annotations

import pytest

from operator_worker.teleoperate import TeleoperationError, TeleoperationLoop


class FakeLeader:
    def __init__(self) -> None:
        self.calls = 0

    def get_action(self) -> dict[str, float]:
        self.calls += 1
        if self.calls > 2:
            raise RuntimeError("serial failure")
        return {"shoulder_pan.pos": float(self.calls)}


class FakeFollower:
    def __init__(self) -> None:
        self.actions: list[dict[str, float]] = []

    def get_observation(self) -> dict[str, float]:
        return {"shoulder_pan.pos": 0.0}

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self.actions.append(action)
        return action


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_serial_failure_stops_without_reusing_stale_action() -> None:
    follower = FakeFollower()
    loop = TeleoperationLoop(FakeLeader(), follower, fps=30)

    with pytest.raises(TeleoperationError, match="serial failure"):
        loop.run(max_iterations=10)

    assert follower.actions == [
        {"shoulder_pan.pos": 1.0},
        {"shoulder_pan.pos": 2.0},
    ]


@pytest.mark.parametrize("action", [{}, {"shoulder_pan.pos": float("nan")}])
def test_incomplete_or_nonfinite_action_fails_before_send(
    action: dict[str, float],
) -> None:
    class Leader:
        def get_action(self) -> dict[str, float]:
            return action

    follower = FakeFollower()
    loop = TeleoperationLoop(
        Leader(),
        follower,
        fps=30,
        expected_action_keys={"shoulder_pan.pos"},
    )

    with pytest.raises(TeleoperationError, match="action"):
        loop.run(max_iterations=1)

    assert follower.actions == []


def test_processors_run_before_follower_send() -> None:
    events: list[str] = []

    def observation_processor(observation):
        events.append("observation")
        return observation

    def teleop_processor(value):
        events.append("teleop")
        return value[0]

    def robot_processor(value):
        events.append("robot")
        return {"shoulder_pan.pos": value[0]["shoulder_pan.pos"] + 1}

    follower = FakeFollower()
    loop = TeleoperationLoop(
        FakeLeader(),
        follower,
        fps=30,
        expected_action_keys={"shoulder_pan.pos"},
        observation_processor=observation_processor,
        teleop_action_processor=teleop_processor,
        robot_action_processor=robot_processor,
    )

    loop.run(max_iterations=1)

    assert events == ["observation", "teleop", "robot"]
    assert follower.actions == [{"shoulder_pan.pos": 2.0}]


def test_stale_action_fails_before_send() -> None:
    clock = FakeClock()

    class SlowLeader:
        def get_action(self) -> dict[str, float]:
            clock.value += 0.1
            return {"shoulder_pan.pos": 1.0}

    follower = FakeFollower()
    loop = TeleoperationLoop(
        SlowLeader(),
        follower,
        fps=30,
        expected_action_keys={"shoulder_pan.pos"},
        clock=clock,
    )

    with pytest.raises(TeleoperationError, match="stale"):
        loop.run(max_iterations=1)

    assert follower.actions == []


def test_repeated_loop_overruns_fail_stop() -> None:
    clock = FakeClock()

    class SlowFollower(FakeFollower):
        def send_action(self, action: dict[str, float]) -> dict[str, float]:
            result = super().send_action(action)
            clock.value += 0.05
            return result

    loop = TeleoperationLoop(
        FakeLeader(),
        SlowFollower(),
        fps=30,
        expected_action_keys={"shoulder_pan.pos"},
        clock=clock,
        max_consecutive_overruns=2,
    )

    with pytest.raises(TeleoperationError, match="overrun"):
        loop.run(max_iterations=10)


def test_emits_bounded_rate_metrics() -> None:
    metrics = []
    loop = TeleoperationLoop(
        FakeLeader(),
        FakeFollower(),
        fps=2,
        expected_action_keys={"shoulder_pan.pos"},
        on_metrics=metrics.append,
    )

    loop.run(max_iterations=2)

    assert len(metrics) == 1
    assert metrics[0].target_hz == 2
    assert metrics[0].actual_hz > 0
    assert metrics[0].loop_p95_ms >= 0


def test_emits_bounded_joint_telemetry() -> None:
    telemetry = []
    follower = FakeFollower()
    loop = TeleoperationLoop(
        FakeLeader(),
        follower,
        fps=30,
        expected_action_keys={"shoulder_pan.pos"},
        telemetry_hz=10,
        on_telemetry=telemetry.append,
    )

    loop.run(max_iterations=2)

    assert len(telemetry) == 1
    assert telemetry[0].leader == {"shoulder_pan.pos": 1.0}
    assert telemetry[0].follower == {"shoulder_pan.pos": 0.0}
    assert telemetry[0].commanded == {"shoulder_pan.pos": 1.0}
