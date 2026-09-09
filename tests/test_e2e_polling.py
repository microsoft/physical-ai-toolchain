from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.e2e import _common
from tests.e2e._aml import AML_STARTED_STATES


class _FakeClock:
    def __init__(self) -> None:
        self.current = 0.0

    def time(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


def _raise_on_monotonic() -> float:
    raise AssertionError("wait_for_status must use a suspend-aware wall clock")


def _wait_for_never_completed(
    monkeypatch: pytest.MonkeyPatch,
    clock: _FakeClock,
    *,
    timeout_minutes: int,
    poll_interval_seconds: int,
    log: Callable[[str], None] | None = None,
) -> None:
    monkeypatch.setattr(_common.time, "time", clock.time)
    monkeypatch.setattr(_common.time, "sleep", clock.sleep)
    monkeypatch.setattr(_common.time, "monotonic", _raise_on_monotonic)
    if log is not None:
        monkeypatch.setattr(_common, "log_e2e", log)

    with pytest.raises(AssertionError, match="Timed out waiting for test job; last status was 'Queued'"):
        _common.wait_for_status(
            lambda: "Queued",
            goal_description="test job",
            timeout_minutes=timeout_minutes,
            poll_interval_seconds=poll_interval_seconds,
            success_statuses={"Completed"},
        )


def test_aml_started_states_require_execution() -> None:
    assert {"Running", "Finalizing", "Completed"} == AML_STARTED_STATES


def test_wait_for_status_uses_suspend_aware_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()

    _wait_for_never_completed(
        monkeypatch,
        clock,
        timeout_minutes=1,
        poll_interval_seconds=30,
    )

    assert clock.current == 60


def test_wait_for_status_logs_unchanged_status_heartbeats(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _FakeClock()
    messages: list[str] = []

    _wait_for_never_completed(
        monkeypatch,
        clock,
        timeout_minutes=11,
        poll_interval_seconds=60,
        log=messages.append,
    )

    assert "Observed status=Queued (elapsed=300s, remaining=360s)" in messages
    assert "Observed status=Queued (elapsed=600s, remaining=60s)" in messages
