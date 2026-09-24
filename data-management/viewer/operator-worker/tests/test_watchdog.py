from __future__ import annotations

import os
from types import SimpleNamespace

from operator_worker.resources import CleanupReport
from operator_worker.watchdog import GuardedRuntime, watchdog_loop


def test_given_confirmed_cleanup_when_guarded_runtime_cleans_then_watchdog_disarms() -> None:
    calls: list[str] = []
    runtime = SimpleNamespace(cleanup=lambda: CleanupReport(True, (), (), torque_verified_off=True))
    watchdog = SimpleNamespace(disarm=lambda: calls.append("disarm"))

    report = GuardedRuntime(runtime, watchdog).cleanup()

    assert report.cleanup_complete is True
    assert calls == ["disarm"]


def test_given_unconfirmed_cleanup_when_guarded_runtime_cleans_then_watchdog_stays_armed() -> None:
    calls: list[str] = []
    runtime = SimpleNamespace(cleanup=lambda: CleanupReport(True, (), (), torque_verified_off=False))
    watchdog = SimpleNamespace(disarm=lambda: calls.append("disarm"))

    GuardedRuntime(runtime, watchdog).cleanup()

    assert calls == []


def test_given_backend_loss_when_watchdog_runs_then_recovery_is_frontend_independent() -> None:
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    recovered: list[object] = []
    profile = object()
    try:
        result = watchdog_loop(
            read_fd,
            backend_pid=100,
            worker_pid=101,
            profile=profile,
            deenergize=lambda value: (
                recovered.append(value) or CleanupReport(True, ("follower",), (), torque_verified_off=True)
            ),
            is_alive=lambda _pid: False,
        )
    finally:
        os.close(read_fd)

    assert result is True
    assert recovered == [profile]
