"""Configure MLflow tracking for LeRobot evaluation and record it for the entry script.

``TRACKING_BACKEND`` selects the tracking server: the Azure ML workspace (``azureml``, default) or the MLflow
server at ``MLFLOW_TRACKING_URI`` (``mlflow``). ``none`` configures nothing. The resolved tracking URI and
experiment are written to ``MLFLOW_CONFIG_PATH`` for the entry script to export.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The tracking strategy lives in the training package beside the evaluation tree, in both the repository and
# the job payload.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from training.utils.tracking import MLflowTracking, TrackingConfigError, resolve_tracking_backend_name  # noqa: E402


def _experiment_name() -> str:
    name = os.environ.get("EXPERIMENT_NAME", "")
    if not name or name == "none":
        name = f"lerobot-{os.environ.get('POLICY_TYPE', 'act')}-inference"
    return name


def _azure_ml_tracking_uri() -> str:
    """Return the Azure ML workspace's MLflow tracking URI, or an empty string when it has none."""
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential

    client = MLClient(
        DefaultAzureCredential(),
        os.environ["AZURE_SUBSCRIPTION_ID"],
        os.environ["AZURE_RESOURCE_GROUP"],
        os.environ["AZUREML_WORKSPACE_NAME"],
    )
    workspace = client.workspaces.get(os.environ["AZUREML_WORKSPACE_NAME"])
    return workspace.mlflow_tracking_uri or ""


def main() -> int:
    """Configure MLflow for the selected backend and write the tracking config file.

    Returns:
        Process exit code.
    """
    try:
        backend = resolve_tracking_backend_name()
    except TrackingConfigError as exc:
        print(f"ERROR: {exc}")
        return 1
    if backend == "none":
        print("MLflow tracking disabled (TRACKING_BACKEND=none)")
        return 0

    import mlflow

    experiment_name = _experiment_name()
    if backend == "mlflow":
        try:
            tracking = MLflowTracking(experiment_name=experiment_name)
        except TrackingConfigError as exc:
            print(f"ERROR: {exc}")
            return 1
        tracking.configure_mlflow(mlflow)
        tracking_uri = tracking.tracking_uri
    else:
        tracking_uri = _azure_ml_tracking_uri()
        if not tracking_uri:
            print("ERROR: Azure ML workspace does not expose MLflow tracking URI")
            return 1
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)

    config_path = Path(os.environ.get("MLFLOW_CONFIG_PATH", "/tmp/mlflow_config.env"))
    config_path.write_text(f"MLFLOW_TRACKING_URI={tracking_uri}\nMLFLOW_EXPERIMENT_NAME={experiment_name}\n")
    print(f"MLflow configured: {experiment_name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
