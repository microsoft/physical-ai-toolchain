from __future__ import annotations

import pytest

from operator_worker.acquisition import AcquisitionError, ResourceTransaction


class FakeResource:
    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        fails: bool = False,
        release_fails: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.fails = fails
        self.release_fails = release_fails

    def acquire(self) -> None:
        self.events.append(f"acquire:{self.name}")
        if self.fails:
            raise RuntimeError(f"failed:{self.name}")

    def release(self) -> None:
        self.events.append(f"release:{self.name}")
        if self.release_fails:
            raise RuntimeError(f"release-failed:{self.name}")


@pytest.mark.parametrize("failure_index", range(4))
def test_failure_rolls_back_in_reverse_order(failure_index: int) -> None:
    events: list[str] = []
    resources = [
        FakeResource("wrist", events, fails=failure_index == 0),
        FakeResource("front", events, fails=failure_index == 1),
        FakeResource("leader", events, fails=failure_index == 2),
        FakeResource("follower", events, fails=failure_index == 3),
    ]

    with pytest.raises(AcquisitionError, match="failed"):
        ResourceTransaction(resources).acquire_all()

    acquired = [resource.name for resource in resources[: failure_index + 1]]
    assert [event for event in events if event.startswith("release:")] == [
        f"release:{name}" for name in reversed(acquired)
    ]


def test_success_releases_follower_first() -> None:
    events: list[str] = []
    resources = [
        FakeResource(name, events) for name in ("wrist", "front", "leader", "follower")
    ]
    transaction = ResourceTransaction(resources)

    transaction.acquire_all()
    report = transaction.release_all()

    assert [event for event in events if event.startswith("release:")] == [
        "release:follower",
        "release:leader",
        "release:front",
        "release:wrist",
    ]
    assert report.cleanup_complete is True


def test_acquisition_cleanup_failure_cannot_be_masked() -> None:
    events: list[str] = []
    transaction = ResourceTransaction(
        [FakeResource("follower", events, fails=True, release_fails=True)]
    )

    with pytest.raises(AcquisitionError, match="cleanup errors"):
        transaction.acquire_all()

    report = transaction.release_all()
    assert report.cleanup_complete is False
    assert report.errors == ("follower: release-failed:follower",)
