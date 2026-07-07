from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

_MAX_CONSECUTIVE_STATUS_ERRORS = 5


def e2e_name(prefix: str) -> str:
    """Generate a collision-resistant resource name for an e2e run."""
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def env_value(name: str, default: str | None = None) -> str | None:
    """Return a stripped environment variable, or ``default`` when unset/blank."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


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


def upload_blob_directory(
    repo_root: Path,
    storage_account: str,
    container: str,
    prefix: str,
    source_dir: Path,
    *,
    description: str,
) -> None:
    result = run_command(
        [
            "az",
            "storage",
            "blob",
            "upload-batch",
            "--account-name",
            storage_account,
            "--auth-mode",
            "login",
            "--destination",
            container,
            "--destination-path",
            prefix,
            "--source",
            str(source_dir),
            "--overwrite",
            "--only-show-errors",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Failed to upload {description} to {storage_account}/{container}/{prefix}\n\n"
            f"{format_command_failure(result)}"
        )


def delete_blob_prefix(
    repo_root: Path,
    storage_account: str,
    container: str,
    prefix: str,
    *,
    description: str,
) -> None:
    log_e2e(f"Deleting {description} under {container}/{prefix}")
    result = run_command(
        [
            "az",
            "storage",
            "blob",
            "delete-batch",
            "--account-name",
            storage_account,
            "--auth-mode",
            "login",
            "--source",
            container,
            "--pattern",
            f"{prefix}/*",
            "--only-show-errors",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Failed to delete {description} under {storage_account}/{container}/{prefix}\n\n"
            f"{format_command_failure(result)}"
        )


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

    consecutive_errors = 0
    while time.monotonic() < deadline:
        try:
            last_status = fetch_status()
        except Exception as error:
            # A single throttled/blipped status query must not abort a 30-60 minute
            # live poll; only a persistent failure run is treated as fatal.
            consecutive_errors += 1
            log_e2e(f"Status fetch failed ({consecutive_errors}/{_MAX_CONSECUTIVE_STATUS_ERRORS}): {error}")
            if consecutive_errors >= _MAX_CONSECUTIVE_STATUS_ERRORS:
                raise
            time.sleep(poll_interval_seconds)
            continue
        consecutive_errors = 0
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
