"""Checkpoint upload and model registration for LeRobot training."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

EXIT_SUCCESS = 0


def _get_aml_client() -> Any | None:
    """Create an Azure ML client from environment variables.

    Returns:
        MLClient instance, or None if credentials/config are unavailable.
    """
    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP", "")
    workspace_name = os.environ.get("AZUREML_WORKSPACE_NAME", "")

    if not all([subscription_id, resource_group, workspace_name]):
        return None

    try:
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential(
            managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"),
            authority=os.environ.get("AZURE_AUTHORITY_HOST"),
        )
        return MLClient(
            credential=credential,
            subscription_id=subscription_id,
            resource_group_name=resource_group,
            workspace_name=workspace_name,
        )
    except Exception as exc:
        print(f"[AzureML] Failed to create MLClient: {exc}")
        return None


def _register_model_via_aml(
    checkpoint_path: Path,
    checkpoint_name: str,
    *,
    source: str = "osmo-lerobot-training",
) -> bool:
    """Register a checkpoint as an Azure ML model using the AML SDK directly.

    Bypasses mlflow.register_model() to avoid azureml-mlflow SDK compatibility
    issues with the tracking_uri parameter.

    Args:
        checkpoint_path: Local path to checkpoint directory.
        checkpoint_name: Identifier for the checkpoint (e.g., "005000").
        source: Source tag value for provenance tracking.

    Returns:
        True if registration succeeded.
    """
    client = _get_aml_client()
    if client is None:
        print(f"[AzureML] No AML client available, skipping registration for {checkpoint_name}")
        return False

    try:
        from azure.ai.ml.constants import AssetTypes
        from azure.ai.ml.entities import Model

        job_name = os.environ.get("JOB_NAME", "lerobot-training")
        policy_type = os.environ.get("POLICY_TYPE", "act")
        register_name = os.environ.get("REGISTER_CHECKPOINT", "") or job_name
        model_name = register_name.replace("_", "-")

        model = Model(
            path=str(checkpoint_path),
            name=model_name,
            description=f"LeRobot {policy_type} policy from job: {job_name} (checkpoint {checkpoint_name})",
            type=AssetTypes.CUSTOM_MODEL,
            tags={
                "framework": "lerobot",
                "policy_type": policy_type,
                "job_name": job_name,
                "checkpoint": checkpoint_name,
                "source": source,
            },
        )
        registered = client.models.create_or_update(model)
        print(f"[AzureML] Registered: {registered.name} v{registered.version} ({checkpoint_name})")
        return True
    except Exception as exc:
        print(f"[AzureML] Failed to register checkpoint {checkpoint_name}: {exc}")
        return False


def upload_new_checkpoints(
    run: Any,
    output_dir: Path,
    uploaded: set[str],
    *,
    source: str = "osmo-lerobot-training",
) -> None:
    """Scan for new checkpoint directories, log artifacts to MLflow, and register via AML SDK.

    Args:
        run: Active MLflow run object.
        output_dir: Training output directory containing checkpoints/.
        uploaded: Set of already-uploaded checkpoint names (mutated in place).
        source: Source tag value for provenance tracking.
    """
    import mlflow

    checkpoints_dir = output_dir / "checkpoints"
    if not checkpoints_dir.exists():
        return

    for ckpt_dir in checkpoints_dir.iterdir():
        if ckpt_dir.is_dir() and ckpt_dir.name not in uploaded:
            pretrained_dir = ckpt_dir / "pretrained_model"
            if pretrained_dir.exists() and (pretrained_dir / "model.safetensors").exists():
                print(f"[MLflow] Uploading checkpoint: {ckpt_dir.name}")
                artifact_path = f"checkpoints/{ckpt_dir.name}"
                try:
                    mlflow.log_artifacts(str(pretrained_dir), artifact_path)
                    mlflow.set_tag(f"checkpoint_{ckpt_dir.name}_artifact", artifact_path)
                except Exception as exc:
                    print(f"[MLflow] Failed to log artifacts for {ckpt_dir.name}: {exc}")

                uploaded.add(ckpt_dir.name)
                _register_model_via_aml(pretrained_dir, ckpt_dir.name, source=source)


def register_final_checkpoint() -> int:
    """Register the latest checkpoint to Azure ML model registry.

    Reads configuration from environment variables:
        REGISTER_CHECKPOINT: Model name for registration.
        OUTPUT_DIR: Training output directory.

    Returns:
        Exit code (0 on success).
    """
    register_name = os.environ.get("REGISTER_CHECKPOINT", "")
    if not register_name:
        return EXIT_SUCCESS

    output_dir = Path(os.environ.get("OUTPUT_DIR", "/workspace/outputs/train"))

    checkpoint_dirs = sorted(
        output_dir.glob("checkpoints/*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not checkpoint_dirs:
        pretrained_dir = output_dir / "pretrained_model"
        if pretrained_dir.exists():
            checkpoint_path = pretrained_dir
            checkpoint_name = "last"
        else:
            print(f"[WARNING] No checkpoints found in {output_dir}")
            return EXIT_SUCCESS
    else:
        checkpoint_path = checkpoint_dirs[0] / "pretrained_model"
        checkpoint_name = checkpoint_dirs[0].name
        if not checkpoint_path.exists():
            checkpoint_path = checkpoint_dirs[0]

    print(f"[INFO] Registering checkpoint from: {checkpoint_path}")
    print(f"[INFO] Model name: {register_name}")

    _register_model_via_aml(checkpoint_path, checkpoint_name, source="osmo-workflow")

    return EXIT_SUCCESS


def upload_checkpoints_to_azure_ml() -> int:
    """Upload all checkpoints to Azure ML model registry.

    Used as a post-training step to register any checkpoints that weren't
    uploaded during training.

    Returns:
        Exit code (0 on success).
    """
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/workspace/outputs/train"))
    checkpoints_dir = output_dir / "checkpoints"
    if not checkpoints_dir.exists():
        print("[AzureML] No checkpoints directory found, skipping upload")
        return EXIT_SUCCESS

    uploaded = 0
    for ckpt_dir in sorted(checkpoints_dir.iterdir()):
        if not ckpt_dir.is_dir():
            continue
        pretrained = ckpt_dir / "pretrained_model"
        checkpoint_path = pretrained if pretrained.exists() else ckpt_dir

        if not (checkpoint_path / "model.safetensors").exists():
            continue

        if _register_model_via_aml(checkpoint_path, ckpt_dir.name, source="osmo-lerobot-training"):
            uploaded += 1

    if uploaded == 0:
        print("[AzureML] No valid checkpoints found to upload")
    else:
        print(f"[AzureML] Uploaded {uploaded} checkpoint(s) to Azure ML")

    return EXIT_SUCCESS
