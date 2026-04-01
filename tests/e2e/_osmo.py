from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.e2e._common import format_command_failure, log_e2e, parse_json_from_output, run_command, wait_for_status

OSMO_STARTED_STATES = {"RUNNING", "COMPLETED", "SUCCEEDED"}
OSMO_FAILURE_PREFIXES = ("FAILED",)
OSMO_FAILURE_STATES = {"CANCELLED", "CANCELED", "ERROR"}


@dataclass
class OSMOWorkflow:
    workflow_id: str
    workflow_name: str
    experiment_name: str
    correlation_id: str
    is_terminal: bool = False
    terminal_status: str | None = None


def _find_first_string(payload: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(payload, Mapping):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _find_first_string(value, keys)
            if found is not None:
                return found

    if isinstance(payload, list):
        for item in payload:
            found = _find_first_string(item, keys)
            if found is not None:
                return found

    return None


def _e2e_correlation_id() -> str:
    return f"osmo-rl-e2e-{uuid.uuid4().hex}"


def submit_osmo_training(
    repo_root: Path,
    *,
    task: str,
    max_iterations: int,
    num_envs: int,
) -> OSMOWorkflow:
    experiment_name = f"isaaclab-{task}" if task else "isaaclab-training"
    correlation_id = _e2e_correlation_id()
    log_e2e(
        "Submitting OSMO workflow "
        f"for task={task}, num_envs={num_envs}, max_iterations={max_iterations}, experiment={experiment_name}, "
        f"correlation_id={correlation_id}"
    )
    result = run_command(
        [
            str(repo_root / "training/rl/scripts/submit-osmo-training.sh"),
            "--task",
            task,
            "--max-iterations",
            str(max_iterations),
            "--num-envs",
            str(num_envs),
            "--correlation-id",
            correlation_id,
            "--skip-register-checkpoint",
            "--",
            "--format-type",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"OSMO e2e submission failed\n\n{format_command_failure(result)}")

    payload = parse_json_from_output("\n".join(part for part in (result.stdout, result.stderr) if part))
    workflow_id = _find_first_string(payload, ("workflow_id", "workflowId", "id", "name"))
    workflow_name = _find_first_string(payload, ("name", "workflow_name", "workflowName", "display_name"))
    if workflow_id is None:
        raise AssertionError(f"Unable to parse OSMO workflow ID from submission output\n\n{result.stdout.strip()}")
    if workflow_name is None:
        workflow_name = workflow_id

    log_e2e(f"Submitted OSMO workflow id={workflow_id}, name={workflow_name}")

    return OSMOWorkflow(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        experiment_name=experiment_name,
        correlation_id=correlation_id,
    )


def _fetch_osmo_workflow_payload(workflow: OSMOWorkflow, repo_root: Path) -> dict[str, Any]:
    result = run_command(
        ["osmo", "workflow", "query", workflow.workflow_id, "--format-type", "json"],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Unable to query OSMO workflow {workflow.workflow_id!r}\n\n{format_command_failure(result)}"
        )

    payload = parse_json_from_output(result.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"OSMO workflow payload for {workflow.workflow_id!r} was not a JSON object")
    return payload


def _osmo_status(payload: Mapping[str, Any]) -> str:
    status = _find_first_string(payload, ("status", "state", "phase"))
    return status or "UNKNOWN"


def wait_until_osmo_started(
    workflow: OSMOWorkflow,
    repo_root: Path,
    *,
    timeout_minutes: int,
    poll_interval_seconds: int,
) -> None:
    wait_for_status(
        lambda: _osmo_status(_fetch_osmo_workflow_payload(workflow, repo_root)),
        goal_description=f"OSMO workflow {workflow.workflow_id} to start",
        timeout_minutes=timeout_minutes,
        poll_interval_seconds=poll_interval_seconds,
        success_statuses=OSMO_STARTED_STATES,
        failure_statuses=OSMO_FAILURE_STATES,
        failure_matcher=lambda status: any(status.startswith(prefix) for prefix in OSMO_FAILURE_PREFIXES),
    )


def wait_until_osmo_completed(
    workflow: OSMOWorkflow,
    repo_root: Path,
    *,
    timeout_minutes: int,
    poll_interval_seconds: int,
) -> None:
    terminal_status = wait_for_status(
        lambda: _osmo_status(_fetch_osmo_workflow_payload(workflow, repo_root)),
        goal_description=f"OSMO workflow {workflow.workflow_id} to complete",
        timeout_minutes=timeout_minutes,
        poll_interval_seconds=poll_interval_seconds,
        success_statuses={"COMPLETED", "SUCCEEDED"},
        failure_statuses=OSMO_FAILURE_STATES,
        failure_matcher=lambda status: any(status.startswith(prefix) for prefix in OSMO_FAILURE_PREFIXES),
        on_failure=lambda status: _mark_workflow_terminal(workflow, status),
        status_log_prefix="Completion poll status",
    )
    _mark_workflow_terminal(workflow, terminal_status)
    log_e2e(f"OSMO workflow {workflow.workflow_id} completed successfully")


def _mark_workflow_terminal(workflow: OSMOWorkflow, terminal_status: str) -> None:
    workflow.is_terminal = True
    workflow.terminal_status = terminal_status


def assert_workflow_task_succeeded(workflow: OSMOWorkflow, repo_root: Path, task_name: str) -> None:
    payload = _fetch_osmo_workflow_payload(workflow, repo_root)
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise AssertionError(f"OSMO workflow payload for {workflow.workflow_id!r} did not include task groups")

    for group in groups:
        if not isinstance(group, Mapping):
            continue
        tasks = group.get("tasks")
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, Mapping):
                continue
            current_name = task.get("name")
            if current_name != task_name:
                continue

            status = task.get("status")
            exit_code = task.get("exit_code")
            pod_name = task.get("pod_name")
            if status in {"COMPLETED", "SUCCEEDED"} and exit_code == 0:
                rendered_pod = pod_name if isinstance(pod_name, str) and pod_name else "<unknown>"
                log_e2e(f"Verified OSMO task {task_name} succeeded with exit_code=0 on pod={rendered_pod}")
                return

            raise AssertionError(
                f"OSMO task {task_name!r} did not succeed: status={status!r}, exit_code={exit_code!r}, "
                f"pod_name={pod_name!r}"
            )

    raise AssertionError(f"OSMO workflow {workflow.workflow_id!r} did not contain task {task_name!r}")


def cancel_osmo_workflow(workflow: OSMOWorkflow, repo_root: Path) -> None:
    if workflow.is_terminal:
        log_e2e(f"Skipping cancel for OSMO workflow {workflow.workflow_id}; terminal status={workflow.terminal_status}")
        return

    log_e2e(f"Cancelling OSMO workflow {workflow.workflow_id}")

    run_command(["osmo", "workflow", "cancel", workflow.workflow_id], cwd=repo_root)
