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
    "meta/tasks.jsonl",
    "meta/episodes.jsonl",
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
        # Per-dataset cache of episode_index -> {camera -> (chunk, file, from_ts, to_ts)}
        self._episode_video_cache: dict[str, dict[int, dict[str, tuple[int, int, float, float]]]] = {}

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
        """Discover LeRobot and HDF5 datasets via prefix-only enumeration.

        Walks virtual directories with a '/' delimiter (cost proportional to
        the number of folders, not blobs) and probes each prefix for a
        ``meta/info.json`` marker. Prefixes without an info marker but with
        ``.hdf5`` blobs at that level are classified as HDF5 datasets.
        Recursion is bounded to 5 segments to match the dataset-id schema.

        Returns:
            Dict with 'lerobot' and 'hdf5' keys, each a sorted list of dataset IDs.
        """
        lerobot_ids: set[str] = set()
        hdf5_ids: set[str] = set()
        max_depth = 5

        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
        except Exception as e:
            logger.warning("Failed to open blob container '%s': %s", self.container_name, e)
            return {"lerobot": [], "hdf5": []}

        async def _has_info_json(prefix: str) -> bool:
            try:
                await container.get_blob_client(f"{prefix}meta/info.json").get_blob_properties()
                return True
            except ResourceNotFoundError:
                return False
            except Exception as e:
                logger.warning("info.json probe failed for prefix '%s': %s", prefix, e)
                return False

        async def _walk(prefix: str, depth: int) -> None:
            segments = prefix.rstrip("/").split("/") if prefix else []
            if 1 <= len(segments) <= max_depth and await _has_info_json(prefix):
                lerobot_ids.add("--".join(segments))
                return

            if depth >= max_depth:
                return

            child_prefixes: list[str] = []
            found_hdf5 = False
            try:
                async for item in container.walk_blobs(name_starts_with=prefix, delimiter="/"):
                    name = getattr(item, "name", None)
                    if not name:
                        continue
                    if name.endswith("/"):
                        child_prefixes.append(name)
                    elif name.endswith(".hdf5"):
                        found_hdf5 = True
            except Exception as e:
                logger.warning("Failed to walk blob prefix '%s': %s", prefix, e)
                return

            if found_hdf5 and 1 <= len(segments) <= max_depth:
                hdf5_ids.add("--".join(segments))
                return

            for child in child_prefixes:
                await _walk(child, depth + 1)

        try:
            await _walk("", 0)
        except Exception as e:
            logger.warning("Failed to scan blob container '%s': %s", self.container_name, e)

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
            logger.warning(
                "Invalid JSON in info.json for dataset '%s': %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                e,
            )
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

        Looks up the per-episode (chunk_index, file_index) for the requested
        camera in ``meta/episodes/chunk-*/file-*.parquet`` (LeRobot v3 stores
        many episodes per concatenated mp4). Falls back to template-based
        candidates and a directory scan when meta lookup is unavailable.

        Args:
            dataset_id: Dataset identifier.
            episode_idx: Episode index.
            camera: Camera key (e.g. 'observation.images.color').

        Returns:
            Blob path string if found, None otherwise.
        """
        info = await self.get_info_json(dataset_id)
        prefix = self.get_blob_prefix(dataset_id)

        # Primary path: meta-driven lookup of (chunk_index, file_index)
        meta_entry = await self._get_episode_video_entry(dataset_id, episode_idx, camera)
        candidates: list[str] = []
        if meta_entry is not None and info is not None:
            chunk_index, file_index, _, _ = meta_entry
            template = info.get("video_path") or "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
            try:
                templated = template.format(
                    video_key=camera,
                    chunk_index=chunk_index,
                    file_index=file_index,
                )
                candidates.append(f"{prefix}/{templated}")
            except (KeyError, IndexError):
                pass

        candidates.extend(self._build_video_path_candidates(info, prefix, camera, episode_idx))

        for blob_path in candidates:
            props = await self.get_blob_properties(blob_path)
            if props is not None:
                return blob_path

        # Fallback: scan for the episode-specific file in chunk directories
        file_suffix = f"/file-{episode_idx:03d}.mp4"
        episode_suffix = f"/episode_{episode_idx:06d}.mp4"
        video_prefix = f"{prefix}/videos/{camera}/"
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            async for blob in container.list_blobs(name_starts_with=video_prefix):
                if blob.name.endswith(file_suffix) or blob.name.endswith(episode_suffix):
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

    async def get_episode_video_window(
        self,
        dataset_id: str,
        episode_idx: int,
        camera: str,
    ) -> tuple[float, float] | None:
        """Return (from_timestamp, to_timestamp) for an episode within its concatenated video."""
        entry = await self._get_episode_video_entry(dataset_id, episode_idx, camera)
        if entry is None:
            return None
        _, _, from_ts, to_ts = entry
        if to_ts <= from_ts:
            return None
        return from_ts, to_ts

    async def _get_episode_video_entry(
        self,
        dataset_id: str,
        episode_idx: int,
        camera: str,
    ) -> tuple[int, int, float, float] | None:
        """Load per-episode video metadata (cached) and return entry for the camera."""
        cache = self._episode_video_cache.get(dataset_id)
        if cache is None:
            cache = await self._load_episode_video_metadata(dataset_id)
            if cache is None:
                return None
            self._episode_video_cache[dataset_id] = cache
        return cache.get(episode_idx, {}).get(camera)

    async def _load_episode_video_metadata(
        self,
        dataset_id: str,
    ) -> dict[int, dict[str, tuple[int, int, float, float]]] | None:
        """Download and parse meta/episodes/chunk-*/file-*.parquet for video lookup."""
        try:
            import io

            import pyarrow.parquet as pq
        except ImportError:
            return None

        prefix = self.get_blob_prefix(dataset_id)
        meta_prefix = f"{prefix}/meta/episodes/"
        result: dict[int, dict[str, tuple[int, int, float, float]]] = {}

        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            async for blob in container.list_blobs(name_starts_with=meta_prefix):
                if not blob.name.endswith(".parquet"):
                    continue
                data = await self._read_blob_bytes(blob.name)
                if data is None:
                    continue
                table = pq.read_table(io.BytesIO(data))
                cols = table.column_names
                if "episode_index" not in cols:
                    continue
                cameras = sorted(
                    {c.split("/")[1] for c in cols if c.startswith("videos/") and c.endswith("/chunk_index")}
                )
                episodes = table.column("episode_index").to_pylist()
                for camera in cameras:
                    chunk_col = f"videos/{camera}/chunk_index"
                    file_col = f"videos/{camera}/file_index"
                    from_col = f"videos/{camera}/from_timestamp"
                    to_col = f"videos/{camera}/to_timestamp"
                    if not all(c in cols for c in (chunk_col, file_col, from_col, to_col)):
                        continue
                    chunks = table.column(chunk_col).to_pylist()
                    files = table.column(file_col).to_pylist()
                    froms = table.column(from_col).to_pylist()
                    tos = table.column(to_col).to_pylist()
                    for ep, ck, fl, ft, tt in zip(episodes, chunks, files, froms, tos):
                        result.setdefault(int(ep), {})[camera] = (
                            int(ck),
                            int(fl),
                            float(ft),
                            float(tt),
                        )
        except Exception as e:
            logger.warning(
                "Failed to load episode video metadata for %s: %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                e,
            )
            return None

        return result or None

    @staticmethod
    def _build_video_path_candidates(
        info: dict | None,
        prefix: str,
        camera: str,
        episode_idx: int,
    ) -> list[str]:
        """Build an ordered list of candidate blob paths for an episode video."""
        chunks_size = int((info or {}).get("chunks_size", 1000))
        candidates: list[str] = []

        # Use the video_path template from info.json when present
        video_path_template = (info or {}).get("video_path")
        if video_path_template:
            # One-episode-per-chunk layout (chunk_index == episode_index, file_index == 0)
            try:
                templated = video_path_template.format(
                    video_key=camera,
                    chunk_index=episode_idx,
                    file_index=0,
                )
                candidates.append(f"{prefix}/{templated}")
            except (KeyError, IndexError):
                pass  # Template may lack required placeholders; skip to next strategy

            # chunks_size-based layout
            chunk_index = episode_idx // chunks_size
            file_index = episode_idx % chunks_size
            try:
                cs_templated = video_path_template.format(
                    video_key=camera,
                    chunk_index=chunk_index,
                    file_index=file_index,
                )
                cs_path = f"{prefix}/{cs_templated}"
                if cs_path not in candidates:
                    candidates.append(cs_path)
            except (KeyError, IndexError):
                pass  # Template may lack required placeholders; skip to fallback paths

        # Hardcoded fallback paths when no template is available
        if not candidates:
            candidates.append(f"{prefix}/videos/{camera}/chunk-{episode_idx:03d}/file-{episode_idx:03d}.mp4")
            chunk_index = episode_idx // chunks_size
            file_index = episode_idx % chunks_size
            candidates.append(f"{prefix}/videos/{camera}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4")
            # v2/flat layout: videos/{camera}/[chunk-XXX/]episode_{episode_index:06d}.mp4
            candidates.append(f"{prefix}/videos/{camera}/episode_{episode_idx:06d}.mp4")
            candidates.append(f"{prefix}/videos/chunk-{chunk_index:03d}/{camera}/episode_{episode_idx:06d}.mp4")

        return candidates

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

    async def upload_video(self, dataset_id: str, camera: str, episode_idx: int, local_path: Path) -> bool:
        """Upload a locally generated video to blob storage.

        Creates a dedicated client to avoid event loop conflicts when called
        from a worker thread via asyncio.new_event_loop().
        """
        prefix = self.get_blob_prefix(dataset_id)
        blob_path = f"{prefix}/meta/videos/{camera}/episode_{episode_idx:06d}.mp4"

        account_url = f"https://{self.account_name}.blob.core.windows.net"
        try:
            credential = AsyncDefaultAzureCredential() if not self.sas_token else None
            effective_credential = self.sas_token or credential
            client = BlobServiceClient(account_url=account_url, credential=effective_credential)

            async with client:
                container = client.get_container_client(self.container_name)
                blob_client = container.get_blob_client(blob_path)
                with open(local_path, "rb") as f:
                    await blob_client.upload_blob(f, overwrite=True)

            if credential:
                await credential.close()

            logger.info("Uploaded video to blob: %s", blob_path)
            return True
        except Exception as e:
            logger.warning("Failed to upload video to blob '%s': %s", blob_path, e)
            return False

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
                # Skip HDF5 files — they are downloaded on demand per episode
                if blob.name.endswith(".hdf5"):
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
                dataset_id.replace("\r", "").replace("\n", ""),
                local_dir,
            )
            return True

        except Exception as e:
            logger.warning(
                "Failed to sync dataset '%s' to local: %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                e,
            )
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
                logger.warning(
                    "meta/info.json not found for dataset '%s'",
                    dataset_id.replace("\r", "").replace("\n", ""),
                )
                return False

            return True

        except Exception as e:
            logger.warning(
                "Failed to sync meta for dataset '%s': %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                e,
            )
            return False

    # ------------------------------------------------------------------
    # HDF5 dataset sync and metadata
    # ------------------------------------------------------------------

    async def sync_hdf5_dataset_to_local(self, dataset_id: str, local_dir: Path) -> bool:
        """Download HDF5 config, video cache, and episode listing to a local directory.

        Downloads JSON config files, cached MP4 videos from meta/videos/,
        and creates empty placeholder files for each .hdf5 blob so
        HDF5Loader.list_episodes() can discover episode indices without
        downloading full episode data. Episode HDF5 files are fetched
        on-demand via sync_hdf5_episode_to_local.
        """
        local_dir.mkdir(parents=True, exist_ok=True)
        prefix = self.get_blob_prefix(dataset_id)
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            found_hdf5 = False
            async for blob in container.list_blobs(name_starts_with=prefix + "/"):
                if blob.name.endswith(".json"):
                    filename = blob.name.rsplit("/", 1)[-1]
                    local_path = local_dir / filename
                    if local_path.exists():
                        continue
                    data = await self._read_blob_bytes(blob.name)
                    if data is not None:
                        local_path.write_bytes(data)
                elif blob.name.endswith(".hdf5"):
                    found_hdf5 = True
                    filename = blob.name.rsplit("/", 1)[-1]
                    local_path = local_dir / filename
                    if not local_path.exists():
                        local_path.touch()
                elif blob.name.endswith(".mp4") and "/meta/videos/" in blob.name:
                    relative = blob.name[len(prefix + "/") :]
                    local_path = local_dir / relative
                    if local_path.exists():
                        continue
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    blob_client = container.get_blob_client(blob.name)
                    download = await blob_client.download_blob()
                    tmp_path = local_path.with_suffix(".mp4.tmp")
                    with open(tmp_path, "wb") as f:
                        async for chunk in download.chunks():
                            f.write(chunk)
                    tmp_path.rename(local_path)
                    logger.info("Downloaded cached video: %s", relative)
            return found_hdf5
        except Exception as e:
            logger.warning(
                "Failed to sync HDF5 dataset '%s': %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                e,
            )
            return False

    async def sync_hdf5_episode_to_local(self, dataset_id: str, local_dir: Path, episode_idx: int) -> bool:
        """Download a single HDF5 episode file to the local directory.

        Streams directly to disk to avoid loading the entire file into memory.
        """
        prefix = self.get_blob_prefix(dataset_id)
        patterns = [
            f"episode_{episode_idx:06d}.hdf5",
            f"episode_{episode_idx}.hdf5",
            f"ep_{episode_idx:06d}.hdf5",
            f"ep_{episode_idx}.hdf5",
        ]
        try:
            client = await self._get_client()
            container = client.get_container_client(self.container_name)
            async for blob in container.list_blobs(name_starts_with=prefix + "/"):
                if not blob.name.endswith(".hdf5"):
                    continue
                filename = blob.name.rsplit("/", 1)[-1]
                if filename not in patterns:
                    continue
                local_path = local_dir / filename
                if local_path.exists() and local_path.stat().st_size > 0:
                    return True
                blob_client = container.get_blob_client(blob.name)
                download = await blob_client.download_blob()
                tmp_path = local_path.with_suffix(".hdf5.tmp")
                written = 0
                with open(tmp_path, "wb") as f:
                    async for chunk in download.chunks():
                        f.write(chunk)
                        written += len(chunk)
                tmp_path.rename(local_path)
                logger.info(
                    "Downloaded HDF5 episode %d for '%s' (%d bytes)",
                    episode_idx,
                    dataset_id,
                    written,
                )
                return True
            return False
        except Exception as e:
            logger.warning(
                "Failed to sync HDF5 episode %d for '%s': %s",
                episode_idx,
                dataset_id,
                e,
            )
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
            logger.warning(
                "Failed to count HDF5 episodes for '%s': %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                e,
            )
            return 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release the internal BlobServiceClient."""
        if self._client is not None:
            await self._client.close()
            self._client = None
