from __future__ import annotations

import os
from types import SimpleNamespace

from test_so101 import _profile

from operator_worker.acquisition import CleanupReport
from operator_worker.watchdog import GuardedRuntime, watchdog_loop


def test_guarded_runtime_forwards_recording_operations() -> None:
    events = []
    runtime = SimpleNamespace(
        record=lambda: events.append("record"),
        command=lambda action: events.append(action) or action,
        upload_after_cleanup=lambda: (True, True, None),
    )
    guarded = GuardedRuntime(runtime, SimpleNamespace())

    guarded.record()
    result = guarded.command("save")
    upload = guarded.upload_after_cleanup()

    assert events == ["record", "save"]
    assert result == "save"
    assert upload == (True, True, None)


def test_watchdog_disarms_without_recovery(tmp_path) -> None:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"C")
    os.close(write_fd)
    calls = []
    try:
        assert watchdog_loop(
            read_fd,
            backend_pid=os.getpid(),
            worker_pid=999_999,
            profile=_profile(tmp_path),
            deenergize=lambda profile: calls.append(profile)
            or CleanupReport(True, (), ()),
        )
    finally:
        os.close(read_fd)
    assert calls == []


def test_watchdog_recovers_after_backend_and_worker_loss(tmp_path) -> None:
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    calls = []
    try:
        assert watchdog_loop(
            read_fd,
            backend_pid=999_998,
            worker_pid=999_999,
            profile=_profile(tmp_path),
            deenergize=lambda profile: calls.append(profile)
            or CleanupReport(True, ("follower", "leader"), ()),
        )
    finally:
        os.close(read_fd)
    assert len(calls) == 1
