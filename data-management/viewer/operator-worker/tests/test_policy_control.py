from __future__ import annotations

from threading import Event
from typing import ClassVar

import pytest

from operator_worker.policy_control import PolicyControlError, PolicyControlLoop


class FakeFollower:
    action_features: ClassVar[dict[str, type[float]]] = {
        "shoulder_pan.pos": float,
        "shoulder_lift.pos": float,
        "elbow_flex.pos": float,
        "wrist_flex.pos": float,
        "wrist_roll.pos": float,
        "gripper.pos": float,
    }

    def __init__(self) -> None:
        self.observation = {key: 0.0 for key in self.action_features}
        self.sent: list[dict[str, float]] = []

    def get_observation(self) -> dict[str, float]:
        return dict(self.observation)

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self.sent.append(dict(action))
        self.observation.update(action)
        return action


class FakePolicy:
    def __init__(self, chunks: list[list[list[float]]]) -> None:
        self.chunks = chunks
        self.prompts: list[str] = []

    def predict(self, observation, task: str) -> list[list[float]]:
        self.prompts.append(task)
        return self.chunks.pop(0)

    def close(self) -> None:
        pass


def test_policy_loop_clamps_commands_and_stops_at_duration() -> None:
    follower = FakeFollower()
    policy = FakePolicy([[[5.0, -5.0, 1.0, -1.0, 0.5, 10.0]]])
    clock_values = iter([0.0, 0.0, 0.1, 0.1])

    loop = PolicyControlLoop(
        follower,
        policy,
        task="Pick up the rubber puck on the right bin and place on the front",
        fps=10,
        max_duration_s=0.05,
        max_relative_target=2.0,
        stop_event=Event(),
        read_cameras=lambda: {"wrist": object(), "front": object()},
        clock=lambda: next(clock_values),
    )

    loop.run()

    assert policy.prompts == [
        "Pick up the rubber puck on the right bin and place on the front"
    ]
    assert follower.sent == [
        {
            "shoulder_pan.pos": 2.0,
            "shoulder_lift.pos": -2.0,
            "elbow_flex.pos": 1.0,
            "wrist_flex.pos": -1.0,
            "wrist_roll.pos": 0.5,
            "gripper.pos": 2.0,
        }
    ]


def test_policy_loop_rejects_non_finite_action_before_send() -> None:
    follower = FakeFollower()
    policy = FakePolicy([[[0.0, 0.0, float("nan"), 0.0, 0.0, 0.0]]])
    loop = PolicyControlLoop(
        follower,
        policy,
        task="Pick up the puck",
        fps=10,
        max_duration_s=1.0,
        max_relative_target=2.0,
        read_cameras=lambda: {"wrist": object(), "front": object()},
    )

    with pytest.raises(PolicyControlError, match="finite"):
        loop.run(max_iterations=1)

    assert follower.sent == []
