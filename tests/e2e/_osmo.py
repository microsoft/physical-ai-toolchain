from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.e2e._aml import AzureMLWorkspace
from tests.e2e._common import (
    e2e_name,
    env_value,
    format_command_failure,
    log_e2e,
    parse_json_from_output,
    run_command,
    wait_for_status,
)

OSMO_STARTED_STATES = {"RUNNING", "COMPLETED", "SUCCEEDED"}
OSMO_FAILURE_PREFIXES = ("FAILED",)
OSMO_FAILURE_STATES = {"CANCELLED", "CANCELED", "ERROR"}

# A workflow only reports RUNNING once its task container is live, so the "started"
# wait must absorb a full cold scale-from-zero path: GPU node provisioning, NVIDIA
# GPU-operator cold-start, and the isaac-lab image pull.
OSMO_STARTED_TIMEOUT_MINUTES = 60
OSMO_POLL_INTERVAL_SECONDS = 30

OSMO_WORKFLOWS_NAMESPACE = "osmo-workflows"
_POD_LOG_POLL_INTERVAL_SECONDS = 5


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
            # Smoke-sized to fit a single Standard_NC24ads_A100_v4 GPU node
            # (24 vCPU / 220 GiB); the script default targets production headroom.
            "--cpu",
            "20",
            "--memory",
            "180Gi",
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
    timeout_minutes: int = OSMO_STARTED_TIMEOUT_MINUTES,
    poll_interval_seconds: int = OSMO_POLL_INTERVAL_SECONDS,
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
    poll_interval_seconds: int = OSMO_POLL_INTERVAL_SECONDS,
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


@dataclass(frozen=True)
class _TaskPod:
    name: str
    phase: str
    container_state: str
    started: bool
    terminated: bool
    exit_code: int | None


def _pod_created_at(item: Mapping[str, Any]) -> str:
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        created = metadata.get("creationTimestamp")
        if isinstance(created, str):
            return created
    return ""


def _container_state_summary(state: Mapping[str, Any]) -> tuple[str, bool, bool, int | None]:
    terminated = state.get("terminated")
    if isinstance(terminated, Mapping):
        reason = terminated.get("reason") or "Terminated"
        exit_code = terminated.get("exitCode")
        exit_code = exit_code if isinstance(exit_code, int) else None
        rendered = f"terminated({reason}, exit_code={exit_code})" if exit_code is not None else f"terminated({reason})"
        return rendered, True, True, exit_code

    if isinstance(state.get("running"), Mapping):
        return "running", True, False, None

    waiting = state.get("waiting")
    if isinstance(waiting, Mapping):
        return f"waiting({waiting.get('reason') or 'Waiting'})", False, False, None

    return "unknown", False, False, None


def _task_pod_from_item(item: Mapping[str, Any], task_name: str) -> _TaskPod:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
    name = metadata.get("name") if isinstance(metadata.get("name"), str) else "<unknown>"
    phase = status.get("phase") if isinstance(status.get("phase"), str) else "UNKNOWN"

    container_state, started, terminated, exit_code = "unknown", False, False, None
    container_statuses = status.get("containerStatuses")
    if isinstance(container_statuses, list):
        for entry in container_statuses:
            if isinstance(entry, Mapping) and entry.get("name") == task_name:
                state = entry.get("state")
                if isinstance(state, Mapping):
                    container_state, started, terminated, exit_code = _container_state_summary(state)
                break

    return _TaskPod(
        name=name,
        phase=phase,
        container_state=container_state,
        started=started,
        terminated=terminated,
        exit_code=exit_code,
    )


class TaskPodLogStream:
    """
    Monitors and streams the Kubernetes pod logs for a single OSMO workflow task.

    Runs a background thread that resolves the task pod via OSMO pod labels
    (``osmo.workflow_id`` + ``osmo.task_name``), logs pod phase and container-state
    transitions, and streams ``kubectl logs -f`` for the task container to the console.
    The pod is not scheduled immediately, so the thread polls the container state and
    only attaches once it is running or terminated. Streaming is best-effort: failures
    never raise, so it cannot break the test it observes.
    """

    def __init__(
        self,
        workflow: OSMOWorkflow,
        repo_root: Path,
        task_name: str,
        *,
        namespace: str,
        poll_interval_seconds: int,
    ) -> None:
        self._workflow = workflow
        self._repo_root = repo_root
        self._task_name = task_name
        self._namespace = namespace
        self._poll_interval_seconds = poll_interval_seconds
        self._stop = threading.Event()
        self._proc_lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._thread = threading.Thread(target=self._run, name=f"osmo-logs-{task_name}", daemon=True)

    def start(self) -> TaskPodLogStream:
        self._thread.start()
        return self

    def stop(self, *, timeout_seconds: float = 30.0) -> None:
        self._stop.set()
        with self._proc_lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self._thread.join(timeout=timeout_seconds)

    def _run(self) -> None:
        if shutil.which("kubectl") is None:
            log_e2e(f"kubectl unavailable; skipping pod log streaming for OSMO task {self._task_name!r}")
            return

        streamed: set[str] = set()
        reported: dict[str, str] = {}
        while not self._stop.is_set():
            pod = self._latest_task_pod()
            if pod is None:
                if self._stop.wait(self._poll_interval_seconds):
                    return
                continue

            signature = f"{pod.phase}|{pod.container_state}"
            if reported.get(pod.name) != signature:
                log_e2e(
                    f"OSMO task {self._task_name} pod {pod.name}: phase={pod.phase}, container={pod.container_state}"
                )
                reported[pod.name] = signature

            if pod.started and pod.name not in streamed:
                streamed.add(pod.name)
                self._follow(pod.name)
                continue

            if pod.terminated and pod.name in streamed:
                return

            if self._stop.wait(self._poll_interval_seconds):
                return

    def _latest_task_pod(self) -> _TaskPod | None:
        selector = f"osmo.workflow_id={self._workflow.workflow_id},osmo.task_name={self._task_name}"
        result = run_command(
            ["kubectl", "get", "pods", "-n", self._namespace, "-l", selector, "-o", "json"],
            cwd=self._repo_root,
        )
        if result.returncode != 0:
            return None

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None

        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return None

        newest = max(items, key=_pod_created_at)
        return _task_pod_from_item(newest, self._task_name)

    def _follow(self, pod_name: str) -> None:
        log_e2e(f"Streaming logs for OSMO task {self._task_name} (pod {pod_name})")
        try:
            proc = subprocess.Popen(
                ["kubectl", "logs", "-f", "-n", self._namespace, pod_name, "-c", self._task_name],
                cwd=str(self._repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            log_e2e(f"Failed to stream logs for pod {pod_name}: {error}")
            return

        with self._proc_lock:
            self._proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                print(f"[pod {pod_name}] {line.rstrip()}", flush=True)
                if self._stop.is_set():
                    break
        finally:
            with self._proc_lock:
                self._proc = None
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


def start_task_pod_log_stream(
    workflow: OSMOWorkflow,
    repo_root: Path,
    task_name: str,
    *,
    namespace: str = OSMO_WORKFLOWS_NAMESPACE,
    poll_interval_seconds: int = _POD_LOG_POLL_INTERVAL_SECONDS,
) -> TaskPodLogStream:
    return TaskPodLogStream(
        workflow,
        repo_root,
        task_name,
        namespace=namespace,
        poll_interval_seconds=poll_interval_seconds,
    ).start()


def _osmo_workflow_from_submission(
    result: subprocess.CompletedProcess[str],
    experiment_name: str,
    description: str,
    *,
    correlation_id: str = "",
) -> OSMOWorkflow:
    payload = parse_json_from_output("\n".join(part for part in (result.stdout, result.stderr) if part))
    workflow_id = _find_first_string(payload, ("workflow_id", "workflowId", "id", "name"))
    workflow_name = _find_first_string(payload, ("name", "workflow_name", "workflowName", "display_name"))
    if workflow_id is None:
        raise AssertionError(f"Unable to parse OSMO workflow ID from {description} output\n\n{result.stdout.strip()}")
    if workflow_name is None:
        workflow_name = workflow_id

    log_e2e(f"Submitted {description} workflow id={workflow_id}, name={workflow_name}")

    return OSMOWorkflow(
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        experiment_name=experiment_name,
        correlation_id=correlation_id,
    )


def submit_osmo_lerobot_training(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    blob_url: str,
    policy_type: str,
    training_steps: int,
    save_freq: int,
    batch_size: int,
    learning_rate: str,
    log_freq: int,
) -> OSMOWorkflow:
    experiment_name = e2e_name("il-training-e2e-osmo")
    log_e2e(
        "Submitting OSMO LeRobot training workflow "
        f"for dataset={blob_url}, policy={policy_type}, training_steps={training_steps}, "
        f"save_freq={save_freq}, batch_size={batch_size}, learning_rate={learning_rate}, "
        f"log_freq={log_freq}, experiment={experiment_name}"
    )
    # eval-freq > training-steps disables in-loop evaluation, which needs sim deps absent from the training container.
    result = run_command(
        [
            str(repo_root / "training/il/scripts/submit-osmo-lerobot-training.sh"),
            "--blob-url",
            blob_url,
            "--policy-type",
            policy_type,
            "--training-steps",
            str(training_steps),
            "--batch-size",
            str(batch_size),
            "--save-freq",
            str(save_freq),
            "--log-freq",
            str(log_freq),
            "--eval-freq",
            str(training_steps + 1),
            "--learning-rate",
            learning_rate,
            "--experiment-name",
            experiment_name,
            "--azure-subscription-id",
            aml_workspace.subscription_id,
            "--azure-resource-group",
            aml_workspace.resource_group,
            "--azure-workspace-name",
            aml_workspace.workspace_name,
            "--",
            "--format-type",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"OSMO LeRobot training e2e submission failed\n\n{format_command_failure(result)}")

    return _osmo_workflow_from_submission(result, experiment_name, "OSMO LeRobot training")


_LEROBOT_EVAL_MODEL_ENV = "E2E_LEROBOT_EVAL_MODEL"
_LEROBOT_EVAL_POLICY_REPO_ENV = "E2E_LEROBOT_EVAL_POLICY_REPO_ID"


def _lerobot_eval_model_source_args() -> tuple[list[str], str]:
    policy_repo_id = env_value(_LEROBOT_EVAL_POLICY_REPO_ENV)
    if policy_repo_id:
        return ["--policy-repo-id", policy_repo_id], f"HuggingFace policy repo {policy_repo_id}"

    model = env_value(_LEROBOT_EVAL_MODEL_ENV)
    if not model:
        # Default: mint a base policy in-container from LeRobot's built-in ACT
        # architecture, sized to the synthetic dataset. Keeps the eval self-contained
        # (no external policy / HuggingFace token). Override via the env vars above.
        return ["--builtin-policy"], "LeRobot built-in (minted base policy, default)"
    if ":" not in model:
        pytest.skip(f"{_LEROBOT_EVAL_MODEL_ENV} must use AzureML model name:version syntax")

    model_name, model_version = (part.strip() for part in model.split(":", 1))
    if not model_name or not model_version:
        pytest.skip(f"{_LEROBOT_EVAL_MODEL_ENV} must include a non-empty AzureML model name and version")

    return [
        "--from-aml-model",
        "--model-name",
        model_name,
        "--model-version",
        model_version,
    ], f"AzureML model {model_name}:{model_version}"


def submit_osmo_lerobot_eval(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    policy_type: str,
    eval_episodes: int,
    eval_batch_size: int,
    blob_storage_account: str,
    blob_container: str,
    blob_prefix: str,
) -> OSMOWorkflow:
    model_args, model_description = _lerobot_eval_model_source_args()
    dataset_args = [
        "--from-blob-dataset",
        "--storage-account",
        blob_storage_account,
        "--storage-container",
        blob_container,
        "--blob-prefix",
        blob_prefix,
    ]
    dataset_description = f"Azure Blob dataset {blob_storage_account}/{blob_container}/{blob_prefix}"
    experiment_name = e2e_name("lerobot-eval-e2e-osmo")
    job_name = e2e_name("lerobot-eval")
    log_e2e(
        "Submitting OSMO LeRobot eval workflow "
        f"for policy_type={policy_type}, model={model_description}, dataset={dataset_description}, "
        f"experiment={experiment_name}, job={job_name}"
    )
    result = run_command(
        [
            str(repo_root / "evaluation/sil/scripts/submit-osmo-lerobot-eval.sh"),
            "--workflow",
            str(repo_root / "evaluation/sil/workflows/osmo/lerobot-eval.yaml"),
            *model_args,
            *dataset_args,
            "--policy-type",
            policy_type,
            "--eval-episodes",
            str(eval_episodes),
            "--eval-batch-size",
            str(eval_batch_size),
            "--experiment-name",
            experiment_name,
            "--mlflow-enable",
            "--azure-subscription-id",
            aml_workspace.subscription_id,
            "--azure-resource-group",
            aml_workspace.resource_group,
            "--azure-workspace-name",
            aml_workspace.workspace_name,
            "--job-name",
            job_name,
            "--",
            "--format-type",
            "json",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(f"OSMO LeRobot eval e2e submission failed\n\n{format_command_failure(result)}")

    return _osmo_workflow_from_submission(result, experiment_name, "OSMO LeRobot eval", correlation_id=experiment_name)


_VLA_DATASET_BLOB_URL_ENV = "E2E_VLA_DATASET_BLOB_URL"
_VLA_VERSION_ENV = "E2E_VLA_VERSION"
_VLA_BASE_MODEL_ENV = "E2E_VLA_BASE_MODEL"
_VLA_DATA_CONFIG_ENV = "E2E_VLA_DATA_CONFIG"
_VLA_EMBODIMENT_TAG_ENV = "E2E_VLA_EMBODIMENT_TAG"
_VLA_PLATFORM_ENV = "E2E_VLA_PLATFORM"
_VLA_DATASET_CONTAINER_ENV = "E2E_VLA_DATASET_CONTAINER"
_DEFAULT_VLA_VERSION = "1.5"
_DEFAULT_VLA_DATA_CONFIG = "example"
_DEFAULT_VLA_EMBODIMENT_TAG = "new_embodiment"
_DEFAULT_VLA_PLATFORM = "gpu_platform"
# The OSMO data container always exists and the OSMO workflow identity has
# account-scoped Storage Blob Data Contributor, so the fine-tuning pod can read
# a dataset staged here via its workload identity (no SAS needed).
_DEFAULT_VLA_DATASET_CONTAINER = "osmo"


@dataclass(frozen=True)
class _VlaDataset:
    blob_url: str
    data_config: str
    data_config_file: Path | None


def _upload_vla_dataset(repo_root: Path, storage_account: str, container: str, prefix: str, dataset_dir: Path) -> None:
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
            str(dataset_dir),
            "--overwrite",
            "--only-show-errors",
        ],
        cwd=repo_root,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Failed to upload synthetic VLA dataset to {storage_account}/{container}/{prefix}\n\n"
            f"{format_command_failure(result)}"
        )


def _delete_vla_dataset(repo_root: Path, storage_account: str, container: str, prefix: str) -> None:
    log_e2e(f"Deleting staged synthetic VLA dataset under {container}/{prefix}")
    run_command(
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


def _stage_synthetic_vla_dataset(request: pytest.FixtureRequest, repo_root: Path) -> _VlaDataset:
    # Imported lazily so the numpy/pyarrow/imageio generator stack only loads when
    # the e2e test actually runs (not during default collection of this module).
    from tests.e2e._vla_dataset import (
        DATA_CONFIG_KEY,
        build_synthetic_dataset,
        validate_synthetic_dataset,
        write_data_config_file,
    )

    storage_account = request.getfixturevalue("storage_account")
    container = env_value(_VLA_DATASET_CONTAINER_ENV, _DEFAULT_VLA_DATASET_CONTAINER) or _DEFAULT_VLA_DATASET_CONTAINER

    work_dir = Path(tempfile.mkdtemp(prefix="vla-e2e-dataset-"))
    request.addfinalizer(lambda: shutil.rmtree(work_dir, ignore_errors=True))

    dataset_dir = work_dir / "dataset"
    log_e2e("Generating synthetic GR00T fine-tuning dataset")
    build_synthetic_dataset(dataset_dir)
    validate_synthetic_dataset(dataset_dir)
    data_config_file = write_data_config_file(work_dir / f"{DATA_CONFIG_KEY}_data_config.py")

    prefix = f"e2e-vla-datasets/{e2e_name('vla-finetune')}"
    log_e2e(f"Uploading synthetic GR00T dataset to {storage_account}/{container}/{prefix}")
    _upload_vla_dataset(repo_root, storage_account, container, prefix, dataset_dir)
    request.addfinalizer(lambda: _delete_vla_dataset(repo_root, storage_account, container, prefix))

    blob_url = f"https://{storage_account}.blob.core.windows.net/{container}/{prefix}"
    return _VlaDataset(blob_url=blob_url, data_config=DATA_CONFIG_KEY, data_config_file=data_config_file)


def _resolve_vla_dataset(request: pytest.FixtureRequest, repo_root: Path) -> _VlaDataset:
    """Use a pre-staged dataset when ``E2E_VLA_DATASET_BLOB_URL`` is set; otherwise
    generate a minimal GR00T dataset, upload it, and register teardown cleanup."""
    blob_url = env_value(_VLA_DATASET_BLOB_URL_ENV)
    if blob_url is not None:
        data_config = env_value(_VLA_DATA_CONFIG_ENV, _DEFAULT_VLA_DATA_CONFIG)
        log_e2e(f"Using pre-staged VLA dataset from {_VLA_DATASET_BLOB_URL_ENV}")
        return _VlaDataset(blob_url=blob_url, data_config=data_config, data_config_file=None)
    return _stage_synthetic_vla_dataset(request, repo_root)


def submit_osmo_vla_finetune(
    repo_root: Path,
    aml_workspace: AzureMLWorkspace,
    *,
    request: pytest.FixtureRequest,
    max_steps: int,
    save_steps: int,
    batch_size: int,
    dataloader_workers: int,
) -> OSMOWorkflow:
    dataset = _resolve_vla_dataset(request, repo_root)

    vla_version = env_value(_VLA_VERSION_ENV, _DEFAULT_VLA_VERSION)
    base_model = env_value(_VLA_BASE_MODEL_ENV)
    embodiment_tag = env_value(_VLA_EMBODIMENT_TAG_ENV, _DEFAULT_VLA_EMBODIMENT_TAG)
    platform = env_value(_VLA_PLATFORM_ENV, _DEFAULT_VLA_PLATFORM)
    job_name = e2e_name("vla-finetune-e2e-osmo")
    log_e2e(
        "Submitting OSMO VLA fine-tuning workflow "
        f"for blob_url={dataset.blob_url}, vla_version={vla_version}, data_config={dataset.data_config}, "
        f"max_steps={max_steps}, save_steps={save_steps}, batch_size={batch_size}, job={job_name}"
    )
    args = [
        str(repo_root / "training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh"),
        "--blob-url",
        dataset.blob_url,
        "--data-config",
        dataset.data_config,
        "--vla-version",
        vla_version,
        "--embodiment-tag",
        embodiment_tag,
        "--max-steps",
        str(max_steps),
        "--save-steps",
        str(save_steps),
        "--batch-size",
        str(batch_size),
        "--dataloader-workers",
        str(dataloader_workers),
        "--job-name",
        job_name,
        "--platform",
        platform,
        "--azure-subscription-id",
        aml_workspace.subscription_id,
        "--azure-resource-group",
        aml_workspace.resource_group,
        "--azure-workspace-name",
        aml_workspace.workspace_name,
    ]
    if base_model is not None:
        args.extend(["--base-model", base_model])
    if dataset.data_config_file is not None:
        args.extend(["--data-config-file", str(dataset.data_config_file)])
    args.extend(["--", "--format-type", "json"])

    result = run_command(args, cwd=repo_root)
    if result.returncode != 0:
        raise AssertionError(f"OSMO VLA fine-tuning e2e submission failed\n\n{format_command_failure(result)}")

    return _osmo_workflow_from_submission(result, job_name, "OSMO VLA fine-tuning")
