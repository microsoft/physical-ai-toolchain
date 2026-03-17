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

        mlflow.set_tracking_uri(tracking_uri)

        resolved_name = experiment_name or f"lerobot-{policy_type}-{job_name}"
        mlflow.set_experiment(resolved_name)
        mlflow.autolog(log_models=False, log_input_examples=False)

        print(f"[INFO] MLflow tracking URI: {tracking_uri}")
        print(f"[INFO] MLflow experiment: {resolved_name}")

        # Write config for downstream scripts
        config_path = Path("/tmp/mlflow_config.env")
        config_path.write_text(f"MLFLOW_TRACKING_URI={tracking_uri}\nMLFLOW_EXPERIMENT_NAME={resolved_name}\n")

        return MLflowConfig(tracking_uri=tracking_uri, experiment_name=resolved_name)

    except Exception as exc:
        print(f"[ERROR] Failed to configure Azure ML: {exc}", file=sys.stderr)
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
