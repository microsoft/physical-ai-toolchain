from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from tests.e2e._aml import AzureMLJob, AzureMLWorkspace
from tests.e2e._common import log_e2e
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


@dataclass(frozen=True)
class MLflowTrackingResult:
    metrics: dict[str, float]
    params: dict[str, str]
    tags: dict[str, str]


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


def _mlflow_client(aml_workspace: AzureMLWorkspace) -> MlflowClient:
    from mlflow.tracking import MlflowClient

    return MlflowClient(
        tracking_uri=_tracking_uri(
            aml_workspace.subscription_id,
            aml_workspace.resource_group,
            aml_workspace.workspace_name,
        )
    )


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
    """Search an experiment's runs, tolerating AzureML search-index lag.

    AzureML's MLflow ``get_experiment_by_name`` and ``search_runs`` are eventually
    consistent: a just-finished run (or even its experiment) can trail the search
    index by tens of seconds, so a single query races the index. Poll until a run
    is visible or the budget is spent.
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
                f"Waiting up to {_MLFLOW_SEARCH_TIMEOUT_SECONDS}s for AzureML MLflow search to index "
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
    aml_workspace: AzureMLWorkspace,
    *,
    run_id: str,
    experiment_name: str,
    required_metrics: tuple[str, ...] = REQUIRED_METRICS,
    required_params: tuple[str, ...] = REQUIRED_PARAMS,
) -> MLflowTrackingResult:
    client = _mlflow_client(aml_workspace)
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


def assert_osmo_workflow_has_mlflow_tracking(workflow: OSMOWorkflow, aml_workspace: AzureMLWorkspace) -> None:
    client = _mlflow_client(aml_workspace)
    run_id = _resolve_mlflow_run_id_by_correlation_id(client, workflow.experiment_name, workflow.correlation_id)
    tracking = _assert_run_has_expected_tracking(
        aml_workspace,
        run_id=run_id,
        experiment_name=workflow.experiment_name,
    )

    if tracking.tags.get("correlation_id") != workflow.correlation_id:
        raise AssertionError(
            f"MLflow run {run_id!r} had correlation_id={tracking.tags.get('correlation_id')!r}, "
            f"expected {workflow.correlation_id!r}"
        )

    rendered_tags = ", ".join(
        f"{name}={value}" for name, value in sorted(tracking.tags.items()) if name == "correlation_id"
    )
    rendered_metrics = ", ".join(f"{name}={value}" for name, value in tracking.metrics.items())
    rendered_params = ", ".join(f"{name}={value}" for name, value in tracking.params.items())
    log_e2e(
        f"OSMO MLflow tracking passed: run_id={run_id} metrics=[{rendered_metrics}] "
        f"params=[{rendered_params}] tags=[{rendered_tags}]"
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


def _resolve_latest_mlflow_run_id_by_experiment(aml_workspace: AzureMLWorkspace, experiment_name: str) -> str:
    """Resolve the most recent MLflow run in a per-run unique experiment.

    Used by OSMO LeRobot submission paths whose scripts do not set a
    ``correlation_id`` tag; the unique experiment name keeps the resolution
    unambiguous (a single run lands in each experiment).
    """
    client = _mlflow_client(aml_workspace)
    runs = _search_experiment_runs_with_retry(client, experiment_name, max_results=1)
    run_id = runs[0].info.run_id
    log_e2e(f"Resolved latest MLflow run in experiment {experiment_name}: run_id={run_id}")
    return run_id


def assert_osmo_lerobot_training_has_mlflow_tracking(workflow: OSMOWorkflow, aml_workspace: AzureMLWorkspace) -> None:
    run_id = _resolve_latest_mlflow_run_id_by_experiment(aml_workspace, workflow.experiment_name)
    tracking = _assert_run_has_expected_tracking(
        aml_workspace,
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


def assert_osmo_lerobot_eval_has_mlflow_tracking(workflow: OSMOWorkflow, aml_workspace: AzureMLWorkspace) -> None:
    run_id = _resolve_latest_mlflow_run_id_by_experiment(aml_workspace, workflow.experiment_name)
    tracking = _assert_run_has_expected_tracking(
        aml_workspace,
        run_id=run_id,
        experiment_name=workflow.experiment_name,
        required_metrics=_LEROBOT_EVAL_REQUIRED_METRICS,
        required_params=_LEROBOT_EVAL_REQUIRED_PARAMS,
    )
    rendered_metrics = ", ".join(f"{name}={value}" for name, value in tracking.metrics.items())
    rendered_params = ", ".join(f"{name}={value}" for name, value in tracking.params.items())
    log_e2e(
        f"OSMO LeRobot eval MLflow tracking passed: run_id={run_id} "
        f"metrics=[{rendered_metrics}] params=[{rendered_params}]"
    )


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
