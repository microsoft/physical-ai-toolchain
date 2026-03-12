"""
Azure Blob Storage provider for dataset file access.

Provides read-only access to dataset files (metadata, parquet, videos)
stored in Azure Blob Storage. Authenticates via DefaultAzureCredential
(managed identity, workload identity, environment credentials) when no
SAS token is provided.

Expected blob layout per dataset:
    {dataset_id}/meta/info.json
    {dataset_id}/meta/stats.json
    {dataset_id}/meta/tasks.parquet
    {dataset_id}/meta/episodes/chunk-{chunk:03d}/file-{file:03d}.parquet
    {dataset_id}/data/chunk-{chunk:03d}/file-{file:03d}.parquet
    {dataset_id}/videos/{camera}/chunk-{chunk:03d}/file-{file:03d}.mp4
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from .paths import dataset_id_to_blob_prefix

logger = logging.getLogger(__name__)

try:
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential
    from azure.storage.blob.aio import BlobServiceClient

    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    ResourceNotFoundError = Exception  # type: ignore[assignment,misc]
    AsyncDefaultAzureCredential = None  # type: ignore[assignment,misc]
    BlobServiceClient = None  # type: ignore[assignment]

_SYNC_META_BLOBS = {
    "meta/info.json",
    "meta/stats.json",
    "meta/tasks.parquet",
}


class BlobDatasetProvider:
    """
    Read-only access to dataset files in Azure Blob Storage.

    Authenticates via DefaultAzureCredential (MSI / workload identity /
    environment credentials) when no SAS token is provided.
    """

    def __init__(
        self,
        account_name: str,
        container_name: str,
        sas_token: str | None = None,
    ):
        """
        Initialize the blob dataset provider.

        Args:
            account_name: Azure Storage account name.
            container_name: Blob container holding dataset files.
            sas_token: Optional SAS token. DefaultAzureCredential is used when absent.

        Raises:
            ImportError: If azure-storage-blob or azure-identity is not installed.
        """
        if not AZURE_AVAILABLE:
            raise ImportError(
                "BlobDatasetProvider requires azure-storage-blob and azure-identity. "
                "Install with: pip install 'lerobot-annotation-api[azure]'"
            )

        self.account_name = account_name
        self.container_name = container_name
        self.sas_token = sas_token
        self._client: BlobServiceClient | None = None
        self._info_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_blob_prefix(dataset_id: str) -> str:
        """Convert a --separated dataset ID to a /-separated blob prefix."""
        return dataset_id_to_blob_prefix(dataset_id)

    async def _get_client(self) -> BlobServiceClient:
        """Return a lazily-initialized async BlobServiceClient."""
        if self._client is None:
            account_url = f"https://{self.account_name}.blob.core.windows.net"
            if self.sas_token:
                self._client = BlobServiceClient(
                    account_url=account_url,
                    credential=self.sas_token,
                )
            else:
                credential = AsyncDefaultAzureCredential()
                self._client = BlobServiceClient(
                    account_url=account_url,
                    credential=credential,
                )
        return self._client

    async def _read_blob_bytes(self, blob_path: str) -> bytes | None:
        """Download and return all bytes for a blob, or None if not found."""
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            blob_client = container.get_blob_client(blob_path)
            download = await blob_client.download_blob()
            return await download.readall()
        except ResourceNotFoundError:
            return None
        except Exception as e:
            logger.warning("Failed to read blob '%s': %s", blob_path, e)
            return None

    # ------------------------------------------------------------------
    # Dataset discovery
    # ------------------------------------------------------------------

    async def list_dataset_ids(self) -> list[str]:
        """List LeRobot dataset IDs by scanning for meta/info.json markers."""
        result = await self.scan_all_dataset_ids()
        return result["lerobot"]

    async def list_hdf5_dataset_ids(self) -> list[str]:
        """List HDF5 dataset IDs by scanning for .hdf5 episode files."""
        result = await self.scan_all_dataset_ids()
        return result["hdf5"]

    async def scan_all_dataset_ids(self) -> dict[str, list[str]]:
        """Single-pass scan that discovers both LeRobot and HDF5 datasets.

        Uses list_blob_names() for performance. Classifies each blob by
        checking for meta/info.json (LeRobot) or .hdf5 (HDF5 episodes).

        Returns:
            Dict with 'lerobot' and 'hdf5' keys, each a sorted list of dataset IDs.
        """
        lerobot_ids: set[str] = set()
        hdf5_ids: set[str] = set()
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            async for name in container.list_blob_names():
                parts = name.split("/")
                if len(parts) >= 3 and parts[-2] == "meta" and parts[-1] == "info.json":
                    lerobot_ids.add(parts[0])
                elif name.endswith(".hdf5"):
                    parent_parts = name.rsplit("/", 1)
                    if len(parent_parts) == 2:
                        segments = parent_parts[0].split("/")
                        if len(segments) <= 5:
                            hdf5_ids.add("--".join(segments))
        except Exception as e:
            logger.warning("Failed to scan blob container '%s': %s", self.container_name, e)

        # Exclude datasets already discovered as LeRobot
        hdf5_ids -= lerobot_ids

        return {
            "lerobot": sorted(lerobot_ids),
            "hdf5": sorted(hdf5_ids),
        }

    async def dataset_exists(self, dataset_id: str) -> bool:
        """Return True if the dataset has a meta/info.json blob."""
        blob_path = f"{self.get_blob_prefix(dataset_id)}/meta/info.json"
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            blob_client = container.get_blob_client(blob_path)
            await blob_client.get_blob_properties()
            return True
        except ResourceNotFoundError:
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Metadata access
    # ------------------------------------------------------------------

    async def get_info_json(self, dataset_id: str) -> dict | None:
        """
        Read and cache meta/info.json for a dataset.

        Args:
            dataset_id: Dataset identifier.

        Returns:
            Parsed JSON dict or None if not found.
        """
        if dataset_id in self._info_cache:
            return self._info_cache[dataset_id]

        data = await self._read_blob_bytes(f"{self.get_blob_prefix(dataset_id)}/meta/info.json")
        if data is None:
            return None

        try:
            info = json.loads(data.decode("utf-8"))
            self._info_cache[dataset_id] = info
            return info
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in info.json for dataset '%s': %s", dataset_id, e)
            return None

    # ------------------------------------------------------------------
    # Blob properties
    # ------------------------------------------------------------------

    async def get_blob_properties(self, blob_path: str) -> dict | None:
        """
        Return size and content_type for a blob, or None if not found.

        Args:
            blob_path: Full blob path within the container.

        Returns:
            Dict with 'size' (int) and 'content_type' (str), or None.
        """
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            blob_client = container.get_blob_client(blob_path)
            props = await blob_client.get_blob_properties()
            return {
                "size": props.size,
                "content_type": props.content_settings.content_type or "application/octet-stream",
            }
        except ResourceNotFoundError:
            return None
        except Exception as e:
            logger.warning("Failed to get properties for blob '%s': %s", blob_path, e)
            return None

    # ------------------------------------------------------------------
    # Video access
    # ------------------------------------------------------------------

    async def resolve_video_blob_path(
        self,
        dataset_id: str,
        episode_idx: int,
        camera: str,
    ) -> str | None:
        """
        Resolve the blob path for a LeRobot v3 video file.

        Reads chunks_size from meta/info.json to compute the correct
        chunk and file indices for the given episode.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.
            camera: Camera key (e.g. 'observation.images.color').

        Returns:
            Blob path string if found, None otherwise.
        """
        info = await self.get_info_json(dataset_id)
        chunks_size = int((info or {}).get("chunks_size", 1000))

        chunk_index = episode_idx // chunks_size
        file_index = episode_idx % chunks_size
        prefix = self.get_blob_prefix(dataset_id)
        blob_path = f"{prefix}/videos/{camera}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"

        props = await self.get_blob_properties(blob_path)
        if props is not None:
            return blob_path

        # Fallback: scan camera prefix for any matching mp4 (HDF5-style flat layout)
        video_prefix = f"{prefix}/videos/{camera}/"
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            async for blob in container.list_blobs(name_starts_with=video_prefix):
                if blob.name.endswith(".mp4"):
                    return blob.name
        except Exception as e:
            logger.warning(
                "Fallback video scan failed for %s ep%d %s: %s",
                dataset_id,
                episode_idx,
                camera,
                e,
            )
        return None

    async def stream_video(
        self,
        blob_path: str,
        chunk_size: int = 1024 * 1024,
        offset: int | None = None,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        """
        Stream video bytes from blob in chunks.

        Args:
            blob_path: Full blob path within the container.
            chunk_size: Streaming chunk size in bytes (default 1 MiB).
            offset: Starting byte offset for partial download.
            length: Number of bytes to download from offset.

        Yields:
            Bytes chunks of the video stream.
        """
        client = await self._get_client()
        container = client.get_container_client(self.container_name)
        blob_client = container.get_blob_client(blob_path)
        download = await blob_client.download_blob(
            offset=offset,
            length=length,
            max_concurrency=4,
        )
        async for chunk in download.chunks():
            yield chunk

    # ------------------------------------------------------------------
    # Parquet / metadata sync to local temp dir (enables existing loaders)
    # ------------------------------------------------------------------

    async def sync_dataset_to_local(self, dataset_id: str, local_dir: Path) -> bool:
        """
        Download non-video dataset files to a local directory.

        Downloads meta files and data parquet files so that LeRobotLoader
        and HDF5Loader can operate on local paths. Videos are excluded
        to avoid downloading large media files.

        Args:
            dataset_id: Dataset identifier.
            local_dir: Local directory to sync into. Created if absent.

        Returns:
            True if sync completed successfully, False on critical failure.
        """
        local_dir.mkdir(parents=True, exist_ok=True)

        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            prefix = f"{self.get_blob_prefix(dataset_id)}/"
            synced_count = 0

            async for blob in container.list_blobs(name_starts_with=prefix):
                # Skip video files — they are streamed on demand
                if "/videos/" in blob.name:
                    continue

                relative = blob.name[len(prefix) :]
                local_path = local_dir / relative
                local_path.parent.mkdir(parents=True, exist_ok=True)

                if local_path.exists():
                    continue  # Already synced

                data = await self._read_blob_bytes(blob.name)
                if data is not None:
                    local_path.write_bytes(data)
                    synced_count += 1

            logger.info(
                "Synced %d blobs for dataset '%s' to '%s'",
                synced_count,
                dataset_id,
                local_dir,
            )
            return True

        except Exception as e:
            logger.warning("Failed to sync dataset '%s' to local: %s", dataset_id, e)
            return False

    async def sync_meta_only_to_local(self, dataset_id: str, local_dir: Path) -> bool:
        """
        Download only meta/ files for a dataset to a local directory.

        Fetches info.json, stats.json, tasks.parquet, and all episode metadata
        parquet files from the meta/ prefix without downloading data/ or videos/.
        Used for episode listing without triggering a full data sync.

        Args:
            dataset_id: Dataset identifier.
            local_dir: Local directory to sync into. Created if absent.

        Returns:
            True if meta/info.json was successfully downloaded, False otherwise.
        """
        local_dir.mkdir(parents=True, exist_ok=True)

        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            prefix = self.get_blob_prefix(dataset_id)
            meta_prefix = f"{prefix}/meta/"

            async for blob in container.list_blobs(name_starts_with=meta_prefix):
                relative = blob.name[len(f"{prefix}/") :]
                if relative not in _SYNC_META_BLOBS and not relative.startswith("meta/episodes/"):
                    continue

                local_path = local_dir / relative
                local_path.parent.mkdir(parents=True, exist_ok=True)

                if local_path.exists():
                    continue

                data = await self._read_blob_bytes(blob.name)
                if data is not None:
                    local_path.write_bytes(data)

            info_path = local_dir / "meta" / "info.json"
            if not info_path.exists():
                logger.warning("meta/info.json not found for dataset '%s'", dataset_id)
                return False

            return True

        except Exception as e:
            logger.warning("Failed to sync meta for dataset '%s': %s", dataset_id, e)
            return False

    # ------------------------------------------------------------------
    # HDF5 dataset sync and metadata
    # ------------------------------------------------------------------

    async def sync_hdf5_dataset_to_local(self, dataset_id: str, local_dir: Path) -> bool:
        """Download HDF5 episode files and config to a local directory."""
        local_dir.mkdir(parents=True, exist_ok=True)
        prefix = self.get_blob_prefix(dataset_id)
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            synced = 0
            async for blob in container.list_blobs(name_starts_with=prefix + "/"):
                if not (blob.name.endswith(".hdf5") or blob.name.endswith(".json")):
                    continue
                filename = blob.name.rsplit("/", 1)[-1]
                local_path = local_dir / filename
                if local_path.exists():
                    continue
                data = await self._read_blob_bytes(blob.name)
                if data is not None:
                    local_path.write_bytes(data)
                    synced += 1
            return synced > 0
        except Exception as e:
            logger.warning("Failed to sync HDF5 dataset '%s': %s", dataset_id, e)
            return False

    async def get_hdf5_dataset_config(self, dataset_id: str) -> dict | None:
        """Read dataset_config.json for a dataset."""
        data = await self._read_blob_bytes(f"{self.get_blob_prefix(dataset_id)}/dataset_config.json")
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    async def count_hdf5_episodes(self, dataset_id: str) -> int:
        """Count .hdf5 files for a dataset."""
        prefix = self.get_blob_prefix(dataset_id)
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            count = 0
            async for name in container.list_blob_names(name_starts_with=prefix + "/"):
                if name.endswith(".hdf5"):
                    count += 1
            return count
        except Exception as e:
            logger.warning("Failed to count HDF5 episodes for '%s': %s", dataset_id, e)
            return 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the internal BlobServiceClient."""
        if self._client is not None:
            await self._client.close()
            self._client = None
