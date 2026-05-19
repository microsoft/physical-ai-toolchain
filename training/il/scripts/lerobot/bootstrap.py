"""Azure ML MLflow bootstrap and HuggingFace authentication for LeRobot training."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MLflowConfig:
    """Resolved MLflow configuration after Azure ML bootstrap."""

    tracking_uri: str
    experiment_name: str


def bootstrap_mlflow(
    *,
    experiment_name: str = "",
    policy_type: str = "act",
    job_name: str = "training",
) -> MLflowConfig:
    """Initialize Azure ML connection and configure MLflow tracking.

    Args:
        experiment_name: Explicit experiment name (auto-derived if empty).
        policy_type: Policy architecture for default experiment naming.
        job_name: Job identifier for default experiment naming.

    Returns:
        MLflowConfig with tracking URI and resolved experiment name.

    Raises:
        SystemExit: On missing Azure environment variables or connection failure.
    """
    # Point MLflow's registry URI at a local file path BEFORE importing mlflow
    # or azure.ai.ml. azure.ai.ml.MLClient.__init__ initializes mlflow as a
    # side effect, which then probes the tracking URI as a registry URI and
    # fails because the Azure ML MLflow endpoint does not implement registry.
    # Setting the env var pre-import avoids that probe.
    registry_dir = Path(os.environ.get("MLFLOW_LOCAL_REGISTRY_DIR", "/tmp/mlflow_registry"))
    registry_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MLFLOW_REGISTRY_URI", f"file://{registry_dir}")

    try:
        import mlflow
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        print(f"[ERROR] Missing required package: {exc}", file=sys.stderr)
        sys.exit(1)

    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP", "")
    workspace_name = os.environ.get("AZUREML_WORKSPACE_NAME", "")

    if not all([subscription_id, resource_group, workspace_name]):
        print(
            "[ERROR] Azure ML requires AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AZUREML_WORKSPACE_NAME",
            file=sys.stderr,
        )
        sys.exit(1)

    print("[INFO] Initializing Azure ML connection...")

    try:
        credential = DefaultAzureCredential(
            managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
            authority=os.environ.get("AZURE_AUTHORITY_HOST"),
        )

        client = MLClient(
            credential=credential,
            subscription_id=subscription_id,
            resource_group_name=resource_group,
            workspace_name=workspace_name,
        )

        workspace = client.workspaces.get(workspace_name)
        tracking_uri = workspace.mlflow_tracking_uri

        if not tracking_uri:
            print("[ERROR] Azure ML workspace does not expose MLflow tracking URI", file=sys.stderr)
            sys.exit(1)

        # MLflow's MlflowClient defaults the registry URI to the tracking URI.
        # Azure ML's MLflow endpoint only implements tracking, not registry, so
        # any code that touches the registry (set_experiment, log_model, etc.)
        # fails with "Model registry functionality is unavailable; got
        # unsupported URI 'azureml://...'". Point the registry URI at a local
        # file-scheme directory (one of the supported schemes per the MLflow
        # error message) before any tracking operation that creates a client.
        registry_dir = Path(os.environ.get("MLFLOW_LOCAL_REGISTRY_DIR", "/tmp/mlflow_registry"))
        registry_dir.mkdir(parents=True, exist_ok=True)
        mlflow.set_registry_uri(f"file://{registry_dir}")

        mlflow.set_tracking_uri(tracking_uri)

        # When running inside an Azure ML job, the AzureML compute target
        # creates an MLflow run automatically and sets MLFLOW_RUN_ID. The run
        # already lives in an Azure-ML-assigned experiment, and overriding the
        # experiment afterward causes start_run() to fail with:
        #   Cannot start run with ID <id> because active experiment ID does
        #   not match environment run ID.
        # Skip set_experiment() in that case and attach to the existing run.
        if os.environ.get("MLFLOW_RUN_ID"):
            resolved_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "azureml-managed")
            print("[INFO] Detected MLFLOW_RUN_ID: attaching to AzureML-managed run")
        else:
            resolved_name = experiment_name or f"lerobot-{policy_type}-{job_name}"
            mlflow.set_experiment(resolved_name)

        # Skip mlflow.autolog: it tries to use the tracking URI as a model
        # registry URI, which the Azure ML MLflow endpoint does not implement.
        # Metrics are logged explicitly by the training wrapper.

        print(f"[INFO] MLflow tracking URI: {tracking_uri}")
        print(f"[INFO] MLflow experiment: {resolved_name}")

        # Write config for downstream scripts
        config_path = Path("/tmp/mlflow_config.env")
        config_path.write_text(f"MLFLOW_TRACKING_URI={tracking_uri}\nMLFLOW_EXPERIMENT_NAME={resolved_name}\n")

        return MLflowConfig(tracking_uri=tracking_uri, experiment_name=resolved_name)

    except Exception as exc:
        import traceback

        print(f"[ERROR] Failed to configure Azure ML: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def authenticate_huggingface() -> str | None:
    """Authenticate with HuggingFace Hub using HF_TOKEN environment variable.

    Returns:
        HuggingFace username if authenticated, None otherwise.
    """
    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token:
        print("Warning: HF_TOKEN not set, skipping HuggingFace authentication")
        return None

    try:
        from huggingface_hub import login, whoami

        login(token=hf_token, add_to_git_credential=False)
        user_info = whoami()
        username = user_info.get("name", "")
        print(f"[INFO] Authenticated with HuggingFace as: {username}")
        return username
    except Exception as exc:
        print(f"Warning: HuggingFace authentication failed: {exc}")
        return None
