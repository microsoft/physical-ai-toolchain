from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from tests.e2e._common import (
    e2e_name,
    env_value,
    format_command_failure,
    log_e2e,
    parse_json_from_output,
    run_command,
    wait_for_status,
)

AML_STARTED_STATES = {"Queued", "Preparing", "Starting", "Running", "Finalizing", "Completed"}
AML_FAILURE_STATES = {"Canceled", "Cancelled", "Failed", "NotResponding"}


@dataclass
class AzureMLWorkspace:
    subscription_id: str
    resource_group: str
    workspace_name: str


def archive_aml_asset(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    asset_type: str,
    name: str,
    version: str,
) -> None:
    log_e2e(f"Archiving AzureML {asset_type} {name}:{version}")
    result = run_command(
        [
            "az",
            "ml",
            asset_type,
            "archive",
            "--name",
            name,
            "--version",
            version,
            *aml_workspace_args(aml_workspace),
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Failed to archive AzureML {asset_type} {name}:{version}\n\n{format_command_failure(result)}"
        )


@dataclass
class AzureMLJob:
    name: str
    workspace: AzureMLWorkspace
    experiment_name: str
    is_terminal: bool = False
    terminal_status: str | None = None


@dataclass(frozen=True)
class AmlModelRef:
    """A concrete registered AzureML model — never the mutable ``latest`` alias."""

    name: str
    version: str


def _model_versions(payload: Any) -> list[int]:
    items = payload if isinstance(payload, list) else [payload]
    versions: list[int] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        try:
            versions.append(int(str(item.get("version"))))
        except (TypeError, ValueError):
            continue
    return versions


def _list_model_versions(repo_root: Path, aml_workspace: AzureMLWorkspace, model_name: str) -> list[int]:
    """List concrete integer versions registered under an AzureML model name."""
    result = run_command(
        [
            "az",
            "ml",
            "model",
            "list",
            "--name",
            model_name,
            *aml_workspace_args(aml_workspace),
            "-o",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Failed to list AzureML model versions for {model_name!r}\n\n{format_command_failure(result)}"
        )
    return _model_versions(parse_json_from_output(result.stdout))


def resolve_registered_model(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    model_name: str,
) -> AmlModelRef:
    """Resolve the concrete latest version of a registered AzureML model.

    A lifecycle test registers its checkpoint under a unique model name, so the
    returned version is the single concrete version that run produced — this avoids
    the mutable ``latest`` alias entirely.
    """
    versions = _list_model_versions(repo_root, aml_workspace, model_name)
    if not versions:
        raise AssertionError(f"Training run registered no versions under AzureML model {model_name!r}")
    version = str(max(versions))
    log_e2e(f"Resolved AzureML model {model_name} to concrete version {version}")
    return AmlModelRef(name=model_name, version=version)


def archive_all_model_versions(repo_root: Path, aml_workspace: AzureMLWorkspace, model_name: str) -> None:
    """Archive every registered version of an AzureML model (best-effort cleanup)."""
    for version in _list_model_versions(repo_root, aml_workspace, model_name):
        archive_aml_asset(repo_root, aml_workspace, "model", model_name, str(version))


def _parse_azureml_job_name(output: str) -> str | None:
    patterns = (
        r"Job submitted:\s*(?P<name>[^\s]+)",
        r"Pipeline submitted:\s*(?P<name>[^\s]+)",
        r"Job Name:\s*(?P<name>[^\s]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, output)
        if match is not None:
            return match.group("name")
    return None


def aml_workspace_args(aml_workspace: AzureMLWorkspace) -> list[str]:
    return [
        "--subscription",
        aml_workspace.subscription_id,
        "--resource-group",
        aml_workspace.resource_group,
        "--workspace-name",
        aml_workspace.workspace_name,
    ]


def _submit_workspace_args(aml_workspace: AzureMLWorkspace) -> list[str]:
    return [
        "--subscription-id",
        aml_workspace.subscription_id,
        "--resource-group",
        aml_workspace.resource_group,
        "--workspace-name",
        aml_workspace.workspace_name,
    ]


def submit_aml_training(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    task: str,
    max_iterations: int,
    num_envs: int,
) -> AzureMLJob:
    experiment_name = e2e_name("rl-training-e2e-aml")
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
            *_submit_workspace_args(aml_workspace),
            "--skip-register-checkpoint",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML e2e submission failed\n\n{format_command_failure(result)}")

    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AzureML training")


def submit_aml_lerobot_training(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    blob_url: str,
    policy_type: str,
    training_steps: int,
    save_freq: int,
    batch_size: int,
    log_freq: int,
    register_model_name: str,
) -> AzureMLJob:
    experiment_name = e2e_name("il-training-e2e-aml")
    log_e2e(
        "Submitting AzureML LeRobot training job "
        f"for dataset={blob_url}, policy={policy_type}, training_steps={training_steps}, "
        f"save_freq={save_freq}, batch_size={batch_size}, log_freq={log_freq}, experiment={experiment_name}"
    )
    # eval-freq > training-steps disables in-loop evaluation (which would need
    # sim deps that are not part of the lerobot training container).
    result = run_command(
        [
            str(repo_root / "training/il/scripts/submit-azureml-lerobot-training.sh"),
            "--blob-url",
            blob_url,
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
            *_submit_workspace_args(aml_workspace),
            "--register-checkpoint",
            register_model_name,
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML LeRobot e2e submission failed\n\n{format_command_failure(result)}")

    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AzureML LeRobot training")


_AML_LEROBOT_EVAL_MODEL_ENV = "E2E_AML_LEROBOT_EVAL_MODEL"


@dataclass(frozen=True)
class AmlLeRobotEvalPolicySource:
    """Resolved policy source for the AzureML LeRobot eval submission."""

    args: tuple[str, ...]
    description: str


def aml_lerobot_policy_source_from_model(model: AmlModelRef) -> AmlLeRobotEvalPolicySource:
    """Build an eval policy source from a concrete registered AzureML model."""
    return AmlLeRobotEvalPolicySource(
        args=("--from-aml-model", "--model-name", model.name, "--model-version", model.version),
        description=f"AzureML model {model.name}:{model.version}",
    )


def resolve_aml_lerobot_eval_policy_override() -> AmlLeRobotEvalPolicySource | None:
    """Resolve an eval policy source from the environment, or ``None`` when unset.

    ``submit-azureml-lerobot-eval.sh`` has no ``--builtin-policy`` option, so a real
    policy must be supplied. Configure:

    - ``E2E_AML_LEROBOT_EVAL_MODEL`` — an AzureML model ``name:version``.

    Returns ``None`` when this is unset: the lifecycle test provisions a freshly
    trained model instead. Malformed values skip.
    """
    model = env_value(_AML_LEROBOT_EVAL_MODEL_ENV)
    if not model:
        return None
    if ":" not in model:
        pytest.skip(f"{_AML_LEROBOT_EVAL_MODEL_ENV} must use AzureML model name:version syntax")

    model_name, model_version = (part.strip() for part in model.split(":", 1))
    if not model_name or not model_version:
        pytest.skip(f"{_AML_LEROBOT_EVAL_MODEL_ENV} must include a non-empty AzureML model name and version")

    return aml_lerobot_policy_source_from_model(AmlModelRef(name=model_name, version=model_version))


def submit_aml_lerobot_eval(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    policy_source: AmlLeRobotEvalPolicySource,
    policy_type: str,
    eval_episodes: int,
    eval_batch_size: int,
    blob_storage_account: str,
    blob_container: str,
    blob_prefix: str,
) -> AzureMLJob:
    policy_args = list(policy_source.args)
    policy_description = policy_source.description
    experiment_name = e2e_name("il-eval-e2e-aml")
    log_e2e(
        "Submitting AzureML LeRobot eval job "
        f"for policy={policy_description}, policy_type={policy_type}, eval_episodes={eval_episodes}, "
        f"dataset={blob_storage_account}/{blob_container}/{blob_prefix}, experiment={experiment_name}"
    )
    result = run_command(
        [
            str(repo_root / "evaluation/sil/scripts/submit-azureml-lerobot-eval.sh"),
            *policy_args,
            "--policy-type",
            policy_type,
            "--from-blob",
            "--storage-account",
            blob_storage_account,
            "--storage-container",
            blob_container,
            "--blob-prefix",
            blob_prefix,
            "--eval-episodes",
            str(eval_episodes),
            "--eval-batch-size",
            str(eval_batch_size),
            "--mlflow-enable",
            "--experiment-name",
            experiment_name,
            *_submit_workspace_args(aml_workspace),
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML LeRobot eval e2e submission failed\n\n{format_command_failure(result)}")

    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AzureML LeRobot eval")


def submit_aml_lerobot_pipeline(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    dataset_asset: str,
    dataset_repo_id: str,
    policy_type: str,
    training_steps: int,
    save_freq: int,
    batch_size: int,
    eval_episodes: int,
) -> AzureMLJob:
    experiment_name = e2e_name("il-pipeline-e2e-aml")
    log_e2e(
        "Submitting AzureML LeRobot pipeline job "
        f"for dataset_asset={dataset_asset}, dataset_repo_id={dataset_repo_id}, policy={policy_type}, "
        f"training_steps={training_steps}, save_freq={save_freq}, batch_size={batch_size}, "
        f"eval_episodes={eval_episodes}, experiment={experiment_name}"
    )
    result = run_command(
        [
            str(repo_root / "training/il/scripts/submit-azureml-lerobot-pipeline.sh"),
            "--dataset-asset",
            dataset_asset,
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
            "--eval-episodes",
            str(eval_episodes),
            "--experiment-name",
            experiment_name,
            *_submit_workspace_args(aml_workspace),
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML LeRobot pipeline e2e submission failed\n\n{format_command_failure(result)}")

    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AzureML LeRobot pipeline")


def _aml_job_from_submission(
    result: subprocess.CompletedProcess[str],
    aml_workspace: AzureMLWorkspace,
    experiment_name: str,
    description: str,
) -> AzureMLJob:
    combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    job_name = _parse_azureml_job_name(combined_output)
    if job_name is None:
        raise AssertionError(
            f"Unable to parse {description} job name from submission output\n\n{combined_output.strip()}"
        )
    log_e2e(f"Submitted {description} job name={job_name}")
    return AzureMLJob(name=job_name, workspace=aml_workspace, experiment_name=experiment_name)


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
