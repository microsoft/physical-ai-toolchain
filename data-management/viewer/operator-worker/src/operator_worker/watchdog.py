"""Independent parent-loss watchdog for hardware deenergization."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

from .acquisition import CleanupReport
from .config import WorkerProfile
from .deenergize import deenergize_profile


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def watchdog_loop(
    read_fd: int,
    *,
    backend_pid: int,
    worker_pid: int,
    profile: WorkerProfile,
    grace_s: float = 5.0,
    deenergize=deenergize_profile,
) -> bool:
    """Recover only when the backend is gone and normal cleanup cannot finish."""
    while True:
        readable, _, _ = select.select([read_fd], [], [], 0.2)
        if readable:
            message = os.read(read_fd, 1)
            if message == b"C":
                return True
            if message == b"" and _is_alive(backend_pid):
                return True
            if message == b"":
                report = deenergize(profile)
                return report.cleanup_complete
        if _is_alive(backend_pid):
            continue
        if _is_alive(worker_pid):
            os.kill(worker_pid, signal.SIGTERM)
            deadline = time.monotonic() + grace_s
            while _is_alive(worker_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            if _is_alive(worker_pid):
                os.kill(worker_pid, signal.SIGKILL)
        report = deenergize(profile)
        return report.cleanup_complete


def watchdog_main() -> int:
    profile = WorkerProfile.model_validate_json(os.environ["OPERATOR_PROFILE_JSON"])
    recovered = watchdog_loop(
        int(os.environ["OPERATOR_WATCHDOG_FD"]),
        backend_pid=int(os.environ["OPERATOR_PARENT_PID"]),
        worker_pid=int(os.environ["OPERATOR_WORKER_PID"]),
        profile=profile,
    )
    return 0 if recovered else 1


@dataclass
class WatchdogController:
    process: subprocess.Popen[bytes]
    write_fd: int

    def disarm(self) -> None:
        try:
            os.write(self.write_fd, b"C")
        finally:
            os.close(self.write_fd)
        self.process.wait(timeout=5)


def start_watchdog(
    profile: WorkerProfile, *, backend_pid: int, lease_fd: int
) -> WatchdogController:
    """Launch a lease-inheriting watchdog before hardware acquisition."""
    read_fd, write_fd = os.pipe()
    environment = {
        "OPERATOR_PROFILE_JSON": profile.model_dump_json(),
        "OPERATOR_PARENT_PID": str(backend_pid),
        "OPERATOR_WORKER_PID": str(os.getpid()),
        "OPERATOR_WATCHDOG_FD": str(read_fd),
        "OPERATOR_HOST_LEASE_FD": str(lease_fd),
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.argv[0], "--watchdog"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        pass_fds=(read_fd, lease_fd),
    )
    os.close(read_fd)
    return WatchdogController(process=process, write_fd=write_fd)


class GuardedRuntime:
    """Disarm the independent watchdog only after confirmed cleanup."""

    def __init__(self, runtime: Any, watchdog: WatchdogController) -> None:
        self.runtime = runtime
        self.watchdog = watchdog

    def acquire(self) -> None:
        self.runtime.acquire()

    def enable_motion(self) -> None:
        self.runtime.enable_motion()

    def teleoperate(self) -> None:
        self.runtime.teleoperate()

    def record(self) -> None:
        self.runtime.record()

    def policy(self) -> None:
        self.runtime.policy()

    def command(self, action: str):
        return self.runtime.command(action)

    def request_stop(self) -> None:
        self.runtime.request_stop()

    def discard_recording(self) -> None:
        self.runtime.discard_recording()

    def set_rate_callback(self, callback) -> None:
        self.runtime.set_rate_callback(callback)

    def set_telemetry_callback(self, callback) -> None:
        self.runtime.set_telemetry_callback(callback)

    def set_preview_callback(self, callback) -> None:
        self.runtime.set_preview_callback(callback)

    def upload_after_cleanup(self) -> tuple[bool, bool, str | None]:
        return self.runtime.upload_after_cleanup()

    def cleanup(self) -> CleanupReport:
        report = self.runtime.cleanup()
        if report.cleanup_complete:
            self.watchdog.disarm()
        return report
