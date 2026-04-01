from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from tests.e2e._aml import AzureMLJob, AzureMLWorkspace
from tests.e2e._common import log_e2e
from tests.e2e._osmo import OSMOWorkflow

if TYPE_CHECKING:
    from mlflow.entities import Experiment
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


def _experiment_by_name(client: MlflowClient, experiment_name: str) -> Experiment:
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        raise AssertionError(f"MLflow experiment {experiment_name!r} was not found")
    return experiment


def _resolve_mlflow_run_id_by_correlation_id(
    client: MlflowClient,
    experiment_name: str,
    correlation_id: str,
) -> str:
    experiment = _experiment_by_name(client, experiment_name)
    escaped_correlation_id = correlation_id.replace("'", "\\'")
    runs = client.search_runs(
        [experiment.experiment_id],
        filter_string=f"tags.correlation_id = '{escaped_correlation_id}'",
        order_by=["attributes.start_time DESC"],
        max_results=2,
    )

    if not runs:
        raise AssertionError(
            f"No MLflow runs were found in experiment {experiment_name!r} for correlation_id {correlation_id!r}"
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

    missing_metrics = [name for name in REQUIRED_METRICS if name not in metrics]
    if missing_metrics:
        raise AssertionError(
            f"MLflow run {run_id!r} was missing metrics {missing_metrics}; available metrics: {sorted(metrics)}"
        )

    missing_params = [name for name in REQUIRED_PARAMS if name not in params or params[name] == ""]
    if missing_params:
        raise AssertionError(
            f"MLflow run {run_id!r} was missing params {missing_params}; available params: {sorted(params)}"
        )

    return MLflowTrackingResult(
        metrics={name: metrics[name] for name in REQUIRED_METRICS},
        params={name: params[name] for name in REQUIRED_PARAMS},
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
