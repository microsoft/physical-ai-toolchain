from __future__ import annotations

from threading import Event
from typing import Any, ClassVar

import pytest

from operator_worker.policy_control import PolicyControlError, PolicyControlLoop


class FakeFollower:
    action_features: ClassVar[dict[str, type[float]]] = {"joint.pos": float}

    def __init__(self) -> None:
        self.observation = {"joint.pos": 0.0}
        self.sent: list[dict[str, float]] = []

    def get_observation(self) -> dict[str, float]:
        return dict(self.observation)

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self.sent.append(action)
        self.observation.update(action)
        return action


class FakePolicy:
    def __init__(self, chunk: list[list[float]]) -> None:
        self.chunk = chunk

    def predict(self, observation: dict[str, Any], task: str) -> list[list[float]]:
        return self.chunk

    def close(self) -> None:
        return None


def test_given_policy_delta_limit_when_run_then_actions_are_clamped_and_stoppable() -> None:
    follower = FakeFollower()
    stop_event = Event()
    loop = PolicyControlLoop(
        follower,
        FakePolicy([[10.0], [10.0]]),
        task="Move safely",
        fps=30,
        max_duration_s=5.0,
        max_relative_target=2.0,
        stop_event=stop_event,
        read_cameras=lambda: {"wrist": object(), "front": object()},
        on_step=lambda *_args: stop_event.set(),
    )

    loop.run()

    assert follower.sent == [{"joint.pos": 2.0}]


def test_given_invalid_policy_chunk_when_run_then_no_action_is_sent() -> None:
    follower = FakeFollower()
    loop = PolicyControlLoop(
        follower,
        FakePolicy([[float("nan")]]),
        task="Move safely",
        fps=30,
        max_duration_s=1.0,
        max_relative_target=2.0,
        read_cameras=lambda: {"wrist": object(), "front": object()},
    )

    with pytest.raises(PolicyControlError, match="finite"):
        loop.run()

    assert follower.sent == []
