"""Checkpoint upload and model registration for LeRobot training."""

from __future__ import annotations

import json
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

        # Lineage metadata: dataset -> job -> model.
        # These tags make it possible to walk from a registered model back to
        # the training run, the MLflow run, and the Azure Blob path (or
        # HuggingFace repo as a rarely-used fallback) that produced it.
        # Surfaces in the AML Model card and via `az ml model show --name <name>`.
        #
        # Three submission paths feed three different env-var conventions:
        #   1. AzureML submission: BLOB_URLS (JSON array of canonical
        #      https://<account>.blob.core.windows.net/<container>/<prefix> URLs).
        #   2. OSMO submission: STORAGE_ACCOUNT + STORAGE_CONTAINER + BLOB_PREFIX
        #      (the canonical URL is synthesized here for lineage parity).
        #   3. HuggingFace fallback: DATASET_REPO_ID alone. Datasets in this
        #      repo come from Azure Blob; HF is supported for legacy compatibility.
        dataset_repo_id = os.environ.get("DATASET_REPO_ID", "")
        blob_urls_json = os.environ.get("BLOB_URLS", "")
        storage_account = os.environ.get("STORAGE_ACCOUNT", "")
        storage_container = os.environ.get("STORAGE_CONTAINER", "")
        blob_prefix = os.environ.get("BLOB_PREFIX", "")
        azureml_run_id = os.environ.get("AZUREML_RUN_ID", "") or os.environ.get("MLFLOW_RUN_ID", "")
        mlflow_run_id = os.environ.get("MLFLOW_RUN_ID", "")
        experiment_id = os.environ.get("MLFLOW_EXPERIMENT_ID", "")

        dataset_uri = ""
        dataset_source_kind = ""
        blob_urls: list[str] = []

        if blob_urls_json and blob_urls_json not in ("{}", "[]"):
            try:
                parsed = json.loads(blob_urls_json)
                if isinstance(parsed, list) and parsed:
                    blob_urls = [str(u) for u in parsed]
                    dataset_uri = blob_urls[0] if len(blob_urls) == 1 else " ".join(blob_urls)
                    dataset_source_kind = "azure-blob"
            except (json.JSONDecodeError, TypeError) as exc:
                print(f"[AzureML] Failed to parse BLOB_URLS: {exc}. Falling back to other dataset source metadata.")

        if not dataset_uri and storage_account:
            container = storage_container or "datasets"
            prefix = blob_prefix.lstrip("/")
            synthesized = f"https://{storage_account}.blob.core.windows.net/{container}"
            if prefix:
                synthesized = f"{synthesized}/{prefix}"
            blob_urls = [synthesized]
            dataset_uri = synthesized
            dataset_source_kind = "azure-blob"

        if not dataset_uri and dataset_repo_id:
            dataset_uri = f"hf://{dataset_repo_id}"
            dataset_source_kind = "huggingface"

        description_lines = [
            f"LeRobot {policy_type} policy",
            f"Job: {job_name} (checkpoint {checkpoint_name})",
        ]
        if dataset_uri:
            description_lines.append(f"Dataset: {dataset_uri}")
        if azureml_run_id:
            description_lines.append(f"AML run: {azureml_run_id}")
        description = "\n".join(description_lines)

        tags = {
            "framework": "lerobot",
            "policy_type": policy_type,
            "job_name": job_name,
            "checkpoint": checkpoint_name,
            "source": source,
        }
        if dataset_source_kind:
            tags["dataset_source"] = dataset_source_kind
        if dataset_uri:
            tags["dataset_uri"] = dataset_uri
        if dataset_repo_id:
            tags["dataset_repo_id"] = dataset_repo_id
        if storage_account:
            tags["storage_account"] = storage_account
            tags["storage_container"] = storage_container or "datasets"
            if blob_prefix:
                tags["blob_prefix"] = blob_prefix
        if blob_urls:
            tags["blob_urls"] = blob_urls[0] if len(blob_urls) == 1 else json.dumps(blob_urls)
        if azureml_run_id:
            tags["azureml_run_id"] = azureml_run_id
        if mlflow_run_id:
            tags["mlflow_run_id"] = mlflow_run_id
        if experiment_id:
            tags["mlflow_experiment_id"] = experiment_id

        model = Model(
            path=str(checkpoint_path),
            name=model_name,
            description=description,
            type=AssetTypes.CUSTOM_MODEL,
            tags=tags,
        )
        registered = client.models.create_or_update(model)
        print(f"[AzureML] Registered: {registered.name} v{registered.version} ({checkpoint_name})")
        if dataset_uri:
            print(f"[AzureML] Lineage: {dataset_uri} -> {job_name} -> {registered.name}:v{registered.version}")
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
