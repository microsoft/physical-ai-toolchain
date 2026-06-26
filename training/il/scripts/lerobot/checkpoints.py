"""Checkpoint upload and model registration for LeRobot training."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
_AZUREML_TAG_VALUE_MAX_LENGTH = 256

# Bounded retry policy for the Azure ML model-registry SDK call. The call runs
# inline in the training loop, so total worst-case delay is capped at
# ~initial * (2^attempts - 1) seconds. Tests override these via monkeypatch.
_AML_REGISTER_MAX_ATTEMPTS = 3
_AML_REGISTER_INITIAL_BACKOFF_S = 1.0


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bounded_tag_value(value: str) -> str:
    if len(value) <= _AZUREML_TAG_VALUE_MAX_LENGTH:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    suffix = f"...sha256:{digest}"
    return f"{value[: _AZUREML_TAG_VALUE_MAX_LENGTH - len(suffix)]}{suffix}"


def _source_list_tag(values: list[str], plural: str) -> str:
    if len(values) == 1:
        return _bounded_tag_value(values[0])
    return f"{len(values)} {plural}; sha256:{_json_hash(values)[:16]}"


def _parse_with_diagnostic(raw: str, var_name: str) -> list[str]:
    """Parse a JSON-array env var, emitting a diagnostic on malformed JSON.

    Wraps :func:`parse_url_list_env` to preserve the operator-facing message
    that previously distinguished malformed payloads from legitimately empty
    ones; the library helper itself stays silent.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        print(f"[AzureML] Failed to parse {var_name}: {exc}. Falling back to other dataset source metadata.")
        return []
    if not isinstance(parsed, list):
        return []
    return [item.strip() for item in parsed if isinstance(item, str) and item.strip()]


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


def _create_model_with_retry(client: Any, model: Any, checkpoint_name: str) -> Any:
    """Call ``client.models.create_or_update`` with bounded exponential backoff.

    Retries any exception up to ``_AML_REGISTER_MAX_ATTEMPTS`` total attempts,
    sleeping ``_AML_REGISTER_INITIAL_BACKOFF_S`` seconds and doubling between
    attempts. The final attempt runs after the retry loop so its outcome —
    success or exception — is the function's outcome with no implicit
    fall-through.
    """
    backoff = _AML_REGISTER_INITIAL_BACKOFF_S
    for attempt in range(1, _AML_REGISTER_MAX_ATTEMPTS):
        try:
            return client.models.create_or_update(model)
        except Exception as exc:
            print(
                f"[AzureML] Registration attempt {attempt}/{_AML_REGISTER_MAX_ATTEMPTS} "
                f"failed for {checkpoint_name}: {exc}; retrying in {backoff:.1f}s"
            )
            if backoff > 0:
                time.sleep(backoff)
            backoff *= 2
    return client.models.create_or_update(model)


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

        # Lineage metadata: dataset -> job -> model. Tags stay intentionally
        # small for AzureML metadata limits; full URI lists are written into the
        # registered model artifact directory as azureml_lineage.json.
        #
        # Dataset env-var conventions:
        #   1. Blob submissions: BLOB_URLS (JSON array of canonical
        #      https://<account>.blob.core.windows.net/<container>/<prefix> URLs).
        #   2. AzureML data asset submissions: DATASET_ASSETS.
        #   3. HuggingFace fallback: DATASET_REPO_ID alone.
        dataset_repo_id = os.environ.get("DATASET_REPO_ID", "")
        blob_urls_json = os.environ.get("BLOB_URLS", "")
        dataset_assets_json = os.environ.get("DATASET_ASSETS", "")
        azureml_run_id = os.environ.get("AZUREML_RUN_ID", "") or os.environ.get("MLFLOW_RUN_ID", "")
        mlflow_run_id = os.environ.get("MLFLOW_RUN_ID", "")
        experiment_id = os.environ.get("MLFLOW_EXPERIMENT_ID", "")

        dataset_uri = ""
        dataset_source_kind = ""

        # parse_url_list_env tolerates whitespace and pretty-printed JSON, and
        # filters empty / non-string entries. The local wrapper preserves the
        # operator-facing diagnostic on malformed payloads.
        dataset_assets = _parse_with_diagnostic(dataset_assets_json, "DATASET_ASSETS")
        if dataset_assets:
            dataset_uri = dataset_assets[0] if len(dataset_assets) == 1 else " ".join(dataset_assets)
            dataset_source_kind = "azureml-data-asset"

        blob_urls = _parse_with_diagnostic(blob_urls_json, "BLOB_URLS")
        if blob_urls and not dataset_uri:
            dataset_uri = blob_urls[0] if len(blob_urls) == 1 else " ".join(blob_urls)
            dataset_source_kind = "azure-blob"

        # Combined case: both data assets and blob URLs present.
        if dataset_assets and blob_urls:
            all_uris = dataset_assets + blob_urls
            dataset_uri = " ".join(all_uris)
            dataset_source_kind = "mixed"

        if not dataset_uri and dataset_repo_id:
            dataset_uri = f"hf://{dataset_repo_id}"
            dataset_source_kind = "huggingface"

        lineage_uris = dataset_assets + blob_urls
        if not lineage_uris and dataset_uri:
            lineage_uris = [dataset_uri]
        lineage_hash = _json_hash(lineage_uris) if lineage_uris else ""
        lineage_summary = dataset_uri
        if len(lineage_uris) > 1:
            lineage_summary = (
                f"{dataset_source_kind}: {len(dataset_assets)} data asset(s), "
                f"{len(blob_urls)} blob URL(s); sha256:{lineage_hash[:16]}"
            )

        lineage = {
            "dataset_source": dataset_source_kind or None,
            "dataset_uri": dataset_uri or None,
            "dataset_summary": lineage_summary or None,
            "dataset_assets": dataset_assets,
            "blob_urls": blob_urls,
            "dataset_repo_id": dataset_repo_id or None,
            "azureml_run_id": azureml_run_id or None,
            "mlflow_run_id": mlflow_run_id or None,
            "mlflow_experiment_id": experiment_id or None,
        }
        lineage_path = checkpoint_path / "azureml_lineage.json"
        lineage_path.write_text(json.dumps(lineage, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        description_lines = [
            f"LeRobot {policy_type} policy",
            f"Job: {job_name} (checkpoint {checkpoint_name})",
            "Lineage artifact: azureml_lineage.json",
        ]
        if lineage_summary:
            description_lines.append(f"Dataset: {_bounded_tag_value(lineage_summary)}")
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
        if lineage_summary:
            tags["dataset_uri"] = _bounded_tag_value(lineage_summary)
        if lineage_hash:
            tags["dataset_lineage_sha256"] = lineage_hash
            tags["dataset_uri_count"] = str(len(lineage_uris))
        if dataset_repo_id:
            tags["dataset_repo_id"] = _bounded_tag_value(dataset_repo_id)
        if blob_urls:
            tags["blob_url_count"] = str(len(blob_urls))
            tags["blob_urls"] = _source_list_tag(blob_urls, "blob URLs")
        if dataset_assets:
            tags["dataset_asset_count"] = str(len(dataset_assets))
            tags["dataset_assets"] = _source_list_tag(dataset_assets, "data assets")
        if azureml_run_id:
            tags["azureml_run_id"] = _bounded_tag_value(azureml_run_id)
        if mlflow_run_id:
            tags["mlflow_run_id"] = _bounded_tag_value(mlflow_run_id)
        if experiment_id:
            tags["mlflow_experiment_id"] = _bounded_tag_value(experiment_id)
        tags = {key: _bounded_tag_value(str(value)) for key, value in tags.items()}

        model = Model(
            path=str(checkpoint_path),
            name=model_name,
            description=description,
            type=AssetTypes.CUSTOM_MODEL,
            tags=tags,
        )
        registered = _create_model_with_retry(client, model, checkpoint_name)
        print(f"[AzureML] Registered: {registered.name} v{registered.version} ({checkpoint_name})")
        if lineage_summary:
            print(f"[AzureML] Lineage: {lineage_summary} -> {job_name} -> {registered.name}:v{registered.version}")
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
                    print(f"[MLflow] Failed to log artifacts for {ckpt_dir.name}: {exc}; dropping MLflow artifact")

                # Intermediate checkpoints are disposable: drop on failure and
                # move on. The next checkpoint will be at least as good, and the
                # canonical final registration is handled by
                # ``register_final_checkpoint`` (which propagates a non-zero
                # exit code on failure).
                if not _register_model_via_aml(pretrained_dir, ckpt_dir.name, source=source):
                    print(f"[AzureML] Registration failed for {ckpt_dir.name}; dropping checkpoint")
                uploaded.add(ckpt_dir.name)


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

    if not _register_model_via_aml(checkpoint_path, checkpoint_name, source="osmo-workflow"):
        return EXIT_FAILURE

    return EXIT_SUCCESS
