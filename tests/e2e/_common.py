from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any


def run_command(
    args: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
        input=input_text,
    )


def format_command_failure(result: subprocess.CompletedProcess[str]) -> str:
    parts = [f"exit code: {result.returncode}"]
    if result.stdout.strip():
        parts.append(f"stdout:\n{result.stdout.strip()}")
    if result.stderr.strip():
        parts.append(f"stderr:\n{result.stderr.strip()}")
    return "\n\n".join(parts)


def parse_json_from_output(output: str) -> Any:
    decoder = json.JSONDecoder()
    stripped = output.strip()
    if not stripped:
        raise AssertionError("Command output was empty")

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    for index, character in enumerate(output):
        if character not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        return payload

    raise AssertionError(f"Unable to parse JSON payload from command output\n\n{stripped}")


def log_e2e(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[e2e] [{timestamp}]: {message}", flush=True)


def wait_for_status(
    fetch_status: Callable[[], str],
    *,
    goal_description: str,
    timeout_minutes: int,
    poll_interval_seconds: int,
    success_statuses: Iterable[str],
    failure_statuses: Iterable[str] = (),
    failure_matcher: Callable[[str], bool] | None = None,
    on_failure: Callable[[str], None] | None = None,
    status_log_prefix: str = "Observed status",
    log_status_changes: bool = True,
) -> str:
    deadline = time.monotonic() + (timeout_minutes * 60)
    last_status = "UNKNOWN"
    previous_status: str | None = None
    normalized_success_statuses = {status.upper() for status in success_statuses}
    normalized_failure_statuses = {status.upper() for status in failure_statuses}

    log_e2e(f"Waiting for {goal_description} for up to {timeout_minutes} minutes (poll every {poll_interval_seconds}s)")

    while time.monotonic() < deadline:
        last_status = fetch_status()
        normalized_status = last_status.upper()

        if log_status_changes and last_status != previous_status:
            log_e2e(f"{status_log_prefix}={last_status}")
            previous_status = last_status

        if normalized_status in normalized_failure_statuses or (
            failure_matcher is not None and failure_matcher(normalized_status)
        ):
            if on_failure is not None:
                on_failure(last_status)
            raise AssertionError(f"{goal_description} failed with status {last_status!r}")

        if normalized_status in normalized_success_statuses:
            log_e2e(f"Reached {goal_description} with status={last_status}")
            return last_status

        time.sleep(poll_interval_seconds)

    raise AssertionError(f"Timed out waiting for {goal_description}; last status was {last_status!r}")
