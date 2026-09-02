"""CLI entry point for the isolated SO-101 worker."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from threading import Event

from .app import WorkerApplication
from .camera_check import check_profile_cameras
from .config import WorkerProfile
from .deenergize import deenergize_profile
from .parent_watch import arm_parent_death_signal
from .so101 import build_so101_runtime
from .watchdog import GuardedRuntime, start_watchdog, watchdog_main


def main() -> int:
    """Arm parent-death handling and run one worker session."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--deenergize", action="store_true")
    parser.add_argument("--camera-check", action="store_true")
    parser.add_argument("--watchdog", action="store_true")
    args = parser.parse_args()
    if args.watchdog:
        return watchdog_main()
    session_id = os.environ["OPERATOR_SESSION_ID"]
    parent_pid = int(os.environ["OPERATOR_PARENT_PID"])
    arm_parent_death_signal(parent_pid)
    lease_fd = int(os.environ["OPERATOR_HOST_LEASE_FD"])
    os.fstat(lease_fd)
    if args.deenergize or args.camera_check:
        profile = WorkerProfile.model_validate_json(os.environ["OPERATOR_PROFILE_JSON"])
        report = deenergize_profile(profile) if args.deenergize else check_profile_cameras(profile)
        print(
            json.dumps(
                {
                    "cleanup_complete": report.cleanup_complete,
                    "torque_verified_off": report.torque_verified_off,
                    "released": report.released,
                    "errors": report.errors,
                }
            ),
            flush=True,
        )
        return 0 if report.cleanup_complete else 1
    stop_event = Event()

    def request_stop(_signal_number, _frame) -> None:
        stop_event.set()

    for signal_number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signal_number, request_stop)
    application = WorkerApplication(
        session_id=session_id,
        input_stream=sys.stdin,
        output_stream=sys.stdout,
        runtime_factory=lambda profile, settings: GuardedRuntime(
            build_so101_runtime(profile, settings=settings, stop_event=stop_event),
            start_watchdog(profile, backend_pid=parent_pid, lease_fd=lease_fd),
        ),
    )
    try:
        return application.run()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
