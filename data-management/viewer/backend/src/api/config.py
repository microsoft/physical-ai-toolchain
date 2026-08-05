"""
Application configuration loaded from environment variables.

Reads storage backend settings, Azure credentials, server config,
and CORS origins. Creates and returns the correct storage adapters
for the configured backend.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CORS = "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5176,http://localhost:5177"


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration loaded from environment variables."""

    storage_backend: str
    """Active storage backend: 'local' or 'azure'."""

    data_path: str
    """Local dataset directory (DATA_DIR). Used when storage_backend='local'."""

    azure_account_name: str | None
    """Azure Storage account name. Required when storage_backend='azure'."""

    azure_dataset_container: str | None
    """Blob container holding dataset files (videos, parquet, HDF5)."""

    azure_annotation_container: str | None
    """Blob container holding annotation JSON files. Falls back to dataset container."""

    azure_sas_token: str | None
    """SAS token for Azure auth. When absent, DefaultAzureCredential is used."""

    backend_host: str
    """Bind host for uvicorn. Use 0.0.0.0 in container deployments."""

    backend_port: int
    """Bind port for uvicorn."""

    cors_origins: list[str] = field(default_factory=list)
    """Allowed CORS origins for the frontend."""

    episode_cache_capacity: int = 32
    """Max episodes held in the LRU cache. 0 disables caching."""

    episode_cache_max_mb: int = 100
    """Max memory budget for the LRU cache in megabytes. 0 means count-only."""

    vlm_judge_enabled: bool = False
    """Whether the VLM-as-judge router is mounted."""

    vlm_judge_backend: str = "echo"
    """Backend kind: 'qwen3-vl', 'openai-compat', or 'echo'."""

    vlm_judge_model_id: str = "Qwen/Qwen3-VL-4B-Instruct"
    """Model identifier for the chosen backend."""

    vlm_judge_model_revision: str | None = None
    """Immutable HF commit SHA pinning the qwen3-vl model download; None uses the mutable default branch."""

    vlm_judge_base_url: str | None = None
    """OpenAI-compatible base URL (vLLM, NIM, Azure OpenAI)."""

    vlm_judge_api_key: str | None = None
    """API key for the OpenAI-compatible backend."""

    vlm_judge_n_frames: int = 12
    """Number of frames sampled per episode."""

    vlm_judge_process_method: str = "gvl"
    """Process-reward method: 'gvl' (shuffle-and-rank) or 'chronological'."""

    vlm_judge_cache_dir: str | None = None
    """Directory for the SHA256-keyed result cache; None disables disk cache."""

    operator_adapter_mode: str = "disabled"
    """Operator implementation: 'disabled', 'simulated', or 'lerobot'."""

    operator_command_timeout_s: float = 5.0
    """Maximum wait for worker startup, command acknowledgement, or cleanup."""

    operator_host_lease_path: str | None = None
    """Required cooperative host lease path for LeRobot mode."""

    operator_worker_executable: str | None = None
    """Absolute protocol v2 worker executable path for LeRobot mode."""

    operator_policy_python: str | None = None
    """Python executable containing the trained GR00T runtime."""

    operator_policy_checkpoint: str | None = None
    """Server-selected GR00T checkpoint directory."""

    operator_policy_cuda_visible_devices: str | None = None
    """Optional CUDA device selector passed only to policy inference."""

    operator_startup_timeout_s: float = 30.0
    operator_stop_timeout_s: float = 5.0
    operator_recovery_timeout_s: float = 10.0


def load_config(env_path: Path | None = None) -> AppConfig:
    """
    Load application configuration from environment variables.

    Args:
        env_path: Optional path to a .env file to load before reading env.

    Returns:
        Populated AppConfig instance.
    """
    if env_path is not None:
        from dotenv import load_dotenv

        load_dotenv(env_path)

    storage_backend = os.environ.get("STORAGE_BACKEND", "local").lower()
    data_path = os.environ.get("DATA_DIR", "./data")

    azure_account_name = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME") or None
    azure_dataset_container = os.environ.get("AZURE_STORAGE_DATASET_CONTAINER") or None
    azure_annotation_container = (
        os.environ.get("AZURE_STORAGE_ANNOTATION_CONTAINER") or None
    )
    azure_sas_token = os.environ.get("AZURE_STORAGE_SAS_TOKEN") or None

    backend_host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    backend_port = int(os.environ.get("BACKEND_PORT", "8000"))

    cors_raw = os.environ.get("CORS_ORIGINS", _DEFAULT_CORS)
    cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]

    episode_cache_capacity = int(os.environ.get("EPISODE_CACHE_CAPACITY", "32"))
    episode_cache_max_mb = int(os.environ.get("EPISODE_CACHE_MAX_MB", "100"))

    vlm_judge_enabled = os.environ.get("VLM_JUDGE_ENABLED", "false").lower() == "true"
    vlm_judge_backend = os.environ.get("VLM_JUDGE_BACKEND", "echo").lower()
    vlm_judge_model_id = os.environ.get(
        "VLM_JUDGE_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct"
    )
    vlm_judge_model_revision = os.environ.get("VLM_JUDGE_MODEL_REVISION") or None
    vlm_judge_base_url = os.environ.get("VLM_JUDGE_BASE_URL") or None
    vlm_judge_api_key = os.environ.get("VLM_JUDGE_API_KEY") or None
    vlm_judge_n_frames = int(os.environ.get("VLM_JUDGE_N_FRAMES", "12"))
    vlm_judge_process_method = os.environ.get("VLM_JUDGE_PROCESS_METHOD", "gvl").lower()
    vlm_judge_cache_dir = os.environ.get("VLM_JUDGE_CACHE_DIR") or None

    operator_adapter_mode = os.environ.get("OPERATOR_ADAPTER_MODE", "disabled").lower()
    allowed_operator_modes = {"disabled", "simulated", "lerobot"}
    if operator_adapter_mode not in allowed_operator_modes:
        allowed = ", ".join(sorted(allowed_operator_modes))
        raise ValueError(f"OPERATOR_ADAPTER_MODE must be one of: {allowed}")
    operator_command_timeout_s = float(
        os.environ.get("OPERATOR_COMMAND_TIMEOUT_S", "5")
    )
    operator_host_lease_path = os.environ.get("OPERATOR_HOST_LEASE_PATH") or None
    operator_worker_executable = os.environ.get("OPERATOR_WORKER_EXECUTABLE") or None
    operator_policy_python = os.environ.get("OPERATOR_POLICY_PYTHON") or None
    operator_policy_checkpoint = os.environ.get("OPERATOR_POLICY_CHECKPOINT") or None
    operator_policy_cuda_visible_devices = (
        os.environ.get("OPERATOR_POLICY_CUDA_VISIBLE_DEVICES") or None
    )
    operator_startup_timeout_s = float(
        os.environ.get("OPERATOR_STARTUP_TIMEOUT_S", "30")
    )
    operator_stop_timeout_s = float(os.environ.get("OPERATOR_STOP_TIMEOUT_S", "5"))
    operator_recovery_timeout_s = float(
        os.environ.get("OPERATOR_RECOVERY_TIMEOUT_S", "10")
    )
    if (
        min(
            operator_startup_timeout_s,
            operator_stop_timeout_s,
            operator_recovery_timeout_s,
        )
        <= 0
    ):
        raise ValueError("Operator timeouts must be greater than zero")
    if operator_command_timeout_s <= 0:
        raise ValueError("OPERATOR_COMMAND_TIMEOUT_S must be greater than zero")
    if storage_backend == "azure" and operator_adapter_mode != "disabled":
        raise ValueError("Operator mode requires local storage")
    if (
        operator_adapter_mode != "disabled"
        and int(os.environ.get("WEB_CONCURRENCY", "1")) != 1
    ):
        raise ValueError("Operator mode requires exactly one backend worker")
    if operator_adapter_mode == "lerobot":
        if not operator_host_lease_path:
            raise ValueError("OPERATOR_HOST_LEASE_PATH is required for LeRobot mode")
        if os.environ.get("DATAVIEWER_AUTH_DISABLED", "false").lower() == "true":
            allowed = (
                os.environ.get(
                    "OPERATOR_ALLOW_UNAUTHENTICATED_LOOPBACK", "false"
                ).lower()
                == "true"
            )
            if not allowed or backend_host not in {"127.0.0.1", "::1", "localhost"}:
                raise ValueError(
                    "Unauthenticated LeRobot mode requires explicit loopback-only authorization"
                )

    return AppConfig(
        storage_backend=storage_backend,
        data_path=data_path,
        azure_account_name=azure_account_name,
        azure_dataset_container=azure_dataset_container,
        azure_annotation_container=azure_annotation_container,
        azure_sas_token=azure_sas_token,
        backend_host=backend_host,
        backend_port=backend_port,
        cors_origins=cors_origins,
        episode_cache_capacity=episode_cache_capacity,
        episode_cache_max_mb=episode_cache_max_mb,
        vlm_judge_enabled=vlm_judge_enabled,
        vlm_judge_backend=vlm_judge_backend,
        vlm_judge_model_id=vlm_judge_model_id,
        vlm_judge_model_revision=vlm_judge_model_revision,
        vlm_judge_base_url=vlm_judge_base_url,
        vlm_judge_api_key=vlm_judge_api_key,
        vlm_judge_n_frames=vlm_judge_n_frames,
        vlm_judge_process_method=vlm_judge_process_method,
        vlm_judge_cache_dir=vlm_judge_cache_dir,
        operator_adapter_mode=operator_adapter_mode,
        operator_command_timeout_s=operator_command_timeout_s,
        operator_host_lease_path=operator_host_lease_path,
        operator_worker_executable=operator_worker_executable,
        operator_policy_python=operator_policy_python,
        operator_policy_checkpoint=operator_policy_checkpoint,
        operator_policy_cuda_visible_devices=operator_policy_cuda_visible_devices,
        operator_startup_timeout_s=operator_startup_timeout_s,
        operator_stop_timeout_s=operator_stop_timeout_s,
        operator_recovery_timeout_s=operator_recovery_timeout_s,
    )


def create_annotation_storage(config: AppConfig):
    """
    Create the annotation storage adapter based on config.

    Returns LocalStorageAdapter when storage_backend='local', or
    AzureBlobStorageAdapter (using DefaultAzureCredential) when storage_backend='azure'.

    Args:
        config: Application configuration.

    Returns:
        Configured StorageAdapter instance.

    Raises:
        ValueError: If azure backend is requested but required config is missing.
        ImportError: If azure extras are not installed.
    """
    from .storage import LocalStorageAdapter

    if config.storage_backend == "azure":
        if not config.azure_account_name:
            raise ValueError(
                "AZURE_STORAGE_ACCOUNT_NAME is required when STORAGE_BACKEND=azure"
            )
        annotation_container = (
            config.azure_annotation_container or config.azure_dataset_container
        )
        if not annotation_container:
            raise ValueError(
                "AZURE_STORAGE_ANNOTATION_CONTAINER or AZURE_STORAGE_DATASET_CONTAINER is required "
                "when STORAGE_BACKEND=azure"
            )

        from .storage.azure import AzureBlobStorageAdapter

        logger.info(
            "Annotation storage: Azure Blob — account=%s container=%s auth=%s",
            config.azure_account_name,
            annotation_container,
            "SAS token" if config.azure_sas_token else "DefaultAzureCredential (MSI)",
        )
        return AzureBlobStorageAdapter(
            account_name=config.azure_account_name,
            container_name=annotation_container,
            sas_token=config.azure_sas_token,
            use_managed_identity=config.azure_sas_token is None,
        )

    logger.info("Annotation storage: local filesystem — path=%s", config.data_path)
    return LocalStorageAdapter(config.data_path)


def create_blob_dataset_provider(config: AppConfig):
    """
    Create a BlobDatasetProvider when storage_backend='azure'.

    Returns None when storage_backend='local' or azure config is incomplete.

    Args:
        config: Application configuration.

    Returns:
        BlobDatasetProvider instance or None.
    """
    if config.storage_backend != "azure":
        return None
    if not config.azure_account_name or not config.azure_dataset_container:
        logger.warning(
            "Blob dataset provider skipped: AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_DATASET_CONTAINER are required"
        )
        return None

    try:
        from .storage.blob_dataset import BlobDatasetProvider

        logger.info(
            "Dataset storage: Azure Blob — account=%s container=%s auth=%s",
            config.azure_account_name,
            config.azure_dataset_container,
            "SAS token" if config.azure_sas_token else "DefaultAzureCredential (MSI)",
        )
        return BlobDatasetProvider(
            account_name=config.azure_account_name,
            container_name=config.azure_dataset_container,
            sas_token=config.azure_sas_token,
        )
    except ImportError:
        logger.warning(
            "BlobDatasetProvider unavailable: install azure-storage-blob and azure-identity"
        )
        return None


# Global config instance (populated on first load_app_config() call)
_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """Return the global AppConfig, loading it on first call."""
    global _app_config
    if _app_config is None:
        _app_config = load_config()
    return _app_config
