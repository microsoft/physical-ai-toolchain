"""Azure ML bootstrap helpers for IsaacLab training entrypoints.

Provides ``bootstrap_azure_ml`` to initialize an Azure ML workspace
connection, configure MLflow tracking, and optionally set up Azure
Blob Storage for blob uploads.

Required dependencies: ``azure-ai-ml``, ``azure-identity``, ``mlflow``.
Optional: ``azure-storage-blob`` (for blob uploads via
``AzureStorageContext``).
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mlflow  # type: ignore[import-not-found]
from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential

if TYPE_CHECKING:  # pragma: no cover - optional dependency import guard
    from azure.storage.blob import BlobServiceClient

from training.utils.env import require_env, set_env_defaults


class AzureConfigError(RuntimeError):
    """Raised when required Azure ML configuration is unavailable."""


@dataclass(frozen=True)
class AzureStorageContext:
    """Azure Blob Storage client wrapper for checkpoint and artifact uploads.

    Wraps a ``BlobServiceClient`` with convenience methods for uploading
    single files, batches of files, and training checkpoints. All uploads
    overwrite existing blobs with the same name.

    Attributes:
        blob_client: Authenticated Azure Blob Storage service client.
        container_name: Target container for all upload operations.
    """

    blob_client: BlobServiceClient
    container_name: str

    def upload_file(self, *, local_path: str, blob_name: str) -> str:
        """Upload a single file to Azure Blob Storage.

        Reads the file at *local_path* and uploads it to the configured
        container under *blob_name*. Overwrites any existing blob with
        the same name.

        Args:
            local_path: Absolute or relative path to the local file.
            blob_name: Destination blob name within the container.

        Returns:
            The blob name of the uploaded file.

        Raises:
            FileNotFoundError: When *local_path* does not point to an
                existing file.
        """
        file_path = Path(local_path)
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {local_path} (destination: {self.container_name}/{blob_name})")

        blob = self.blob_client.get_blob_client(
            container=self.container_name,
            blob=blob_name,
        )
        with file_path.open("rb") as data_stream:
            blob.upload_blob(data_stream, overwrite=True)
        return blob_name

    def upload_files_batch(self, files: list[tuple[str, str]]) -> list[str]:
        """Upload multiple files in parallel using Azure SDK.

        Operates on a best-effort basis. Individual upload failures are
        caught and reported via printed warnings rather than raised.
        Failed files are omitted from the return value.

        Args:
            files: List of (local_path, blob_name) tuples.

        Returns:
            List of successfully uploaded blob names. May be shorter
            than *files* when individual uploads fail.
        """
        if not files:
            return []

        from concurrent.futures import ThreadPoolExecutor, as_completed

        uploaded_blobs = []
        failed_uploads = []

        with ThreadPoolExecutor(max_workers=min(10, len(files))) as executor:
            future_to_file = {
                executor.submit(self.upload_file, local_path=local_path, blob_name=blob_name): (
                    local_path,
                    blob_name,
                )
                for local_path, blob_name in files
            }

            for future in as_completed(future_to_file):
                local_path, _blob_name = future_to_file[future]
                try:
                    result = future.result()
                    uploaded_blobs.append(result)
                except Exception as exc:
                    failed_uploads.append((local_path, str(exc)))

        if failed_uploads:
            print(f"[WARNING] Failed to upload {len(failed_uploads)} files:")
            for local_path, error in failed_uploads[:5]:
                print(f"  - {local_path}: {error}")
            if len(failed_uploads) > 5:
                print(f"  ... and {len(failed_uploads) - 5} more")

        return uploaded_blobs

    def upload_checkpoint(
        self,
        *,
        local_path: str,
        model_name: str,
        step: int | None = None,
    ) -> str:
        """Upload a training checkpoint with an auto-generated blob name.

        Constructs a blob path under ``checkpoints/{model_name}/`` using a
        UTC timestamp and optional training step number. The file extension
        from *local_path* is preserved.

        Blob name format: ``checkpoints/{model_name}/{YYYYMMDD_HHMMSS}[_step_{N}]{ext}``

        Args:
            local_path: Path to the local checkpoint file.
            model_name: Model identifier used as a directory prefix.
            step: Optional training step number appended to the blob name.

        Returns:
            The generated blob name of the uploaded checkpoint.

        Raises:
            FileNotFoundError: When *local_path* does not point to an
                existing file (raised by ``upload_file``).
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        file_path = Path(local_path)
        suffix = file_path.suffix or ""
        step_segment = f"_step_{step}" if step is not None else ""
        blob_name = f"checkpoints/{model_name}/{timestamp}{step_segment}{suffix}"
        return self.upload_file(local_path=local_path, blob_name=blob_name)


@dataclass(frozen=True)
class AzureMLContext:
    """Azure ML workspace connection context.

    Holds the authenticated ``MLClient``, MLflow tracking URI, and an
    optional ``AzureStorageContext`` for checkpoint uploads. Created by
    ``bootstrap_azure_ml``.

    Attributes:
        client: Authenticated Azure ML workspace client.
        tracking_uri: MLflow tracking URI for the workspace.
        workspace_name: Name of the connected Azure ML workspace.
        storage: Optional storage context for blob uploads, or ``None``
            when ``AZURE_STORAGE_ACCOUNT_NAME`` is not set.
    """

    client: MLClient
    tracking_uri: str
    workspace_name: str
    storage: AzureStorageContext | None = None


def _optional_env(name: str) -> str | None:
    """Return the value of an environment variable, or ``None`` when unset or empty.

    Args:
        name: Environment variable name to look up.

    Returns:
        Non-empty string value, or ``None`` when the variable is unset
        or contains an empty string.
    """
    value = os.environ.get(name)
    return value or None


def _build_storage_context(credential: Any) -> AzureStorageContext | None:
    """Build an Azure Blob Storage context if storage is configured.

    Creates a ``BlobServiceClient`` using the provided credential and
    the account URL derived from ``AZURE_STORAGE_ACCOUNT_NAME``. Creates
    the target container if it does not exist.

    Returns ``None`` when ``AZURE_STORAGE_ACCOUNT_NAME`` is not set,
    allowing callers to treat storage as an optional feature.

    Args:
        credential: Azure credential instance for authentication.

    Returns:
        Configured ``AzureStorageContext``, or ``None`` when storage
        is not configured.

    Raises:
        AzureConfigError: When ``azure-storage-blob`` is not installed
            but ``AZURE_STORAGE_ACCOUNT_NAME`` is set.
        AzureConfigError: When container initialization fails due to
            an Azure service error.
    """
    account_name = _optional_env("AZURE_STORAGE_ACCOUNT_NAME")
    if not account_name:
        return None

    try:
        from azure.core.exceptions import AzureError, ResourceExistsError
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise AzureConfigError(
            "azure-storage-blob is required for Azure Blob Storage uploads. "
            "Install the package or unset AZURE_STORAGE_ACCOUNT_NAME."
        ) from exc

    container_name = _optional_env("AZURE_STORAGE_CONTAINER_NAME") or "isaaclab-training-logs"
    account_url = f"https://{account_name}.blob.core.windows.net/"

    try:
        blob_client = BlobServiceClient(account_url=account_url, credential=credential)
        container_client = blob_client.get_container_client(container_name)
        with contextlib.suppress(ResourceExistsError):
            container_client.create_container()
        return AzureStorageContext(blob_client=blob_client, container_name=container_name)
    except AzureError as exc:
        raise AzureConfigError(
            f"Failed to initialize Azure Storage container '{container_name}' in account '{account_name}': {exc}"
        ) from exc


def _build_credential() -> DefaultAzureCredential:
    """Build an Azure credential with managed identity support.

    Configures ``DefaultAzureCredential`` with optional managed identity
    client ID from ``AZURE_CLIENT_ID`` or ``DEFAULT_IDENTITY_CLIENT_ID``
    (fallback). Set ``AZURE_EXCLUDE_MANAGED_IDENTITY=true`` to skip the
    managed identity credential when running on Azure VMs during local
    development.

    Returns:
        Configured ``DefaultAzureCredential`` instance.
    """
    managed_identity_client_id = os.environ.get("AZURE_CLIENT_ID")
    default_identity_client_id = os.environ.get("DEFAULT_IDENTITY_CLIENT_ID")

    if not managed_identity_client_id and default_identity_client_id:
        managed_identity_client_id = default_identity_client_id
        os.environ.setdefault("AZURE_CLIENT_ID", default_identity_client_id)

    # Check if we should exclude managed identity (for local dev on Azure VMs)
    exclude_managed_identity = os.environ.get("AZURE_EXCLUDE_MANAGED_IDENTITY", "false").lower() == "true"

    return DefaultAzureCredential(
        managed_identity_client_id=managed_identity_client_id,
        authority=os.environ.get("AZURE_AUTHORITY_HOST"),
        exclude_managed_identity_credential=exclude_managed_identity,
    )


def bootstrap_azure_ml(
    *,
    experiment_name: str,
) -> AzureMLContext:
    """Initialize an Azure ML workspace connection and configure MLflow.

    Reads required configuration from environment variables, creates an
    authenticated ML client, configures MLflow tracking, and optionally
    initializes Azure Blob Storage for checkpoint uploads.

    Required environment variables:
        - ``AZURE_SUBSCRIPTION_ID``: Azure subscription identifier.
        - ``AZURE_RESOURCE_GROUP``: Resource group containing the workspace.
        - ``AZUREML_WORKSPACE_NAME``: Azure ML workspace name.

    Optional environment variables:
        - ``AZURE_STORAGE_ACCOUNT_NAME``: Enables checkpoint storage when set.
        - ``AZURE_STORAGE_CONTAINER_NAME``: Blob container name
          (default: ``"isaaclab-training-logs"``).
        - ``AZURE_CLIENT_ID``: Managed identity client ID.
        - ``DEFAULT_IDENTITY_CLIENT_ID``: Fallback for ``AZURE_CLIENT_ID``.
        - ``AZURE_EXCLUDE_MANAGED_IDENTITY``: Set to ``"true"`` to skip
          managed identity credential.
        - ``AZURE_AUTHORITY_HOST``: Azure AD authority host override.

    Args:
        experiment_name: MLflow experiment name to set as active.

    Returns:
        Fully initialized ``AzureMLContext`` with workspace client,
        tracking URI, and optional storage context.

    Raises:
        AzureConfigError: When a required environment variable is missing
            or empty.
        AzureConfigError: When the Azure ML client cannot be created.
        AzureConfigError: When the workspace cannot be accessed.
        AzureConfigError: When the workspace does not expose an MLflow
            tracking URI.
        AzureConfigError: When MLflow tracking configuration fails.

    Example:
        >>> ctx = bootstrap_azure_ml(experiment_name="isaaclab-cartpole")
        >>> ctx.client.workspaces.get(ctx.workspace_name)
        >>> if ctx.storage:
        ...     ctx.storage.upload_checkpoint(
        ...         local_path="model.pt", model_name="cartpole"
        ...     )
    """
    subscription_id = require_env("AZURE_SUBSCRIPTION_ID", error_type=AzureConfigError)
    resource_group = require_env("AZURE_RESOURCE_GROUP", error_type=AzureConfigError)
    workspace_name = require_env("AZUREML_WORKSPACE_NAME", error_type=AzureConfigError)

    set_env_defaults(
        {
            "MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES": "3",
            "MLFLOW_HTTP_REQUEST_TIMEOUT": "60",
        }
    )

    credential = _build_credential()

    try:
        client = MLClient(
            credential=credential,
            subscription_id=subscription_id,
            resource_group_name=resource_group,
            workspace_name=workspace_name,
        )
    except Exception as exc:
        raise AzureConfigError(f"Failed to create Azure ML client: {exc}") from exc

    try:
        workspace = client.workspaces.get(workspace_name)
    except Exception as exc:
        raise AzureConfigError(f"Failed to access workspace {workspace_name}: {exc}") from exc

    tracking_uri = getattr(workspace, "mlflow_tracking_uri", None)
    if not tracking_uri:
        raise AzureConfigError("Azure ML workspace does not expose an MLflow tracking URI")

    try:
        mlflow.set_tracking_uri(tracking_uri)
        if experiment_name:
            mlflow.set_experiment(experiment_name)
    except Exception as exc:
        raise AzureConfigError(f"Failed to configure MLflow tracking: {exc}") from exc

    storage_context = _build_storage_context(credential)

    return AzureMLContext(
        client=client,
        tracking_uri=tracking_uri,
        workspace_name=workspace_name,
        storage=storage_context,
    )
