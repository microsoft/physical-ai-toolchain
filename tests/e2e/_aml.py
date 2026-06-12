from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tests.e2e._common import format_command_failure, log_e2e, parse_json_from_output, run_command, wait_for_status

AML_STARTED_STATES = {"Queued", "Preparing", "Starting", "Running", "Finalizing", "Completed"}
AML_FAILURE_STATES = {"Canceled", "Cancelled", "Failed", "NotResponding"}


@dataclass
class AzureMLWorkspace:
    subscription_id: str
    resource_group: str
    workspace_name: str


@dataclass
class AzureMLJob:
    name: str
    workspace: AzureMLWorkspace
    experiment_name: str
    is_terminal: bool = False
    terminal_status: str | None = None


def _e2e_name(prefix: str) -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _parse_azureml_job_name(output: str) -> str | None:
    patterns = (
        r"Job submitted:\s*(?P<name>[^\s]+)",
        r"Job Name:\s*(?P<name>[^\s]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, output)
        if match is not None:
            return match.group("name")
    return None


def submit_aml_training(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    task: str,
    max_iterations: int,
    num_envs: int,
) -> AzureMLJob:
    experiment_name = _e2e_name("rl-training-e2e-aml")
    log_e2e(
        "Submitting AzureML training job "
        f"for task={task}, num_envs={num_envs}, max_iterations={max_iterations}, experiment={experiment_name}"
    )
    result = run_command(
        [
            str(repo_root / "training/rl/scripts/submit-azureml-training.sh"),
            "--task",
            task,
            "--max-iterations",
            str(max_iterations),
            "--num-envs",
            str(num_envs),
            "--experiment-name",
            experiment_name,
            "--subscription-id",
            aml_workspace.subscription_id,
            "--resource-group",
            aml_workspace.resource_group,
            "--workspace-name",
            aml_workspace.workspace_name,
            "--skip-register-checkpoint",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML e2e submission failed\n\n{format_command_failure(result)}")

    combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    job_name = _parse_azureml_job_name(combined_output)
    if job_name is None:
        raise AssertionError(f"Unable to parse AzureML job name from submission output\n\n{combined_output.strip()}")

    log_e2e(f"Submitted AzureML job name={job_name}")

    return AzureMLJob(
        name=job_name,
        workspace=aml_workspace,
        experiment_name=experiment_name,
    )


def submit_aml_lerobot_training(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    dataset_repo_id: str,
    policy_type: str,
    training_steps: int,
    save_freq: int,
    batch_size: int,
    log_freq: int,
) -> AzureMLJob:
    experiment_name = _e2e_name("il-training-e2e-aml")
    log_e2e(
        "Submitting AzureML LeRobot training job "
        f"for dataset={dataset_repo_id}, policy={policy_type}, training_steps={training_steps}, "
        f"save_freq={save_freq}, batch_size={batch_size}, log_freq={log_freq}, experiment={experiment_name}"
    )
    # eval-freq > training-steps disables in-loop evaluation (which would need
    # sim deps that are not part of the lerobot training container).
    result = run_command(
        [
            str(repo_root / "training/il/scripts/submit-azureml-lerobot-training.sh"),
            "--dataset-repo-id",
            dataset_repo_id,
            "--policy-type",
            policy_type,
            "--training-steps",
            str(training_steps),
            "--save-freq",
            str(save_freq),
            "--batch-size",
            str(batch_size),
            "--eval-freq",
            str(training_steps + 1),
            "--log-freq",
            str(log_freq),
            "--experiment-name",
            experiment_name,
            "--subscription-id",
            aml_workspace.subscription_id,
            "--resource-group",
            aml_workspace.resource_group,
            "--workspace-name",
            aml_workspace.workspace_name,
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML LeRobot e2e submission failed\n\n{format_command_failure(result)}")

    combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    job_name = _parse_azureml_job_name(combined_output)
    if job_name is None:
        raise AssertionError(
            f"Unable to parse AzureML job name from LeRobot submission output\n\n{combined_output.strip()}"
        )

    log_e2e(f"Submitted AzureML LeRobot job name={job_name}")

    return AzureMLJob(
        name=job_name,
        workspace=aml_workspace,
        experiment_name=experiment_name,
    )


def fetch_aml_job_payload(job: AzureMLJob, repo_root: Path) -> dict[str, Any]:
    result = run_command(
        [
            "az",
            "ml",
            "job",
            "show",
            "--subscription",
            job.workspace.subscription_id,
            "--resource-group",
            job.workspace.resource_group,
            "--workspace-name",
            job.workspace.workspace_name,
            "--name",
            job.name,
            "-o",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"Unable to query AzureML job {job.name!r}\n\n{format_command_failure(result)}")

    payload = parse_json_from_output(result.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"AzureML job payload for {job.name!r} was not a JSON object")
    return payload


def _aml_status(payload: Mapping[str, Any]) -> str:
    properties = payload.get("properties")
    if isinstance(properties, Mapping):
        status = properties.get("status")
        if isinstance(status, str) and status:
            return status

    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    return "UNKNOWN"


def wait_until_aml_started(
    job: AzureMLJob,
    repo_root: Path,
    *,
    timeout_minutes: int,
    poll_interval_seconds: int,
) -> None:
    wait_for_status(
        lambda: _aml_status(fetch_aml_job_payload(job, repo_root)),
        goal_description=f"AzureML job {job.name} to start",
        timeout_minutes=timeout_minutes,
        poll_interval_seconds=poll_interval_seconds,
        success_statuses=AML_STARTED_STATES,
        failure_statuses=AML_FAILURE_STATES,
    )


def wait_until_aml_completed(
    job: AzureMLJob,
    repo_root: Path,
    *,
    timeout_minutes: int,
    poll_interval_seconds: int,
) -> None:
    terminal_status = wait_for_status(
        lambda: _aml_status(fetch_aml_job_payload(job, repo_root)),
        goal_description=f"AzureML job {job.name} to complete",
        timeout_minutes=timeout_minutes,
        poll_interval_seconds=poll_interval_seconds,
        success_statuses={"COMPLETED"},
        failure_statuses=AML_FAILURE_STATES,
        on_failure=lambda status: _mark_job_terminal(job, status),
        status_log_prefix="Completion poll status",
    )
    _mark_job_terminal(job, terminal_status)
    log_e2e(f"AzureML job {job.name} completed successfully")


def _mark_job_terminal(job: AzureMLJob, terminal_status: str) -> None:
    job.is_terminal = True
    job.terminal_status = terminal_status


def assert_job_has_checkpoint(job: AzureMLJob) -> None:
    """Verify the AzureML ``checkpoints`` named output was populated with at least one file.

    The contents check (not just declaration check) is essential: when the
    output binding is mis-wired, the named output is still *declared* on the
    job payload but the upload directory stays empty and Azure ML silently
    skips the upload. The bug fixed by #855 had exactly this shape — the
    pre-fix code produced jobs whose payload listed ``outputs.checkpoints``
    but whose blob path contained zero files.

    Lists blobs under the named output's ``workspaceblobstore`` prefix
    instead of downloading them — checkpoints can be hundreds of MB.
    """
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import ContainerClient

    log_e2e(f"Listing checkpoint output blobs for AzureML job {job.name}")
    credential = DefaultAzureCredential()
    client = MLClient(
        credential=credential,
        subscription_id=job.workspace.subscription_id,
        resource_group_name=job.workspace.resource_group,
        workspace_name=job.workspace.workspace_name,
    )

    datastore = client.datastores.get("workspaceblobstore")
    account_name = getattr(datastore, "account_name", None)
    container_name = getattr(datastore, "container_name", None)
    if not isinstance(account_name, str) or not account_name:
        raise AssertionError(f"workspaceblobstore datastore has no account_name: {datastore!r}")
    if not isinstance(container_name, str) or not container_name:
        raise AssertionError(f"workspaceblobstore datastore has no container_name: {datastore!r}")

    prefix = f"azureml/{job.name}/checkpoints/"
    container = ContainerClient(
        account_url=f"https://{account_name}.blob.core.windows.net",
        container_name=container_name,
        credential=credential,
    )
    with container:
        blob_names = sorted(b.name for b in container.list_blobs(name_starts_with=prefix))

    if not blob_names:
        raise AssertionError(
            f"AzureML job {job.name!r} 'checkpoints' named output is empty. "
            "The training entry point did not write any files to "
            "$AZURE_ML_OUTPUT_CHECKPOINTS — the wiring is broken (see #855)."
        )

    relative_names = [name.removeprefix(prefix) for name in blob_names]
    sample = ", ".join(relative_names[:5])
    log_e2e(f"Checkpoint output for AzureML job {job.name} contains {len(blob_names)} file(s); sample: {sample}")


def _aml_code_resource_id(payload: Mapping[str, Any]) -> str:
    code_id = payload.get("code")
    if isinstance(code_id, str) and code_id.startswith("azureml:/"):
        return code_id.removeprefix("azureml:")

    properties = payload.get("properties")
    if isinstance(properties, Mapping):
        code_id = properties.get("codeId")
        if isinstance(code_id, str) and code_id.startswith("/"):
            return code_id

    raise AssertionError("AzureML job payload did not include a code asset ID")


def _code_blob_location(code_resource_id: str, repo_root: Path) -> tuple[str, str, str]:
    result = run_command(["az", "resource", "show", "--ids", code_resource_id, "-o", "json"], cwd=repo_root)
    if result.returncode != 0:
        raise AssertionError(
            f"Unable to query AzureML code asset {code_resource_id!r}\n\n{format_command_failure(result)}"
        )

    payload = parse_json_from_output(result.stdout)
    if not isinstance(payload, Mapping):
        raise AssertionError(f"AzureML code asset {code_resource_id!r} was not a JSON object")

    properties = payload.get("properties")
    if not isinstance(properties, Mapping):
        raise AssertionError(f"AzureML code asset {code_resource_id!r} did not include properties")

    code_uri = properties.get("codeUri")
    if not isinstance(code_uri, str) or not code_uri:
        raise AssertionError(f"AzureML code asset {code_resource_id!r} did not include a codeUri")

    parsed = urlparse(code_uri)
    account_name = parsed.hostname.split(".", 1)[0] if parsed.hostname else ""
    path_parts = parsed.path.lstrip("/").split("/", 1)
    if not account_name or len(path_parts) != 2 or not path_parts[0] or not path_parts[1]:
        raise AssertionError(f"AzureML code asset URI had an unexpected format: {code_uri}")

    return account_name, path_parts[0], path_parts[1].rstrip("/")


def assert_job_snapshot_contains_only_training(job: AzureMLJob, repo_root: Path) -> None:
    log_e2e(f"Inspecting uploaded code snapshot for AzureML job {job.name}")
    payload = fetch_aml_job_payload(job, repo_root)
    code_resource_id = _aml_code_resource_id(payload)
    account_name, container_name, blob_prefix = _code_blob_location(code_resource_id, repo_root)

    if Path(blob_prefix).name != "training":
        raise AssertionError(
            f"AzureML code asset did not use the training directory as the snapshot root\n\n{blob_prefix}"
        )

    result = run_command(
        [
            "az",
            "storage",
            "blob",
            "list",
            "--account-name",
            account_name,
            "--container-name",
            container_name,
            "--prefix",
            f"{blob_prefix}/",
            "--auth-mode",
            "login",
            "--query",
            "[].name",
            "-o",
            "tsv",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"Unable to inspect AzureML code asset snapshot\n\n{format_command_failure(result)}")

    blob_names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not blob_names:
        raise AssertionError(f"AzureML code asset {code_resource_id!r} contained no files")

    relative_names = [name.removeprefix(f"{blob_prefix}/") for name in blob_names]
    unexpected = [
        name
        for name in relative_names
        if name == ".git" or name.startswith(".git/") or name == "docs" or name.startswith("docs/")
    ]
    if unexpected:
        preview = "\n".join(unexpected[:10])
        raise AssertionError(f"AzureML code asset contained files outside the training snapshot\n\n{preview}")

    top_level_entries = {name.split("/", 1)[0] for name in relative_names if name}
    required_entries = {"__init__.py", "rl", "stream.py", "utils"}
    missing_entries = sorted(required_entries - top_level_entries)
    if missing_entries:
        rendered = ", ".join(missing_entries)
        raise AssertionError(f"AzureML code asset was missing expected training entries\n\n{rendered}")

    log_e2e(
        f"Code snapshot validation passed for AzureML job {job.name}; "
        f"top-level entries={', '.join(sorted(top_level_entries))}"
    )


def cancel_aml_job(job: AzureMLJob, repo_root: Path) -> None:
    if job.is_terminal:
        log_e2e(f"Skipping cancel for AzureML job {job.name}; terminal status={job.terminal_status}")
        return

    log_e2e(f"Cancelling AzureML job {job.name}")

    run_command(
        [
            "az",
            "ml",
            "job",
            "cancel",
            "--subscription",
            job.workspace.subscription_id,
            "--resource-group",
            job.workspace.resource_group,
            "--workspace-name",
            job.workspace.workspace_name,
            "--name",
            job.name,
        ],
        cwd=repo_root,
    )
