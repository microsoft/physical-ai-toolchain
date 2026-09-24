"""Independent parent-loss watchdog for hardware deenergization."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .deenergize import deenergize_profile
from .resources import CleanupReport


def process_is_alive(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    return True


def watchdog_loop(
    read_fd: int,
    *,
    backend_pid: int,
    worker_pid: int,
    profile: Any,
    deenergize: Callable[[Any], CleanupReport] = deenergize_profile,
    grace_s: float = 5.0,
    is_alive: Callable[[int], bool] = process_is_alive,
) -> bool:
    """Recover when the backend is gone and normal worker cleanup cannot finish."""
    while True:
        readable, _, _ = select.select([read_fd], [], [], 0.2)
        if readable:
            message = os.read(read_fd, 1)
            if message == b"C" or (message == b"" and is_alive(backend_pid)):
                return True
            if message == b"":
                return deenergize(profile).cleanup_complete
        if is_alive(backend_pid):
            continue
        if is_alive(worker_pid):
            os.kill(worker_pid, signal.SIGTERM)
            deadline = time.monotonic() + grace_s
            while is_alive(worker_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            if is_alive(worker_pid):
                os.kill(worker_pid, signal.SIGKILL)
        return deenergize(profile).cleanup_complete


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


def watchdog_main(*, resource_factory: Callable[[Any], Any]) -> int:
    """Run the watchdog process from inherited environment state."""
    profile = json.loads(os.environ["OPERATOR_PROFILE_JSON"])
    recovered = watchdog_loop(
        int(os.environ["OPERATOR_WATCHDOG_FD"]),
        backend_pid=int(os.environ["OPERATOR_PARENT_PID"]),
        worker_pid=int(os.environ["OPERATOR_WORKER_PID"]),
        profile=profile,
        deenergize=lambda value: deenergize_profile(value, resource_factory=resource_factory),
    )
    return 0 if recovered else 1


def start_watchdog(profile: Any, *, backend_pid: int, lease_fd: int) -> WatchdogController:
    """Launch a lease-inheriting watchdog before hardware acquisition."""
    read_fd, write_fd = os.pipe()
    if hasattr(profile, "model_dump_json"):
        profile_json = profile.model_dump_json()
    else:
        profile_json = json.dumps(profile, default=str)
    environment = {
        "OPERATOR_PROFILE_JSON": profile_json,
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

    def __getattr__(self, name: str) -> Any:
        return getattr(self.runtime, name)

    def cleanup(self) -> CleanupReport:
        report = self.runtime.cleanup()
        if report.cleanup_complete and report.torque_verified_off:
            self.watchdog.disarm()
        return report
