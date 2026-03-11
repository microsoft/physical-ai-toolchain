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

_DEFAULT_CORS = (
    "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5176,http://localhost:5177"
)


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration loaded from environment variables."""

    storage_backend: str
    """Active storage backend: 'local' or 'azure'."""

    data_path: str
    """Local dataset directory (HMI_DATA_PATH). Used when storage_backend='local'."""

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

    storage_backend = os.environ.get("HMI_STORAGE_BACKEND", "local").lower()
    data_path = os.environ.get("HMI_DATA_PATH", "./data")

    azure_account_name = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME") or None
    azure_dataset_container = os.environ.get("AZURE_STORAGE_DATASET_CONTAINER") or None
    azure_annotation_container = os.environ.get("AZURE_STORAGE_ANNOTATION_CONTAINER") or None
    azure_sas_token = os.environ.get("AZURE_STORAGE_SAS_TOKEN") or None

    backend_host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    backend_port = int(os.environ.get("BACKEND_PORT", "8000"))

    cors_raw = os.environ.get("CORS_ORIGINS", _DEFAULT_CORS)
    cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]

    episode_cache_capacity = int(os.environ.get("EPISODE_CACHE_CAPACITY", "32"))
    episode_cache_max_mb = int(os.environ.get("EPISODE_CACHE_MAX_MB", "100"))

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
            raise ValueError("AZURE_STORAGE_ACCOUNT_NAME is required when HMI_STORAGE_BACKEND=azure")
        annotation_container = config.azure_annotation_container or config.azure_dataset_container
        if not annotation_container:
            raise ValueError(
                "AZURE_STORAGE_ANNOTATION_CONTAINER or AZURE_STORAGE_DATASET_CONTAINER is required "
                "when HMI_STORAGE_BACKEND=azure"
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
        logger.warning("BlobDatasetProvider unavailable: install azure-storage-blob and azure-identity")
        return None


# Global config instance (populated on first load_app_config() call)
_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """Return the global AppConfig, loading it on first call."""
    global _app_config
    if _app_config is None:
        _app_config = load_config()
    return _app_config
