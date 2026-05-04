"""
Azure Blob Storage adapter for annotations.

Supports both SAS token and managed identity authentication.
"""

from __future__ import annotations

import json

from ..models.annotations import EpisodeAnnotationFile
from .base import StorageAdapter, StorageError
from .paths import dataset_id_to_blob_prefix
from .serializers import DateTimeEncoder

# Azure SDK imports are optional - only required when using this adapter
try:
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
    from azure.identity.aio import DefaultAzureCredential
    from azure.storage.blob import ContentSettings
    from azure.storage.blob.aio import BlobServiceClient

    AZURE_AVAILABLE = True
except ImportError:
    # Distinct sentinel subclasses so `except` clauses don't accidentally
    # match unrelated exceptions when the SDK isn't installed. Tests patch
    # these module attributes to inject their own classes.
    class _HttpResponseErrorStub(Exception):
        """Sentinel for HttpResponseError when azure SDK is unavailable."""

    class _ResourceNotFoundErrorStub(Exception):
        """Sentinel for ResourceNotFoundError when azure SDK is unavailable."""

    HttpResponseError = _HttpResponseErrorStub
    ResourceNotFoundError = _ResourceNotFoundErrorStub
    DefaultAzureCredential = None
    ContentSettings = None
    BlobServiceClient = None
    AZURE_AVAILABLE = False


class AzureBlobStorageAdapter(StorageAdapter):
    """
    Azure Blob Storage adapter for annotation persistence.

    Stores annotations in the container's annotations/episodes/ path,
    with each episode having its own JSON blob.
    """

    def __init__(
        self,
        account_name: str,
        container_name: str,
        sas_token: str | None = None,
        use_managed_identity: bool = False,
    ):
        """
        Initialize the Azure Blob Storage adapter.

        Args:
            account_name: Azure storage account name.
            container_name: Blob container name.
            sas_token: SAS token for authentication (optional).
            use_managed_identity: Use managed identity for auth (optional).

        Raises:
            ImportError: If azure-storage-blob is not installed.
            ValueError: If neither SAS token nor managed identity is specified.
        """
        if not AZURE_AVAILABLE:
            raise ImportError(
                "Azure Blob Storage support requires azure-storage-blob and "
                "azure-identity. Install with: pip install azure-storage-blob azure-identity"
            )

        if not sas_token and not use_managed_identity:
            raise ValueError("Either sas_token or use_managed_identity must be specified")

        self.account_name = account_name
        self.container_name = container_name
        self.sas_token = sas_token
        self.use_managed_identity = use_managed_identity
        self._client: BlobServiceClient | None = None

    async def _get_client(self) -> BlobServiceClient:
        """Get or create the blob service client."""
        if self._client is None:
            account_url = f"https://{self.account_name}.blob.core.windows.net"

            if self.sas_token:
                self._client = BlobServiceClient(
                    account_url=account_url,
                    credential=self.sas_token,
                )
            else:
                credential = DefaultAzureCredential()
                self._client = BlobServiceClient(
                    account_url=account_url,
                    credential=credential,
                )

        return self._client

    def _get_blob_path(self, dataset_id: str, episode_index: int) -> str:
        """Get the blob path for an episode's annotations. Resolves -- to /."""
        blob_prefix = dataset_id_to_blob_prefix(dataset_id)
        return f"{blob_prefix}/annotations/episodes/episode_{episode_index:06d}.json"

    async def get_annotation(self, dataset_id: str, episode_index: int) -> EpisodeAnnotationFile | None:
        """
        Retrieve annotations for an episode from Azure Blob Storage.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.

        Returns:
            EpisodeAnnotationFile if annotations exist, None otherwise.
        """
        blob_path = self._get_blob_path(dataset_id, episode_index)

        try:
            client = await self._get_client()
            container_client = client.get_container_client(self.container_name)
            blob_client = container_client.get_blob_client(blob_path)

            download = await blob_client.download_blob()
            content = await download.readall()
            data = json.loads(content.decode("utf-8"))
            return EpisodeAnnotationFile.model_validate(data)

        except ResourceNotFoundError:
            return None
        except json.JSONDecodeError as e:
            raise StorageError(f"Invalid JSON in blob {blob_path}: {e}", cause=e)
        except HttpResponseError as e:
            raise StorageError(
                f"Azure HTTP error reading blob {blob_path}: status={e.status_code} error_code={e.error_code}",
                cause=e,
            )
        except Exception as e:
            raise StorageError(f"Failed to read blob {blob_path}: {e}", cause=e)

    async def save_annotation(self, dataset_id: str, episode_index: int, annotation: EpisodeAnnotationFile) -> None:
        """
        Save annotations for an episode to Azure Blob Storage.

        Uses ETag-based optimistic concurrency control.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.
            annotation: Complete annotation file to save.

        Raises:
            StorageError: If the save operation fails.
        """
        blob_path = self._get_blob_path(dataset_id, episode_index)

        try:
            client = await self._get_client()
            container_client = client.get_container_client(self.container_name)
            blob_client = container_client.get_blob_client(blob_path)

            json_content = json.dumps(
                annotation.model_dump(mode="json"),
                indent=2,
                cls=DateTimeEncoder,
            )

            await blob_client.upload_blob(
                json_content.encode("utf-8"),
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )

        except HttpResponseError as e:
            raise StorageError(
                f"Azure HTTP error saving blob {blob_path}: status={e.status_code} error_code={e.error_code}",
                cause=e,
            )
        except Exception as e:
            raise StorageError(f"Failed to save blob {blob_path}: {e}", cause=e)

    async def list_annotated_episodes(self, dataset_id: str) -> list[int]:
        """
        List all episode indices with annotations for a dataset.

        Args:
            dataset_id: Unique identifier for the dataset.

        Returns:
            Sorted list of episode indices that have annotations.
        """
        prefix = f"{dataset_id_to_blob_prefix(dataset_id)}/annotations/episodes/episode_"

        try:
            client = await self._get_client()
            container_client = client.get_container_client(self.container_name)

            episode_indices = []
            async for blob in container_client.list_blobs(name_starts_with=prefix):
                # Extract episode index from blob name
                blob_name = blob.name
                if blob_name.endswith(".json"):
                    try:
                        # Get the filename part after the last /
                        filename = blob_name.split("/")[-1]
                        index_str = filename[8:-5]  # Remove "episode_" and ".json"
                        episode_indices.append(int(index_str))
                    except ValueError:
                        continue

            return sorted(episode_indices)

        except HttpResponseError as e:
            raise StorageError(
                f"Azure HTTP error listing annotations for {dataset_id}: "
                f"status={e.status_code} error_code={e.error_code}",
                cause=e,
            )
        except Exception as e:
            raise StorageError(f"Failed to list annotations for {dataset_id}: {e}", cause=e)

    async def delete_annotation(self, dataset_id: str, episode_index: int) -> bool:
        """
        Delete annotations for an episode from Azure Blob Storage.

        Args:
            dataset_id: Unique identifier for the dataset.
            episode_index: Index of the episode within the dataset.

        Returns:
            True if annotations were deleted, False if they didn't exist.
        """
        blob_path = self._get_blob_path(dataset_id, episode_index)

        try:
            client = await self._get_client()
            container_client = client.get_container_client(self.container_name)
            blob_client = container_client.get_blob_client(blob_path)

            await blob_client.delete_blob()
            return True

        except ResourceNotFoundError:
            return False
        except HttpResponseError as e:
            raise StorageError(
                f"Azure HTTP error deleting blob {blob_path}: status={e.status_code} error_code={e.error_code}",
                cause=e,
            )
        except Exception as e:
            raise StorageError(f"Failed to delete blob {blob_path}: {e}", cause=e)

    async def close(self) -> None:
        """Close the blob service client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
