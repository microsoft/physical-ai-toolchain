"""Run the API with graceful request cancellation and an outer process shutdown deadline."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path
from types import FrameType

from dotenv import load_dotenv

from .logging_config import configure_logging

logger = logging.getLogger(__name__)


def shutdown_limits() -> tuple[int, int]:
    """Validate operator-configured request grace and total process shutdown limits."""
    values = []
    for name, default in (
        ("DATAVIEWER_GRACEFUL_SHUTDOWN_SECONDS", 10),
        ("DATAVIEWER_SHUTDOWN_TIMEOUT_SECONDS", 35),
    ):
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError as error:
            raise ValueError(f"{name} must be an integer from 1 to 600") from error
        if not 1 <= value <= 600:
            raise ValueError(f"{name} must be an integer from 1 to 600")
        values.append(value)
    graceful, total = values
    if total <= graceful:
        raise ValueError("DATAVIEWER_SHUTDOWN_TIMEOUT_SECONDS must exceed DATAVIEWER_GRACEFUL_SHUTDOWN_SECONDS")
    return graceful, total


def supervise_server(command: list[str], *, shutdown_seconds: float) -> int:
    """Forward shutdown once, then reap a child that exceeds the total grace period.

    On POSIX, the child owns a new process group so escalation also stops its workers.
    The supervisor never participates in the API thread pool or event loop.
    """
    process = subprocess.Popen(command, start_new_session=os.name == "posix")
    timer = None

    def kill_group() -> None:
        with suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()

    def expire() -> None:
        logger.warning("API shutdown deadline exceeded; terminating server process group")
        kill_group()

    def shutdown(signum: int, _frame: FrameType | None) -> None:
        nonlocal timer
        if timer is not None:
            return
        timer = threading.Timer(shutdown_seconds, expire)
        timer.daemon = True
        timer.start()
        with suppress(ProcessLookupError):
            process.send_signal(signum)

    previous = {signum: signal.signal(signum, shutdown) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        result = process.wait()
        return result if result >= 0 else 128 - result
    finally:
        if timer is not None:
            timer.cancel()
        kill_group()
        process.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main() -> int:
    """Start the configured server after loading the same environment as the application."""
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--host", default=os.environ.get("BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=os.environ.get("BACKEND_PORT", "8000"))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    parser.add_argument("--log-config", default="logging.json")
    args = parser.parse_args()
    try:
        graceful, total = shutdown_limits()
    except ValueError as error:
        logger.error("%s", error)
        return 2
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.api.main:app",
        "--log-config",
        args.log_config,
        "--log-level",
        args.log_level,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--timeout-graceful-shutdown",
        str(graceful),
    ]
    if args.reload:
        command.append("--reload")
    return supervise_server(command, shutdown_seconds=total)


if __name__ == "__main__":
    sys.exit(main())
