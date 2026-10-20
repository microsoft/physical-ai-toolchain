from __future__ import annotations

import json
import math
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from tests.e2e._aml import AzureMLJob, AzureMLWorkspace
from tests.e2e._common import RegisteredModel, log_e2e
from tests.e2e._osmo import OSMOWorkflow

if TYPE_CHECKING:
    from mlflow.entities import Run
    from mlflow.tracking import MlflowClient

REQUIRED_METRICS = (
    "Learning / Learning rate",
    "Loss / Policy loss",
    "Loss / Value loss",
)
REQUIRED_PARAMS = (
    "algorithm",
    "distributed",
    "num_envs",
)
_LEROBOT_REQUIRED_METRICS = (
    "train/loss",
    "train/learning_rate",
)
_LEROBOT_REQUIRED_PARAMS = (
    "policy_type",
    "num_gpus",
    "distributed",
)
_PROXY_REQUIRED_METRICS = (
    "osmo.task_count",
    "osmo.task_completed",
    "osmo.failed_tasks",
    "osmo.task_success_rate",
    "osmo.duration_seconds",
)


@dataclass(frozen=True)
class MLflowTrackingResult:
    metrics: dict[str, float]
    params: dict[str, str]
    tags: dict[str, str]


@dataclass(frozen=True)
class MLflowServer:
    """A self-hosted MLflow tracking server and model registry, such as the local platform profile's."""

    tracking_uri: str


# Where a lifecycle test reads runs and registered models: an Azure ML workspace or an MLflow server.
MLflowTarget = AzureMLWorkspace | MLflowServer


@lru_cache(maxsize=4)
def _tracking_uri(subscription_id: str, resource_group: str, workspace_name: str) -> str:
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential

    client = MLClient(
        credential=DefaultAzureCredential(),
        subscription_id=subscription_id,
        resource_group_name=resource_group,
        workspace_name=workspace_name,
    )
    workspace = client.workspaces.get(workspace_name)
    tracking_uri = getattr(workspace, "mlflow_tracking_uri", None)
    if not isinstance(tracking_uri, str) or not tracking_uri:
        raise AssertionError(f"AzureML workspace {workspace_name!r} did not expose an MLflow tracking URI")
    return tracking_uri


def _target_tracking_uri(target: MLflowTarget) -> str:
    if isinstance(target, MLflowServer):
        return target.tracking_uri
    return _tracking_uri(target.subscription_id, target.resource_group, target.workspace_name)


def _mlflow_client(target: MLflowTarget) -> MlflowClient:
    import mlflow
    from mlflow.tracking import MlflowClient

    tracking_uri = _target_tracking_uri(target)
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_registry_uri(tracking_uri)
    return MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)


_MLFLOW_SEARCH_TIMEOUT_SECONDS = 300
_MLFLOW_SEARCH_POLL_INTERVAL_SECONDS = 10


def _search_experiment_runs_with_retry(
    client: MlflowClient,
    experiment_name: str,
    *,
    filter_string: str = "",
    max_results: int = 1,
    criteria: str | None = None,
) -> list[Run]:
    """Search an experiment's runs, tolerating search-index lag.

    AzureML's MLflow ``get_experiment_by_name`` and ``search_runs`` are eventually
    consistent: a just-finished run (or even its experiment) can trail the search
    index by tens of seconds, so a single query races the index. Poll until a run
    is visible or the budget is spent; a self-hosted server answers on the first query.
    """
    deadline = time.monotonic() + _MLFLOW_SEARCH_TIMEOUT_SECONDS
    waited = False
    while True:
        experiment = client.get_experiment_by_name(experiment_name)
        if experiment is not None:
            runs = client.search_runs(
                [experiment.experiment_id],
                filter_string=filter_string,
                order_by=["attributes.start_time DESC"],
                max_results=max_results,
            )
            if runs:
                return runs
        if time.monotonic() >= deadline:
            suffix = f" matching {criteria}" if criteria else ""
            raise AssertionError(
                f"No MLflow runs{suffix} appeared in experiment {experiment_name!r} "
                f"within {_MLFLOW_SEARCH_TIMEOUT_SECONDS}s"
            )
        if not waited:
            log_e2e(
                f"Waiting up to {_MLFLOW_SEARCH_TIMEOUT_SECONDS}s for MLflow search to index "
                f"{criteria or 'a run'} in experiment {experiment_name!r}"
            )
            waited = True
        time.sleep(_MLFLOW_SEARCH_POLL_INTERVAL_SECONDS)


def _resolve_mlflow_run_id_by_correlation_id(
    client: MlflowClient,
    experiment_name: str,
    correlation_id: str,
) -> str:
    escaped_correlation_id = correlation_id.replace("'", "\\'")
    runs = _search_experiment_runs_with_retry(
        client,
        experiment_name,
        filter_string=f"tags.correlation_id = '{escaped_correlation_id}'",
        max_results=2,
        criteria=f"correlation_id {correlation_id!r}",
    )

    if len(runs) > 1:
        raise AssertionError(
            f"Multiple MLflow runs were found in experiment {experiment_name!r} for correlation_id {correlation_id!r}"
        )

    selected_run = runs[0]
    log_e2e(
        f"Resolved MLflow run via exact correlation_id tag: correlation_id={correlation_id}, "
        f"run_id={selected_run.info.run_id}"
    )
    return selected_run.info.run_id


def _assert_run_has_expected_tracking(
    target: MLflowTarget,
    *,
    run_id: str,
    experiment_name: str,
    required_metrics: tuple[str, ...] = REQUIRED_METRICS,
    required_params: tuple[str, ...] = REQUIRED_PARAMS,
) -> MLflowTrackingResult:
    client = _mlflow_client(target)
    run = client.get_run(run_id)
    experiment = client.get_experiment(run.info.experiment_id)
    actual_experiment_name = experiment.name if experiment is not None else None

    if actual_experiment_name != experiment_name:
        raise AssertionError(
            f"MLflow run {run_id!r} belonged to experiment {actual_experiment_name!r}, expected {experiment_name!r}"
        )

    metrics = run.data.metrics
    params = run.data.params

    missing_metrics = [name for name in required_metrics if name not in metrics]
    if missing_metrics:
        raise AssertionError(
            f"MLflow run {run_id!r} was missing metrics {missing_metrics}; available metrics: {sorted(metrics)}"
        )

    missing_params = [name for name in required_params if name not in params or params[name] == ""]
    if missing_params:
        raise AssertionError(
            f"MLflow run {run_id!r} was missing params {missing_params}; available params: {sorted(params)}"
        )

    return MLflowTrackingResult(
        metrics={name: metrics[name] for name in required_metrics},
        params={name: params[name] for name in required_params},
        tags=run.data.tags,
    )


def assert_aml_job_has_mlflow_tracking(job: AzureMLJob, aml_workspace: AzureMLWorkspace) -> None:
    tracking = _assert_run_has_expected_tracking(
        aml_workspace,
        run_id=job.name,
        experiment_name=job.experiment_name,
    )
    rendered_metrics = ", ".join(f"{name}={value}" for name, value in tracking.metrics.items())
    rendered_params = ", ".join(f"{name}={value}" for name, value in tracking.params.items())
    log_e2e(f"AzureML MLflow tracking passed: metrics=[{rendered_metrics}] params=[{rendered_params}]")


def assert_aml_lerobot_job_has_mlflow_tracking(job: AzureMLJob, aml_workspace: AzureMLWorkspace) -> None:
    tracking = _assert_run_has_expected_tracking(
        aml_workspace,
        run_id=job.name,
        experiment_name=job.experiment_name,
        required_metrics=_LEROBOT_REQUIRED_METRICS,
        required_params=_LEROBOT_REQUIRED_PARAMS,
    )
    rendered_metrics = ", ".join(f"{name}={value}" for name, value in tracking.metrics.items())
    rendered_params = ", ".join(f"{name}={value}" for name, value in tracking.params.items())
    log_e2e(f"AzureML LeRobot MLflow tracking passed: metrics=[{rendered_metrics}] params=[{rendered_params}]")


def assert_osmo_workflow_has_mlflow_tracking(workflow: OSMOWorkflow, target: MLflowTarget) -> None:
    client = _mlflow_client(target)
    run_id = _resolve_mlflow_run_id_by_correlation_id(client, workflow.experiment_name, workflow.correlation_id)
    tracking = _assert_run_has_expected_tracking(
        target,
        run_id=run_id,
        experiment_name=workflow.experiment_name,
    )
    if tracking.tags.get("correlation_id") != workflow.correlation_id:
        raise AssertionError(
            f"MLflow run {run_id!r} had correlation_id={tracking.tags.get('correlation_id')!r}, "
            f"expected {workflow.correlation_id!r}"
        )

    workflow.handle.resource_identifiers["mlflow_run"] = run_id
    rendered_tags = ", ".join(
        f"{name}={value}" for name, value in sorted(tracking.tags.items()) if name == "correlation_id"
    )
    rendered_metrics = ", ".join(f"{name}={value}" for name, value in tracking.metrics.items())
    rendered_params = ", ".join(f"{name}={value}" for name, value in tracking.params.items())
    log_e2e(
        f"OSMO MLflow tracking passed: run_id={run_id} metrics=[{rendered_metrics}] "
        f"params=[{rendered_params}] tags=[{rendered_tags}]"
    )


def assert_aml_osmo_proxy_has_mlflow_tracking(
    job: AzureMLJob,
    aml_workspace: AzureMLWorkspace,
) -> tuple[str, str]:
    run_id = _resolve_latest_mlflow_run_id_by_experiment(aml_workspace, job.experiment_name)
    tracking = _assert_run_has_expected_tracking(
        aml_workspace,
        run_id=run_id,
        experiment_name=job.experiment_name,
        required_metrics=_PROXY_REQUIRED_METRICS,
        required_params=(),
    )
    workflow_id = tracking.tags.get("osmo.workflow_id")
    if not workflow_id:
        raise AssertionError(f"MLflow run {run_id!r} did not include osmo.workflow_id")
    if tracking.tags.get("osmo.status") != "COMPLETED":
        raise AssertionError(
            f"MLflow run {run_id!r} had osmo.status={tracking.tags.get('osmo.status')!r}, expected 'COMPLETED'"
        )
    expected_metrics = {
        "osmo.task_count": 1.0,
        "osmo.task_completed": 1.0,
        "osmo.failed_tasks": 0.0,
        "osmo.task_success_rate": 1.0,
    }
    for name, expected in expected_metrics.items():
        if tracking.metrics[name] != expected:
            raise AssertionError(f"MLflow run {run_id!r} had {name}={tracking.metrics[name]!r}, expected {expected!r}")
    if tracking.metrics["osmo.duration_seconds"] < 0:
        raise AssertionError(
            f"MLflow run {run_id!r} had negative duration {tracking.metrics['osmo.duration_seconds']!r}"
        )
    job.handle.resource_identifiers["osmo_workflow"] = workflow_id
    job.handle.resource_identifiers["mlflow_run"] = run_id
    log_e2e(f"AML-to-OSMO proxy MLflow tracking passed: job={job.name}, workflow_id={workflow_id}, run_id={run_id}")
    return workflow_id, run_id


def assert_osmo_vla_has_mlflow_tracking(
    workflow: OSMOWorkflow,
    target: MLflowTarget,
) -> str:
    client = _mlflow_client(target)
    correlation_id = workflow.correlation_id or workflow.workflow_id
    escaped_correlation_id = correlation_id.replace("'", "\\'")
    runs = _search_experiment_runs_with_retry(
        client,
        workflow.experiment_name,
        filter_string=f"tags.osmo.run_id = '{escaped_correlation_id}'",
        max_results=2,
        criteria=f"osmo.run_id {correlation_id!r}",
    )
    if len(runs) > 1:
        raise AssertionError(
            f"Multiple MLflow runs were found in experiment {workflow.experiment_name!r} "
            f"for osmo.run_id {correlation_id!r}"
        )
    run = runs[0]
    if run.data.tags.get("framework") != "groot" or run.data.tags.get("source") != "osmo-train":
        raise AssertionError(f"MLflow run {run.info.run_id!r} had unexpected framework/source tags: {run.data.tags}")
    required_params = ("BASE_MODEL", "BASE_MODEL_REVISION", "ISAAC_GROOT_REF")
    missing_params = [name for name in required_params if not run.data.params.get(name)]
    if missing_params:
        raise AssertionError(f"MLflow run {run.info.run_id!r} was missing VLA provenance params {missing_params}")
    required_metrics = ("training.checkpoint_count", "training.final_checkpoint_step", "training.model_size_gib")
    missing_metrics = [name for name in required_metrics if name not in run.data.metrics]
    if missing_metrics:
        raise AssertionError(f"MLflow run {run.info.run_id!r} was missing VLA metrics {missing_metrics}")
    workflow.handle.resource_identifiers["mlflow_run"] = run.info.run_id
    log_e2e(f"OSMO VLA MLflow tracking passed: workflow={workflow.workflow_id}, run_id={run.info.run_id}")
    return run.info.run_id


def delete_mlflow_run(target: MLflowTarget, run_id: str) -> None:
    log_e2e(f"Deleting MLflow run {run_id}")
    _mlflow_client(target).delete_run(run_id)


def delete_mlflow_experiment(target: MLflowTarget, experiment_name: str) -> None:
    client = _mlflow_client(target)
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return
    log_e2e(f"Deleting MLflow experiment {experiment_name}")
    client.delete_experiment(experiment.experiment_id)


def assert_osmo_replay_has_mlflow_run(
    *,
    source_run_id: str,
    model_name: str,
    aml_workspace: AzureMLWorkspace,
) -> None:
    """Assert that replay created a tagged MLflow run with uploaded model artifacts."""
    import mlflow

    client = _mlflow_client(aml_workspace)
    escaped_source_run_id = source_run_id.replace("'", "\\'")
    runs = _search_experiment_runs_with_retry(
        client,
        model_name,
        filter_string=f"tags.osmo.replay_source = '{escaped_source_run_id}'",
        max_results=2,
        criteria=f"replay source {source_run_id!r}",
    )
    if len(runs) > 1:
        raise AssertionError(
            f"Multiple MLflow runs were found in experiment {model_name!r} for replay source {source_run_id!r}"
        )

    run = runs[0]
    if run.data.tags.get("osmo.replay") != "true":
        raise AssertionError(f"MLflow replay run {run.info.run_id!r} did not include osmo.replay=true")

    with tempfile.TemporaryDirectory(prefix="e2e-replay-artifacts-") as download_root:
        run = client.get_run(run.info.run_id)
        downloaded_path = Path(
            mlflow.artifacts.download_artifacts(
                artifact_uri=f"{run.info.artifact_uri.rstrip('/')}/model",
                dst_path=download_root,
            )
        )
        artifact_files = [path for path in downloaded_path.rglob("*") if path.is_file()]
        if not artifact_files:
            raise AssertionError(f"MLflow replay run {run.info.run_id!r} contained no model artifacts")

    log_e2e(
        f"OSMO replay MLflow artifacts passed: run_id={run.info.run_id}, "
        f"source_run_id={source_run_id}, artifacts={len(artifact_files)}"
    )


_LEROBOT_EVAL_REQUIRED_METRICS = (
    "ep0_mse",
    "ep0_mae",
    "ep0_avg_inference_ms",
    "ep0_throughput_hz",
    "aggregate_mse",
    "aggregate_mae",
    "aggregate_avg_inference_ms",
    "aggregate_throughput_hz",
)
_LEROBOT_EVAL_REQUIRED_PARAMS = (
    "policy_repo_id",
    "policy_type",
    "eval_episodes",
    "device",
    "fps",
)


def _resolve_latest_mlflow_run_id_by_experiment(target: MLflowTarget, experiment_name: str) -> str:
    """Resolve the most recent MLflow run in a per-run unique experiment.

    Used by OSMO LeRobot submission paths whose scripts do not set a
    ``correlation_id`` tag; the unique experiment name keeps the resolution
    unambiguous (a single run lands in each experiment).
    """
    client = _mlflow_client(target)
    runs = _search_experiment_runs_with_retry(client, experiment_name, max_results=1)
    run_id = runs[0].info.run_id
    log_e2e(f"Resolved latest MLflow run in experiment {experiment_name}: run_id={run_id}")
    return run_id


def assert_osmo_lerobot_training_has_mlflow_tracking(workflow: OSMOWorkflow, target: MLflowTarget) -> None:
    run_id = _resolve_latest_mlflow_run_id_by_experiment(target, workflow.experiment_name)
    tracking = _assert_run_has_expected_tracking(
        target,
        run_id=run_id,
        experiment_name=workflow.experiment_name,
        required_metrics=_LEROBOT_REQUIRED_METRICS,
        required_params=_LEROBOT_REQUIRED_PARAMS,
    )
    rendered_metrics = ", ".join(f"{name}={value}" for name, value in tracking.metrics.items())
    rendered_params = ", ".join(f"{name}={value}" for name, value in tracking.params.items())
    log_e2e(
        f"OSMO LeRobot training MLflow tracking passed: run_id={run_id} "
        f"metrics=[{rendered_metrics}] params=[{rendered_params}]"
    )


@dataclass(frozen=True)
class LeRobotEvalResult:
    """The metrics of one LeRobot replay evaluation run."""

    run_id: str
    metrics: dict[str, float]

    @property
    def aggregate_mse(self) -> float:
        return self.metrics["aggregate_mse"]

    def action_error(self) -> dict[str, float]:
        """Return the action-error metrics, which the same weights reproduce exactly; timing metrics vary."""
        return {name: value for name, value in self.metrics.items() if name.endswith(("_mse", "_mae"))}


def assert_evaluation_schema(metrics_payload: object, failure_cases: str) -> None:
    """Assert evaluation schema v1: finite metrics, a passing aggregate verdict, and no failure cases."""
    if not isinstance(metrics_payload, dict) or metrics_payload.get("evaluation_schema_version") != 1:
        raise AssertionError(f"metrics.json is not evaluation schema version 1: {metrics_payload!r}")
    if metrics_payload.get("aggregate_verdict") != "pass":
        raise AssertionError(f"metrics.json aggregate verdict is {metrics_payload.get('aggregate_verdict')!r}")
    entries = metrics_payload.get("metrics")
    if not isinstance(entries, list) or not entries:
        raise AssertionError("metrics.json lists no metrics")
    for entry in entries:
        value = entry.get("value") if isinstance(entry, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise AssertionError(f"metrics.json has a metric without a finite value: {entry!r}")
    cases = [line for line in failure_cases.splitlines() if line.strip()]
    if cases:
        raise AssertionError(f"failure_cases.jsonl lists {len(cases)} failure case(s), first: {cases[0]}")


def assert_osmo_lerobot_eval_has_mlflow_tracking(workflow: OSMOWorkflow, target: MLflowTarget) -> LeRobotEvalResult:
    """Assert the replay evaluation's run, metrics, and evaluation schema files; return its metrics."""
    run_id = _resolve_latest_mlflow_run_id_by_experiment(target, workflow.experiment_name)
    tracking = _assert_run_has_expected_tracking(
        target,
        run_id=run_id,
        experiment_name=workflow.experiment_name,
        required_metrics=_LEROBOT_EVAL_REQUIRED_METRICS,
        required_params=_LEROBOT_EVAL_REQUIRED_PARAMS,
    )
    client = _mlflow_client(target)
    run = client.get_run(run_id)
    metrics = dict(run.data.metrics)
    non_finite = sorted(name for name, value in metrics.items() if not math.isfinite(value))
    if non_finite:
        raise AssertionError(f"MLflow run {run_id!r} has non-finite metrics {non_finite}")
    requested = int(run.data.params["eval_episodes"])
    if metrics.get("episodes_evaluated") != requested:
        raise AssertionError(
            f"MLflow run {run_id!r} evaluated {metrics.get('episodes_evaluated')!r} of {requested} episodes"
        )
    with tempfile.TemporaryDirectory(prefix="e2e-lerobot-eval-") as download_root:
        metrics_file = Path(client.download_artifacts(run_id, "metrics.json", download_root))
        failure_file = Path(client.download_artifacts(run_id, "failure_cases.jsonl", download_root))
        assert_evaluation_schema(json.loads(metrics_file.read_text(encoding="utf-8")), failure_file.read_text())

    rendered_metrics = ", ".join(f"{name}={value}" for name, value in tracking.metrics.items())
    rendered_params = ", ".join(f"{name}={value}" for name, value in tracking.params.items())
    log_e2e(
        f"OSMO LeRobot eval MLflow tracking passed: run_id={run_id} "
        f"metrics=[{rendered_metrics}] params=[{rendered_params}]"
    )
    return LeRobotEvalResult(run_id=run_id, metrics=metrics)


@dataclass(frozen=True)
class IsaacEvalGate:
    """The verdict of one gated Isaac Lab evaluation run."""

    run_id: str
    success_rate: float
    eval_episodes: int
    videos: int


def _artifact_files(client: MlflowClient, run_id: str, path: str) -> list[str]:
    files: list[str] = []
    for artifact in client.list_artifacts(run_id, path):
        if artifact.is_dir:
            files.extend(_artifact_files(client, run_id, artifact.path))
        else:
            files.append(artifact.path)
    return files


def assert_osmo_isaac_eval_gate(
    workflow: OSMOWorkflow, target: MLflowTarget, *, minimum_success_rate: float
) -> IsaacEvalGate:
    """Assert that the gated evaluation passed, reached ``minimum_success_rate``, and logged rollout videos."""
    run_id = _resolve_latest_mlflow_run_id_by_experiment(target, workflow.experiment_name)
    client = _mlflow_client(target)
    metrics = client.get_run(run_id).data.metrics
    missing = [name for name in ("success_rate", "passed", "eval_episodes") if name not in metrics]
    if missing:
        raise AssertionError(f"MLflow run {run_id!r} has no gate metrics {missing}; available: {sorted(metrics)}")
    if metrics["passed"] != 1.0:
        raise AssertionError(f"MLflow run {run_id!r} records a failed gate: success_rate={metrics['success_rate']}")
    if metrics["success_rate"] < minimum_success_rate:
        raise AssertionError(
            f"MLflow run {run_id!r} success_rate={metrics['success_rate']} is below {minimum_success_rate}"
        )
    videos = [path for path in _artifact_files(client, run_id, "videos") if path.endswith(".mp4")]
    if not videos:
        raise AssertionError(f"MLflow run {run_id!r} logged no rollout videos")
    gate = IsaacEvalGate(
        run_id=run_id,
        success_rate=metrics["success_rate"],
        eval_episodes=int(metrics["eval_episodes"]),
        videos=len(videos),
    )
    workflow.handle.resource_identifiers["mlflow_run"] = run_id
    log_e2e(
        f"OSMO Isaac Lab gate passed: run_id={run_id} success_rate={gate.success_rate} "
        f"episodes={gate.eval_episodes} videos={gate.videos}"
    )
    return gate


def resolve_mlflow_registered_model(target: MLflowTarget, model_name: str) -> RegisteredModel:
    """Resolve the concrete latest version of a model in an MLflow registry."""
    client = _mlflow_client(target)
    escaped_name = model_name.replace("'", "\\'")
    versions = [int(version.version) for version in client.search_model_versions(f"name = '{escaped_name}'")]
    if not versions:
        raise AssertionError(f"Training run registered no versions under MLflow model {model_name!r}")
    model = RegisteredModel(name=model_name, version=str(max(versions)))
    log_e2e(f"Resolved MLflow model {model_name} to concrete version {model.version}")
    return model


def delete_mlflow_registered_model(target: MLflowTarget, model_name: str) -> None:
    """Delete an MLflow registered model and its versions; the training runs keep their artifacts."""
    from mlflow.exceptions import MlflowException

    log_e2e(f"Deleting MLflow registered model {model_name}")
    try:
        _mlflow_client(target).delete_registered_model(model_name)
    except MlflowException as error:
        if error.error_code != "RESOURCE_DOES_NOT_EXIST":
            raise


def assert_aml_lerobot_eval_has_mlflow_tracking(job: AzureMLJob, aml_workspace: AzureMLWorkspace) -> None:
    """Validate the AzureML LeRobot eval MLflow run.

    The eval entrypoint (``run_evaluation.py``) creates its own MLflow run inside the
    job, so the run id does not equal the AzureML job name. Resolution is by the
    per-run-unique experiment name the submission sets, mirroring the OSMO eval path.
    """
    run_id = _resolve_latest_mlflow_run_id_by_experiment(aml_workspace, job.experiment_name)
    tracking = _assert_run_has_expected_tracking(
        aml_workspace,
        run_id=run_id,
        experiment_name=job.experiment_name,
        required_metrics=_LEROBOT_EVAL_REQUIRED_METRICS,
        required_params=_LEROBOT_EVAL_REQUIRED_PARAMS,
    )
    rendered_metrics = ", ".join(f"{name}={value}" for name, value in tracking.metrics.items())
    rendered_params = ", ".join(f"{name}={value}" for name, value in tracking.params.items())
    log_e2e(
        f"AzureML LeRobot eval MLflow tracking passed: run_id={run_id} "
        f"metrics=[{rendered_metrics}] params=[{rendered_params}]"
    )
