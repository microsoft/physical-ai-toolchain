from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from tests.e2e._common import (
    E2EHandle,
    command_tuple,
    e2e_name,
    env_value,
    format_command_failure,
    log_e2e,
    parse_json_from_output,
    run_command,
    wait_for_status,
)
from tests.e2e._environment import (
    INSTANCE_TYPE_VARIABLE,
    LOCAL_ENV_FILE,
    InstanceTypeChoice,
    LocalEnvError,
    instance_type_variables,
    resolve_instance_type,
)

AML_STARTED_STATES = {"Running", "Finalizing", "Completed"}
AML_FAILURE_STATES = {"Canceled", "Cancelled", "Failed", "NotResponding"}
AML_CANCEL_TIMEOUT_SECONDS = 180
# A pipeline whose creation timed out at the Azure ML gateway stays NotStarted with no child
# jobs; healthy pipelines start within seconds, so this window can't mistake one for the other.
AML_ORPHAN_WINDOW_MINUTES = 5
AML_ORPHAN_CANCEL_TIMEOUT_SECONDS = 60
AML_ORPHAN_RESUBMISSIONS = 1
ORPHANED_SUBMISSION = "orphaned-submission"

RL_TRAINING_SCRIPT = "training/rl/scripts/submit-azureml-training.sh"
ISAAC_EVAL_SCRIPT = "evaluation/sil/scripts/submit-azureml-isaaclab-evaluation.sh"
LEROBOT_TRAINING_SCRIPT = "training/il/scripts/submit-azureml-lerobot-training.sh"
LEROBOT_EVAL_SCRIPT = "evaluation/sil/scripts/submit-azureml-lerobot-eval.sh"
VLA_PI0_TRAINING_SCRIPT = "training/vla/scripts/submit-azureml-vla-pi0-training.sh"
GPU_SMOKE_SCRIPT = "training/smoke/scripts/submit-azureml-gpu-smoke.sh"
# The instance type each GPU submission script requests when it gets none; a test keeps these
# in step with the scripts. The pi0 evaluation wrapper delegates to the LeRobot evaluation script.
SCRIPT_DEFAULT_INSTANCE_TYPES: dict[str, str] = {
    RL_TRAINING_SCRIPT: "gpuspot",
    ISAAC_EVAL_SCRIPT: "gpuspot",
    LEROBOT_TRAINING_SCRIPT: "gpuspot",
    LEROBOT_EVAL_SCRIPT: "gpuspot",
    VLA_PI0_TRAINING_SCRIPT: "gpu",
    GPU_SMOKE_SCRIPT: "gpuspot",
}


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
    version: str | None,
) -> None:
    rendered_version = f":{version}" if version is not None else ""
    log_e2e(f"Archiving AzureML {asset_type} {name}{rendered_version}")
    version_args = ["--version", version] if version is not None else []
    result = run_command(
        [
            "az",
            "ml",
            asset_type,
            "archive",
            "--name",
            name,
            *version_args,
            *aml_workspace_args(aml_workspace),
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Failed to archive AzureML {asset_type} {name}{rendered_version}\n\n{format_command_failure(result)}"
        )


@dataclass
class AzureMLJob:
    name: str
    workspace: AzureMLWorkspace
    experiment_name: str
    handle: E2EHandle = field(default_factory=E2EHandle)
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
        if "ModelNotFound" in result.stderr:
            return []
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


def assert_registered_model_has_artifacts(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    model: AmlModelRef,
) -> None:
    """Download a registered model version and assert that it contains files."""
    with tempfile.TemporaryDirectory(prefix="e2e-model-artifacts-") as download_root:
        result = run_command(
            [
                "az",
                "ml",
                "model",
                "download",
                "--name",
                model.name,
                "--version",
                model.version,
                "--download-path",
                download_root,
                *aml_workspace_args(aml_workspace),
                "--output",
                "none",
            ],
            cwd=repo_root,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"Failed to download AzureML model {model.name!r}:{model.version}\n\n{format_command_failure(result)}"
            )
        artifact_files = [path for path in Path(download_root).rglob("*") if path.is_file()]
        if not artifact_files:
            raise AssertionError(f"AzureML model {model.name!r}:{model.version} contained no artifact files")

    log_e2e(f"AzureML model artifacts passed: model={model.name}:{model.version}, artifacts={len(artifact_files)}")


def archive_all_model_versions(repo_root: Path, aml_workspace: AzureMLWorkspace, model_name: str) -> None:
    """Archive every registered version of an AzureML model (best-effort cleanup)."""
    for version in _list_model_versions(repo_root, aml_workspace, model_name):
        archive_aml_asset(repo_root, aml_workspace, "model", model_name, str(version))


def archive_aml_data_asset(repo_root: Path, aml_workspace: AzureMLWorkspace, asset_name: str) -> None:
    result = run_command(
        [
            "az",
            "ml",
            "data",
            "list",
            "--name",
            asset_name,
            *aml_workspace_args(aml_workspace),
            "-o",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        if "container was not found" in result.stderr:
            return
        raise AssertionError(
            f"Failed to list AzureML data asset versions for {asset_name!r}\n\n{format_command_failure(result)}"
        )
    payload = parse_json_from_output(result.stdout)
    if not isinstance(payload, list) or not payload:
        return
    archive_aml_asset(repo_root, aml_workspace, "data", asset_name, None)


def assert_aml_data_asset_exists(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    asset_name: str,
    expected_path: str,
) -> None:
    result = run_command(
        [
            "az",
            "ml",
            "data",
            "show",
            "--name",
            asset_name,
            "--label",
            "latest",
            *aml_workspace_args(aml_workspace),
            "-o",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"AzureML data asset {asset_name!r} was not registered\n\n{format_command_failure(result)}"
        )
    payload = parse_json_from_output(result.stdout)
    if not isinstance(payload, Mapping):
        raise AssertionError(f"AzureML data asset {asset_name!r} payload was not a JSON object")
    actual_path = payload.get("path")
    if actual_path != expected_path:
        raise AssertionError(f"AzureML data asset {asset_name!r} had path {actual_path!r}, expected {expected_path!r}")
    log_e2e(f"AzureML data asset passed: name={asset_name}, path={actual_path}")


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


@dataclass(frozen=True)
class AzureMLCompute:
    """A compute target and, for a Kubernetes compute, the instance types it defines.

    ``instance_types`` is ``None`` when the compute doesn't report any, as for managed AmlCompute.
    """

    name: str
    instance_types: frozenset[str] | None = None
    gpu_instance_types: frozenset[str] = frozenset()


def _instance_type_has_gpu(spec: object) -> bool:
    resources = spec.get("resources") if isinstance(spec, Mapping) else None
    if not isinstance(resources, Mapping):
        return False
    for section in ("limits", "requests"):
        values = resources.get(section)
        # Azure ML reports CPU-only instance types with a null GPU count.
        count = str(values.get("nvidia.com/gpu") or "").strip() if isinstance(values, Mapping) else ""
        if count.isdigit() and int(count) > 0:
            return True
    return False


def aml_compute_from_payload(name: str, payload: Mapping[str, Any]) -> AzureMLCompute:
    """Build an ``AzureMLCompute`` from ``az ml compute show`` JSON without keeping its other properties."""
    properties = payload.get("properties")
    types = properties.get("instance_types") if isinstance(properties, Mapping) else None
    if payload.get("type") != "kubernetes" or not isinstance(types, Mapping):
        return AzureMLCompute(name)
    gpu_types = frozenset(str(type_name) for type_name, spec in types.items() if _instance_type_has_gpu(spec))
    return AzureMLCompute(name, frozenset(str(type_name) for type_name in types), gpu_types)


def instance_type_problem(
    choice: InstanceTypeChoice, compute: AzureMLCompute, *, category: str, scripts: Sequence[str]
) -> str | None:
    """Explain why a compute can't run a category's GPU jobs with this choice, or return ``None``."""
    if compute.instance_types is None:
        return None
    category_variable = instance_type_variables(category)[0]
    if compute.gpu_instance_types:
        available = f"one of its GPU instance types: {', '.join(sorted(compute.gpu_instance_types))}"
    else:
        defined = ", ".join(sorted(compute.instance_types)) or "no types"
        available = f"a GPU instance type; it defines none, only {defined}"
    fix = f"Set {category_variable} or {INSTANCE_TYPE_VARIABLE} in {LOCAL_ENV_FILE} to {available}."
    if choice.value == "":
        return (
            f"{choice.variable} is empty, which omits the instance type. Only managed AmlCompute supports that; "
            f"on Kubernetes compute {compute.name} the jobs would run without a GPU. {fix}"
        )
    if choice.value:
        if choice.value in compute.gpu_instance_types:
            return None
        defined = "defines without a GPU" if choice.value in compute.instance_types else "doesn't define"
        return (
            f"{choice.variable} in {choice.source} requests instance type {choice.value}, which compute "
            f"{compute.name} {defined}. {fix}"
        )
    missing = sorted({SCRIPT_DEFAULT_INSTANCE_TYPES[script] for script in scripts} - compute.gpu_instance_types)
    if not missing:
        return None
    script_names = ", ".join(Path(script).name for script in scripts)
    return (
        f"Compute {compute.name} has no GPU instance type named {', '.join(missing)}, the default of "
        f"{script_names}, and no instance type is set for {category}. {fix}"
    )


def require_gpu_instance_type(
    compute: AzureMLCompute, repo_root: Path, *, category: str, scripts: Sequence[str]
) -> str | None:
    """Choose a category's GPU instance type and confirm the compute can run it, before any submission.

    Returns the value to pass to the submit helpers: a name, ``None`` for the scripts' defaults, or
    an empty string to omit the instance type on a compute that reports no instance types.
    """
    try:
        choice = resolve_instance_type(repo_root, category, os.environ)
    except LocalEnvError as error:
        pytest.fail(f"Can't choose a GPU instance type for {category}: {error}", pytrace=False)
    problem = instance_type_problem(choice, compute, category=category, scripts=scripts)
    if problem:
        pytest.fail(problem, pytrace=False)
    log_e2e(f"GPU instance type for the {category} jobs: {choice.describe()}")
    return choice.value


def _instance_type_args(instance_type: str | None) -> list[str]:
    return [] if instance_type is None else ["--instance-type", instance_type]


def _instance_type_label(instance_type: str | None) -> str:
    if instance_type is None:
        return "<script default>"
    return instance_type or "<managed-compute>"


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
    register_model_name: str,
    instance_type: str | None,
) -> AzureMLJob:
    experiment_name = e2e_name("rl-training-e2e-aml")
    log_e2e(
        "Submitting AzureML training job "
        f"for task={task}, num_envs={num_envs}, max_iterations={max_iterations}, experiment={experiment_name}, "
        f"instance_type={_instance_type_label(instance_type)}"
    )
    result = run_command(
        [
            str(repo_root / RL_TRAINING_SCRIPT),
            "--task",
            task,
            "--max-iterations",
            str(max_iterations),
            "--num-envs",
            str(num_envs),
            "--experiment-name",
            experiment_name,
            *_instance_type_args(instance_type),
            *_submit_workspace_args(aml_workspace),
            "--register-checkpoint",
            register_model_name,
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
    instance_type: str | None,
) -> AzureMLJob:
    experiment_name = e2e_name("il-training-e2e-aml")
    log_e2e(
        "Submitting AzureML LeRobot training job "
        f"for dataset={blob_url}, policy={policy_type}, training_steps={training_steps}, "
        f"save_freq={save_freq}, batch_size={batch_size}, log_freq={log_freq}, experiment={experiment_name}, "
        f"instance_type={_instance_type_label(instance_type)}"
    )
    # eval-freq > training-steps disables in-loop evaluation (which would need
    # sim deps that are not part of the lerobot training container).
    result = run_command(
        [
            str(repo_root / LEROBOT_TRAINING_SCRIPT),
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
            *_instance_type_args(instance_type),
            *_submit_workspace_args(aml_workspace),
            "--register-checkpoint",
            register_model_name,
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML LeRobot e2e submission failed\n\n{format_command_failure(result)}")

    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AzureML LeRobot training")


def submit_aml_vla_pi0_training(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    blob_url: str,
    training_steps: int,
    save_freq: int,
    batch_size: int,
    log_freq: int,
    register_model_name: str,
    instance_type: str | None,
) -> AzureMLJob:
    experiment_name = e2e_name("vla-pi0-training-e2e-aml")
    log_e2e(
        "Submitting AzureML VLA pi0 training job "
        f"for dataset={blob_url}, training_steps={training_steps}, "
        f"save_freq={save_freq}, batch_size={batch_size}, log_freq={log_freq}, experiment={experiment_name}, "
        f"instance_type={_instance_type_label(instance_type)}"
    )
    result = run_command(
        [
            str(repo_root / VLA_PI0_TRAINING_SCRIPT),
            "--blob-url",
            blob_url,
            "--policy-type",
            "pi0",
            "--training-steps",
            str(training_steps),
            "--save-freq",
            str(save_freq),
            "--batch-size",
            str(batch_size),
            "--log-freq",
            str(log_freq),
            "--eval-freq",
            str(training_steps + 1),
            *_instance_type_args(instance_type),
            "--train-expert-only",
            "--experiment-name",
            experiment_name,
            *_submit_workspace_args(aml_workspace),
            "--register-checkpoint",
            register_model_name,
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML VLA pi0 e2e submission failed\n\n{format_command_failure(result)}")

    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AzureML VLA pi0 training")


_AML_LEROBOT_EVAL_MODEL_ENV = "E2E_AML_LEROBOT_EVAL_MODEL"
_AML_ISAAC_EVAL_MODEL_ENV = "E2E_AML_ISAAC_EVAL_MODEL"


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


def _resolve_aml_model_env(env_var: str) -> AmlModelRef | None:
    model = env_value(env_var)
    if not model:
        return None
    if ":" not in model:
        pytest.skip(f"{env_var} must use AzureML model name:version syntax")
    model_name, model_version = (part.strip() for part in model.split(":", 1))
    if not model_name or not model_version:
        pytest.skip(f"{env_var} must include a non-empty AzureML model name and version")
    return AmlModelRef(name=model_name, version=model_version)


def resolve_aml_lerobot_eval_policy_override() -> AmlLeRobotEvalPolicySource | None:
    """Resolve an eval policy source from the environment, or ``None`` when unset.

    ``submit-azureml-lerobot-eval.sh`` has no ``--builtin-policy`` option, so a real
    policy must be supplied. Configure:

    - ``E2E_AML_LEROBOT_EVAL_MODEL`` — an AzureML model ``name:version``.

    Returns ``None`` when this is unset: the lifecycle test provisions a freshly
    trained model instead. Malformed values skip.
    """
    ref = _resolve_aml_model_env(_AML_LEROBOT_EVAL_MODEL_ENV)
    return None if ref is None else aml_lerobot_policy_source_from_model(ref)


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
    instance_type: str | None,
) -> AzureMLJob:
    """Submit a LeRobot eval job; ``instance_type`` of ``None`` keeps the script's default instance type."""
    policy_args = list(policy_source.args)
    policy_description = policy_source.description
    experiment_name = e2e_name("il-eval-e2e-aml")
    submission_script = (
        "submit-azureml-vla-pi0-eval.sh" if policy_type in {"pi0", "pi0_fast"} else "submit-azureml-lerobot-eval.sh"
    )
    log_e2e(
        "Submitting AzureML LeRobot eval job "
        f"for policy={policy_description}, policy_type={policy_type}, eval_episodes={eval_episodes}, "
        f"dataset={blob_storage_account}/{blob_container}/{blob_prefix}, experiment={experiment_name}, "
        f"instance_type={_instance_type_label(instance_type)}"
    )
    result = run_command(
        [
            str(repo_root / "evaluation/sil/scripts" / submission_script),
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
            *_instance_type_args(instance_type),
            *_submit_workspace_args(aml_workspace),
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML LeRobot eval e2e submission failed\n\n{format_command_failure(result)}")

    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AzureML LeRobot eval")


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value}")


def _load_strict_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, parse_constant=_reject_non_finite_json)


def _find_downloaded_output_file(download_root: Path, filename: str) -> Path:
    matches = list(download_root.rglob(filename))
    if len(matches) != 1:
        rendered = ", ".join(str(path.relative_to(download_root)) for path in matches) or "<none>"
        raise AssertionError(f"Expected exactly one {filename!r} in AzureML eval output, found: {rendered}")
    return matches[0]


def assert_aml_lerobot_eval_artifact_contract(job: AzureMLJob, *, eval_episodes: int) -> None:
    """Download and validate the policy-independent replay evaluation artifacts."""
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential

    client = MLClient(
        credential=DefaultAzureCredential(),
        subscription_id=job.workspace.subscription_id,
        resource_group_name=job.workspace.resource_group,
        workspace_name=job.workspace.workspace_name,
    )

    with tempfile.TemporaryDirectory(prefix=f"{job.name}-eval-output-") as download_dir:
        download_root = Path(download_dir)
        client.jobs.download(name=job.name, download_path=str(download_root), output_name="eval_results")

        metrics = _load_strict_json(_find_downloaded_output_file(download_root, "metrics.json"))
        results = _load_strict_json(_find_downloaded_output_file(download_root, "eval_results.json"))
        failure_cases_path = _find_downloaded_output_file(download_root, "failure_cases.jsonl")

        assert isinstance(metrics, dict)
        assert set(metrics) == {
            "evaluation_schema_version",
            "aggregate_verdict",
            "baseline_model_version",
            "metrics",
        }
        assert metrics["evaluation_schema_version"] == 1
        assert metrics["aggregate_verdict"] == "pass"
        assert metrics["baseline_model_version"] == "none"

        metric_entries = metrics["metrics"]
        assert isinstance(metric_entries, list)
        assert {entry["name"] for entry in metric_entries} == {
            "action_accuracy_l1",
            "action_accuracy_l2",
            "inference_latency_mean_ms",
            "throughput_hz",
        }
        for entry in metric_entries:
            assert set(entry) == {
                "name",
                "value",
                "absolute_threshold",
                "absolute_verdict",
                "baseline_value",
                "regression_pct",
                "regression_verdict",
            }
            assert isinstance(entry["value"], int | float)
            assert entry["absolute_threshold"] is None
            assert entry["absolute_verdict"] == "pass"
            assert entry["baseline_value"] is None
            assert entry["regression_pct"] == 0.0
            assert entry["regression_verdict"] == "skipped"

        assert isinstance(results, dict)
        assert set(results) == {
            "job_name",
            "policy_repo_id",
            "policy_type",
            "dataset_repo_id",
            "device",
            "episodes_evaluated",
            "aggregate_mse",
            "aggregate_mae",
            "aggregate_avg_inference_ms",
            "aggregate_throughput_hz",
            "per_episode",
            "status",
        }
        assert results["status"] == "completed"
        assert results["episodes_evaluated"] == eval_episodes
        assert len(results["per_episode"]) == eval_episodes

        for line in failure_cases_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line, parse_constant=_reject_non_finite_json)
            assert set(record) == {
                "evaluation_schema_version",
                "episode_id",
                "dataset_id",
                "dataset_version",
                "domain_category",
                "model_version",
                "artifact_refs",
                "failure_mode",
                "metric_values",
                "metric_thresholds_violated",
            }
            assert record["evaluation_schema_version"] == 1
            assert record["failure_mode"] == "rollout_error"

    log_e2e(f"AzureML LeRobot eval artifact contract passed for job {job.name}")


def resolve_aml_isaac_eval_model_override() -> AmlModelRef | None:
    """Resolve an AzureML eval model from the environment, or ``None`` when unset."""
    return _resolve_aml_model_env(_AML_ISAAC_EVAL_MODEL_ENV)


def submit_aml_isaaclab_eval(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    model: AmlModelRef,
    task: str,
    eval_episodes: int,
    num_envs: int,
    instance_type: str | None,
) -> AzureMLJob:
    """Submit the AzureML Isaac Lab evaluation against a concrete registered model."""
    experiment_name = e2e_name("rl-eval-e2e-aml")
    log_e2e(
        "Submitting AzureML Isaac Lab eval job "
        f"for model={model.name}:{model.version}, task={task}, eval_episodes={eval_episodes}, num_envs={num_envs}, "
        f"experiment={experiment_name}, instance_type={_instance_type_label(instance_type)}"
    )
    result = run_command(
        [
            str(repo_root / ISAAC_EVAL_SCRIPT),
            "--model-name",
            model.name,
            "--model-version",
            model.version,
            "--task",
            task,
            "--eval-episodes",
            str(eval_episodes),
            "--num-envs",
            str(num_envs),
            # The e2e validates the submission + Isaac Lab eval runtime, not policy
            # quality, so a 0.0 threshold accepts any success rate and the job
            # completes regardless of the reference policy's score.
            "--success-threshold",
            "0.0",
            "--experiment-name",
            experiment_name,
            *_instance_type_args(instance_type),
            *_submit_workspace_args(aml_workspace),
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"AzureML Isaac Lab eval e2e submission failed\n\n{format_command_failure(result)}")

    return _aml_job_from_submission(result, aml_workspace, experiment_name, "AzureML Isaac Lab eval")


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
    register_model_name: str | None = None,
) -> AzureMLJob:
    experiment_name = e2e_name("il-pipeline-e2e-aml")
    register_args = (
        ["--with-register", "--register-model-name", register_model_name] if register_model_name is not None else []
    )
    log_e2e(
        "Submitting AzureML LeRobot pipeline job "
        f"for dataset_asset={dataset_asset}, dataset_repo_id={dataset_repo_id}, policy={policy_type}, "
        f"training_steps={training_steps}, save_freq={save_freq}, batch_size={batch_size}, "
        f"eval_episodes={eval_episodes}, register_model_name={register_model_name}, experiment={experiment_name}"
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
            *register_args,
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
    *,
    handle: E2EHandle | None = None,
    expected_job_name: str | None = None,
) -> AzureMLJob:
    combined_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    job_name = _parse_azureml_job_name(combined_output)
    if job_name is None:
        raise AssertionError(
            f"Unable to parse {description} job name from submission output\n\n{combined_output.strip()}"
        )
    if expected_job_name is not None and job_name != expected_job_name:
        raise AssertionError(f"{description} returned job name {job_name!r}, expected {expected_job_name!r}")
    log_e2e(f"Submitted {description} job name={job_name}")
    active_handle = handle or E2EHandle()
    active_handle.submission_commands.append(command_tuple(result.args))
    active_handle.resource_identifiers["azureml_job"] = job_name
    active_handle.attempts["azureml_job"] = ["initial"]
    active_handle.retry_classifications["azureml_job"] = "none"
    return AzureMLJob(
        name=job_name,
        workspace=aml_workspace,
        experiment_name=experiment_name,
        handle=active_handle,
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


def fetch_aml_job_logs(job: AzureMLJob, repo_root: Path) -> str:
    with tempfile.TemporaryDirectory(prefix=f"e2e-aml-logs-{job.name}-") as download_root:
        result = run_command(
            [
                "az",
                "ml",
                "job",
                "download",
                "--name",
                job.name,
                "--all",
                "--download-path",
                download_root,
                *aml_workspace_args(job.workspace),
            ],
            cwd=repo_root,
        )
        if result.returncode != 0:
            raise AssertionError(f"Unable to fetch AzureML job {job.name!r} logs\n\n{format_command_failure(result)}")

        user_logs = sorted(Path(download_root).rglob("user_logs/*.txt"))
        logs = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in user_logs)
        if not logs:
            raise AssertionError(f"AzureML job {job.name!r} did not produce downloadable user logs")

    job.handle.logs["azureml_job"] = logs
    return logs


def _mark_job_terminal(job: AzureMLJob, terminal_status: str) -> None:
    job.is_terminal = True
    job.terminal_status = terminal_status
    job.handle.terminal_states["azureml_job"] = terminal_status


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


def _run_with_time_limit(
    args: list[str], *, cwd: Path, timeout_seconds: float
) -> subprocess.CompletedProcess[str] | None:
    """Run a command in its own session; stop the session and return None if it outlives the limit."""
    process = subprocess.Popen(
        args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except BaseException as error:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        if isinstance(error, subprocess.TimeoutExpired):
            return None
        raise
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def cancel_aml_job(job: AzureMLJob, repo_root: Path, *, timeout_seconds: float | None = None) -> None:
    if job.is_terminal:
        log_e2e(f"Skipping cancel for AzureML job {job.name}; terminal status={job.terminal_status}")
        return

    log_e2e(f"Cancelling AzureML job {job.name}")
    limit = AML_CANCEL_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds

    # The CLI waits for the service to finish cancelling, which never happens for a job the
    # pipeline service didn't accept, so bound the wait; the accepted request still applies.
    result = _run_with_time_limit(
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
        timeout_seconds=limit,
    )
    if result is None:
        log_e2e(f"Cancel request for AzureML job {job.name} did not return within {limit}s; continuing cleanup")


def list_aml_child_jobs(job: AzureMLJob, repo_root: Path) -> list[str]:
    """Return the names of a pipeline job's child jobs."""
    result = run_command(
        [
            "az",
            "ml",
            "job",
            "list",
            *aml_workspace_args(job.workspace),
            "--parent-job-name",
            job.name,
            "--query",
            "[].name",
            "-o",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Unable to list child jobs of AzureML job {job.name!r}\n\n{format_command_failure(result)}"
        )
    payload = parse_json_from_output(result.stdout)
    if not isinstance(payload, list):
        raise AssertionError(f"AzureML child job list for {job.name!r} was not a JSON array")
    return [name for name in payload if isinstance(name, str)]


def archive_aml_job(job: AzureMLJob, repo_root: Path) -> None:
    """Hide a job from default job lists; a failure is logged, because archiving is only cleanup."""
    result = run_command(
        ["az", "ml", "job", "archive", *aml_workspace_args(job.workspace), "--name", job.name],
        cwd=repo_root,
    )
    if result.returncode == 0:
        log_e2e(f"Archived AzureML job {job.name}")
    else:
        log_e2e(f"Could not archive AzureML job {job.name}; continuing\n\n{format_command_failure(result)}")


def _wait_until_started_or_orphaned(
    job: AzureMLJob,
    repo_root: Path,
    *,
    timeout_minutes: int,
    poll_interval_seconds: int,
) -> bool:
    """Wait for a pipeline to start; return False when Azure ML created it without ever starting it."""
    window = min(AML_ORPHAN_WINDOW_MINUTES, timeout_minutes)
    try:
        wait_until_aml_started(job, repo_root, timeout_minutes=window, poll_interval_seconds=poll_interval_seconds)
        return True
    except AssertionError:
        status = _aml_status(fetch_aml_job_payload(job, repo_root))
        if status in AML_FAILURE_STATES:
            raise
        if status == "NotStarted" and not list_aml_child_jobs(job, repo_root):
            return False
        if timeout_minutes <= window:
            raise
    log_e2e(f"AzureML pipeline job {job.name} hasn't started yet but has child jobs; still waiting")
    wait_until_aml_started(
        job, repo_root, timeout_minutes=timeout_minutes - window, poll_interval_seconds=poll_interval_seconds
    )
    return True


def _retire_orphaned_job(job: AzureMLJob, repo_root: Path) -> None:
    log_e2e(
        f"AzureML pipeline job {job.name} is still NotStarted with no child jobs after "
        f"{AML_ORPHAN_WINDOW_MINUTES} minutes, so Azure ML created it without starting it; "
        "cancelling and archiving it"
    )
    cancel_aml_job(job, repo_root, timeout_seconds=AML_ORPHAN_CANCEL_TIMEOUT_SECONDS)
    archive_aml_job(job, repo_root)
    _mark_job_terminal(job, "Orphaned")


def start_aml_pipeline(
    submit: Callable[[], AzureMLJob],
    repo_root: Path,
    *,
    on_submitted: Callable[[AzureMLJob], object],
    timeout_minutes: int,
    poll_interval_seconds: int,
) -> AzureMLJob:
    """Submit an AzureML pipeline and wait for it to start, resubmitting once if Azure ML orphans it.

    When pipeline creation times out at the Azure ML gateway, the CLI retries and returns a job
    record that never starts. ``on_submitted`` receives every job as soon as it exists, so the
    caller can register cleanup before any wait.
    """
    orphans: list[AzureMLJob] = []
    while True:
        job = submit()
        on_submitted(job)
        if orphans:
            job.handle.attempts["azureml_job"] = [
                "initial",
                *(f"orphaned-resubmit-{attempt}" for attempt in range(1, len(orphans) + 1)),
            ]
            job.handle.retry_classifications["azureml_job"] = ORPHANED_SUBMISSION
        if _wait_until_started_or_orphaned(
            job, repo_root, timeout_minutes=timeout_minutes, poll_interval_seconds=poll_interval_seconds
        ):
            return job
        _retire_orphaned_job(job, repo_root)
        orphans.append(job)
        if len(orphans) > AML_ORPHAN_RESUBMISSIONS:
            names = ", ".join(orphan.name for orphan in orphans)
            raise AssertionError(
                f"Azure ML created pipeline jobs {names} but never started them: each stayed NotStarted with no "
                f"child jobs for {AML_ORPHAN_WINDOW_MINUTES} minutes. Pipeline creation probably timed out at the "
                "Azure ML gateway; look for a GatewayTimeout on "
                "Microsoft.MachineLearningServices/workspaces/jobs/write in the workspace Activity Log, then rerun."
            )
        log_e2e(f"Resubmitting the AzureML pipeline after orphaned job {job.name}")


def cleanup_aml_job_and_model_versions(
    job: AzureMLJob,
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    model_name: str,
) -> None:
    """Cancel an AzureML job, then archive every model version it registered, even if it never stops."""
    cancel_aml_job(job, repo_root)
    try:
        if not job.is_terminal:
            terminal_status = wait_for_status(
                lambda: _aml_status(fetch_aml_job_payload(job, repo_root)),
                goal_description=f"AzureML job {job.name} cleanup",
                timeout_minutes=10,
                poll_interval_seconds=15,
                success_statuses={"Completed", *AML_FAILURE_STATES},
                status_log_prefix="Cleanup poll status",
            )
            _mark_job_terminal(job, terminal_status)
    finally:
        archive_all_model_versions(repo_root, aml_workspace, model_name)
