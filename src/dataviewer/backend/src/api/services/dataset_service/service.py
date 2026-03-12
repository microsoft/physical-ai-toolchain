"""
Dataset service orchestrator.

Delegates format-specific operations to registered DatasetFormatHandler
implementations (LeRobot, HDF5) and manages blob storage integration.
"""

import asyncio
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...models.datasources import (
    DatasetInfo,
    EpisodeData,
    EpisodeMeta,
    FeatureSchema,
    TrajectoryPoint,
)
from ...storage import LocalStorageAdapter, StorageAdapter
from ..episode_cache import EpisodeCache
from .base import DatasetFormatHandler
from .hdf5_handler import HDF5FormatHandler
from .lerobot_handler import LEROBOT_AVAILABLE, LeRobotFormatHandler

if TYPE_CHECKING:
    from ...storage.blob_dataset import BlobDatasetProvider

logger = logging.getLogger(__name__)


def _validate_dataset_id(dataset_id: str) -> str:
    """Validate and return a safe dataset identifier. Raises ValueError on traversal attempts."""
    if "\\" in dataset_id or "/" in dataset_id:
        raise ValueError(f"Invalid dataset identifier: {dataset_id!r}")
    parts = dataset_id.split("--")
    if len(parts) > 5:
        raise ValueError(f"Dataset nesting too deep (max 5 levels): {dataset_id!r}")
    for part in parts:
        safe = os.path.basename(part)
        if not safe or safe != part or part in {".", ".."}:
            raise ValueError(f"Invalid dataset identifier: {dataset_id!r}")
    return dataset_id


class DatasetService:
    """
    Service for dataset and episode operations.

    Abstracts storage backend details and provides a consistent
    API for accessing dataset metadata and episode data.
    Supports loading trajectory data from HDF5 files and LeRobot parquet datasets.
    Works with local filesystem or Azure Blob Storage depending on configuration.
    """

    def __init__(
        self,
        base_path: str | None = None,
        storage_adapter: StorageAdapter | None = None,
        blob_provider: "BlobDatasetProvider | None" = None,
        episode_cache_capacity: int = 32,
        episode_cache_max_mb: int = 100,
    ):
        if base_path is None:
            base_path = os.environ.get("HMI_DATA_PATH", "./data")
        self.base_path = base_path
        self._datasets: dict[str, DatasetInfo] = {}
        if storage_adapter is not None:
            self._storage: StorageAdapter = storage_adapter
        else:
            self._storage = LocalStorageAdapter(base_path)
        self._local_dataset_ids: set[str] = set()
        self._blob_dataset_ids: set[str] = set()
        self._blob_provider: BlobDatasetProvider | None = blob_provider
        self._blob_synced: dict[str, Path] = {}
        self._blob_meta_synced: dict[str, Path] = {}

        # Format handlers (ordered by priority — LeRobot checked first)
        self._lerobot_handler = LeRobotFormatHandler()
        self._hdf5_handler = HDF5FormatHandler()
        self._handlers = [self._lerobot_handler, self._hdf5_handler]

        self._episode_cache = EpisodeCache(
            capacity=episode_cache_capacity,
            max_memory_bytes=episode_cache_max_mb * 1024 * 1024 if episode_cache_max_mb > 0 else 0,
        )
        self._prefetch_radius = 2
        self._prefetch_tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Handler resolution
    # ------------------------------------------------------------------

    def _resolve_handler(self, dataset_id: str) -> DatasetFormatHandler | None:
        """Find the handler that owns a dataset, initializing lazily if needed."""
        # Check if any handler already has a loader
        for handler in self._handlers:
            if handler.has_loader(dataset_id):
                return handler

        # Lazy init: try to create a loader via path detection
        try:
            dataset_path = self._get_dataset_path(dataset_id)
        except ValueError:
            return None

        for handler in self._handlers:
            if handler.get_loader(dataset_id, dataset_path):
                return handler
        return None

    def _detect_handler(self, dataset_path: Path) -> DatasetFormatHandler | None:
        """Detect the appropriate handler for a dataset path."""
        for handler in self._handlers:
            if handler.can_handle(dataset_path):
                return handler
        return None

    def _try_handlers(self, dataset_id: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Try the resolved handler, then fall through remaining handlers."""
        primary = self._resolve_handler(dataset_id)
        if primary is not None:
            result = getattr(primary, method)(dataset_id, *args, **kwargs)
            if result:
                return result

        # Fall through to other handlers in priority order
        for handler in self._handlers:
            if handler is not primary:
                result = getattr(handler, method)(dataset_id, *args, **kwargs)
                if result:
                    return result
        return None

    # ------------------------------------------------------------------
    # Blob dataset helpers
    # ------------------------------------------------------------------

    async def _ensure_blob_synced(self, dataset_id: str) -> Path | None:
        """Download blob dataset non-video files to a local temp dir."""
        if self._blob_provider is None:
            return None

        dataset_id = _validate_dataset_id(dataset_id)

        if dataset_id in self._blob_synced:
            return self._blob_synced[dataset_id]

        tmp_dir = Path(tempfile.mkdtemp(prefix="dvw_"))
        # codeql[py/path-injection]
        success = await self._blob_provider.sync_dataset_to_local(dataset_id, tmp_dir)
        if success:
            self._blob_synced[dataset_id] = tmp_dir
            return tmp_dir

        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.warning("Blob sync failed for dataset '%s', tmp dir removed", dataset_id)
        return None

    async def _ensure_blob_meta_synced(self, dataset_id: str) -> Path | None:
        """Download only meta/ files from blob to a local temp dir."""
        if self._blob_provider is None:
            return None

        dataset_id = _validate_dataset_id(dataset_id)

        if dataset_id in self._blob_meta_synced:
            return self._blob_meta_synced[dataset_id]

        tmp_dir = Path(tempfile.mkdtemp(prefix="dvwm_"))
        # codeql[py/path-injection]
        success = await self._blob_provider.sync_meta_only_to_local(dataset_id, tmp_dir)
        if success:
            self._blob_meta_synced[dataset_id] = tmp_dir
            return tmp_dir

        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.warning("Blob meta sync failed for dataset '%s'", dataset_id)
        return None

    async def _ensure_blob_hdf5_synced(self, dataset_id: str) -> Path | None:
        """Download HDF5 blob dataset files to a local temp dir."""
        if self._blob_provider is None:
            return None

        dataset_id = _validate_dataset_id(dataset_id)

        if dataset_id in self._blob_synced:
            return self._blob_synced[dataset_id]

        tmp_dir = Path(tempfile.mkdtemp(prefix="dvwh_"))
        success = await self._blob_provider.sync_hdf5_dataset_to_local(dataset_id, tmp_dir)
        if success:
            self._blob_synced[dataset_id] = tmp_dir
            return tmp_dir

        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    async def _discover_blob_hdf5_dataset(self, dataset_id: str) -> DatasetInfo | None:
        """Build DatasetInfo from an HDF5 blob dataset."""
        if self._blob_provider is None:
            return None

        episode_count = await self._blob_provider.count_hdf5_episodes(dataset_id)
        if episode_count == 0:
            return None

        parts = dataset_id.split("--")
        name = parts[-1]
        group = "--".join(parts[:-1]) if len(parts) > 1 else None

        dataset_info = DatasetInfo(
            id=dataset_id,
            name=name,
            group=group,
            total_episodes=episode_count,
            fps=30.0,
            features={},
            tasks=[],
        )
        self._datasets[dataset_id] = dataset_info
        self._blob_dataset_ids.add(dataset_id)
        return dataset_info

    async def _discover_blob_dataset(self, dataset_id: str) -> DatasetInfo | None:
        """Build DatasetInfo from a blob dataset's meta/info.json."""
        if self._blob_provider is None:
            return None

        info = await self._blob_provider.get_info_json(dataset_id)
        if info is None:
            return None

        features: dict[str, FeatureSchema] = {}
        for name, feat in (info.get("features") or {}).items():
            features[name] = FeatureSchema(
                dtype=feat.get("dtype", "unknown"),
                shape=feat.get("shape", []),
            )

        dataset_info = DatasetInfo(
            id=dataset_id,
            name=f"{dataset_id} ({info.get('robot_type', 'unknown')})" if info.get("robot_type") else dataset_id,
            total_episodes=info.get("total_episodes", 0),
            fps=float(info.get("fps", 30.0)),
            features=features,
            tasks=[],
        )
        self._datasets[dataset_id] = dataset_info
        self._blob_dataset_ids.add(dataset_id)
        return dataset_info

    async def get_blob_video_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        """Resolve the blob path for an episode video."""
        dataset_id = dataset_id.replace("\n", "").replace("\r", "")
        episode_idx = int(episode_idx)
        if self._blob_provider is None:
            return None
        return await self._blob_provider.resolve_video_blob_path(dataset_id, episode_idx, camera)

    async def get_blob_video_stream(
        self,
        blob_path: str,
        offset: int | None = None,
        length: int | None = None,
    ) -> tuple[dict[str, str], str, "AsyncIterator"] | None:
        """Stream video from blob storage with optional byte-range support.

        Returns (headers, media_type, async_iterator) or None.
        """
        if self._blob_provider is None:
            return None

        props = await self._blob_provider.get_blob_properties(blob_path)
        headers: dict[str, str] = {"Accept-Ranges": "bytes"}
        media_type = "video/mp4"
        if props:
            total_size = props["size"]
            mime = props.get("content_type", "")
            if mime and mime.startswith("video/"):
                media_type = mime

            if offset is not None:
                actual_length = length if length is not None else (total_size - offset)
                end_byte = offset + actual_length - 1
                headers["Content-Length"] = str(actual_length)
                headers["Content-Range"] = f"bytes {offset}-{end_byte}/{total_size}"
            else:
                headers["Content-Length"] = str(total_size)

        async def _stream():
            async for chunk in self._blob_provider.stream_video(blob_path, offset=offset, length=length):
                yield chunk

        return headers, media_type, _stream()

    def has_blob_provider(self) -> bool:
        """Return True when Azure Blob Storage dataset provider is configured."""
        return self._blob_provider is not None

    # ------------------------------------------------------------------
    # Dataset discovery
    # ------------------------------------------------------------------

    def _discover_dataset(self, dataset_id: str) -> DatasetInfo | None:
        """Discover and create DatasetInfo from filesystem."""
        try:
            dataset_path = self._get_dataset_path(dataset_id)
        except ValueError:
            return None

        if not dataset_path.exists() or not dataset_path.is_dir():
            return None

        handler = self._detect_handler(dataset_path)
        if handler is None:
            return None

        dataset_info = handler.discover(dataset_id, dataset_path)
        if dataset_info is not None:
            if "--" in dataset_id:
                dataset_info.group = "--".join(dataset_id.split("--")[:-1])
            self._datasets[dataset_id] = dataset_info
            self._local_dataset_ids.add(dataset_id)
        return dataset_info

    def _evict_dataset(self, dataset_id: str) -> None:
        """Remove cached dataset metadata, handler state, and temp dirs for a dataset."""
        self._datasets.pop(dataset_id, None)
        self._local_dataset_ids.discard(dataset_id)
        self._blob_dataset_ids.discard(dataset_id)
        synced_dir = self._blob_synced.pop(dataset_id, None)
        if synced_dir is not None:
            shutil.rmtree(synced_dir, ignore_errors=True)
        meta_dir = self._blob_meta_synced.pop(dataset_id, None)
        if meta_dir is not None:
            shutil.rmtree(meta_dir, ignore_errors=True)
        self._episode_cache.invalidate(dataset_id)
        for handler in self._handlers:
            loaders = getattr(handler, "_loaders", None)
            if isinstance(loaders, dict):
                loaders.pop(dataset_id, None)

    def cleanup_temp_dirs(self) -> None:
        """Remove all blob sync temp directories. Call on shutdown."""
        for path in self._blob_synced.values():
            shutil.rmtree(path, ignore_errors=True)
        self._blob_synced.clear()
        for path in self._blob_meta_synced.values():
            shutil.rmtree(path, ignore_errors=True)
        self._blob_meta_synced.clear()

    def _prune_missing_local_datasets(self, discovered_ids: set[str]) -> None:
        """Evict cached local datasets that no longer exist on disk."""
        stale_ids = self._local_dataset_ids - discovered_ids
        for dataset_id in stale_ids:
            self._evict_dataset(dataset_id)

    def _scan_directory(self, directory: Path, prefix_parts: list[str], discovered: set[str]) -> None:
        """Recursively scan for datasets, building --separated IDs. Max 5 levels."""
        if len(prefix_parts) >= 5:
            return
        for item in directory.iterdir():
            if not item.is_dir():
                continue
            current_parts = [*prefix_parts, item.name]
            handled = False
            for handler in self._handlers:
                if handler.can_handle(item):
                    discovered.add("--".join(current_parts))
                    handled = True
                    break
            if not handled:
                self._scan_directory(item, current_parts, discovered)

    async def list_datasets(self) -> list[DatasetInfo]:
        """List all available datasets."""
        # Single-pass blob container scan for both LeRobot and HDF5 datasets
        if self._blob_provider is not None:
            try:
                scan = await self._blob_provider.scan_all_dataset_ids()
                for dataset_id in scan.get("lerobot", []):
                    if dataset_id not in self._datasets:
                        await self._discover_blob_dataset(dataset_id)
                for dataset_id in scan.get("hdf5", []):
                    if dataset_id not in self._datasets:
                        await self._discover_blob_hdf5_dataset(dataset_id)
            except Exception as e:
                logger.warning("Failed to scan blob datasets: %s", e)

        base = Path(self.base_path)
        if not base.exists():
            return list(self._datasets.values())

        discovered_ids: set[str] = set()
        try:
            self._scan_directory(base, [], discovered_ids)
        except OSError:
            return list(self._datasets.values())

        self._prune_missing_local_datasets(discovered_ids)

        for dataset_id in discovered_ids:
            if dataset_id not in self._datasets:
                self._discover_dataset(dataset_id)

        return list(self._datasets.values())

    async def get_dataset(self, dataset_id: str) -> DatasetInfo | None:
        """Get metadata for a specific dataset."""
        if dataset_id in self._local_dataset_ids:
            try:
                self._get_dataset_path(dataset_id)
            except ValueError:
                self._evict_dataset(dataset_id)
                return None

        dataset = self._datasets.get(dataset_id)
        if dataset is not None:
            return dataset

        # Try blob discovery first
        if self._blob_provider is not None:
            blob_result = await self._discover_blob_dataset(dataset_id)
            if blob_result is not None:
                return blob_result

        return self._discover_dataset(dataset_id)

    async def register_dataset(self, dataset: DatasetInfo) -> None:
        """Register a dataset for access."""
        self._datasets[dataset.id] = dataset

    # ------------------------------------------------------------------
    # Episode operations
    # ------------------------------------------------------------------

    async def list_episodes(
        self,
        dataset_id: str,
        offset: int = 0,
        limit: int = 100,
        has_annotations: bool | None = None,
        task_index: int | None = None,
    ) -> list[EpisodeMeta]:
        """List episodes for a dataset with filtering."""
        dataset_id = dataset_id.replace("\n", "").replace("\r", "")
        dataset = self._datasets.get(dataset_id)
        annotated_indices = set(await self._storage.list_annotated_episodes(dataset_id))

        episode_indices: list[int] = []
        episode_info_map: dict[int, dict] = {}

        # Blob datasets: sync only meta/ files, build episode list from meta/episodes
        if self._blob_provider is not None:
            meta_path = await self._ensure_blob_meta_synced(dataset_id)
            if meta_path is not None and LEROBOT_AVAILABLE:
                info_path = meta_path / "meta" / "info.json"
                if info_path.exists():
                    episode_indices, episode_info_map = self._lerobot_handler.list_episodes_from_path(meta_path)

        # Local datasets: delegate to handler
        if not episode_indices:
            handler = self._resolve_handler(dataset_id)
            if handler is not None:
                episode_indices, episode_info_map = handler.list_episodes(dataset_id)

        # Fallback: generate indices from dataset metadata
        if not episode_indices and dataset is not None:
            episode_indices = list(range(dataset.total_episodes))

        if not episode_indices:
            return []

        episodes = []
        for idx in episode_indices:
            has_annot = idx in annotated_indices

            if has_annotations is not None and has_annot != has_annotations:
                continue

            ep_length = 0
            ep_task_index = 0

            if idx in episode_info_map:
                ep_length = episode_info_map[idx].get("length", 0)
                ep_task_index = episode_info_map[idx].get("task_index", 0)

            if task_index is not None and ep_task_index != task_index:
                continue

            episodes.append(
                EpisodeMeta(
                    index=idx,
                    length=ep_length,
                    task_index=ep_task_index,
                    has_annotations=has_annot,
                )
            )

        return episodes[offset : offset + limit]

    async def get_episode(self, dataset_id: str, episode_idx: int) -> EpisodeData | None:
        """Get complete data for a specific episode."""
        dataset_id = dataset_id.replace("\n", "").replace("\r", "")
        episode_idx = int(episode_idx)

        # Check cache first
        cached = self._episode_cache.get(dataset_id, episode_idx)
        if cached is not None:
            annotated_indices = set(await self._storage.list_annotated_episodes(dataset_id))
            cached.meta.has_annotations = episode_idx in annotated_indices
            return cached

        dataset = self._datasets.get(dataset_id)
        annotated_indices = set(await self._storage.list_annotated_episodes(dataset_id))

        # Try handler (local first)
        handler = self._resolve_handler(dataset_id)

        # If no local handler, try blob-synced LeRobot
        if handler is None and self._blob_provider is not None and LEROBOT_AVAILABLE:
            synced_path = await self._ensure_blob_synced(dataset_id)
            if synced_path is not None and self._lerobot_handler.get_loader(dataset_id, synced_path):
                handler = self._lerobot_handler

        # Try blob-synced HDF5
        if handler is None and self._blob_provider is not None:
            synced_path = await self._ensure_blob_hdf5_synced(dataset_id)
            if synced_path is not None and self._hdf5_handler.get_loader(dataset_id, synced_path):
                handler = self._hdf5_handler

        # Try all handlers in priority order
        handlers_to_try = [handler] if handler else []
        handlers_to_try.extend(h for h in self._handlers if h is not handler)
        for h in handlers_to_try:
            episode = h.load_episode(dataset_id, episode_idx, dataset_info=dataset)
            if episode is not None:
                episode.meta.has_annotations = episode_idx in annotated_indices
                self._episode_cache.put(dataset_id, episode_idx, episode)
                self._schedule_prefetch(dataset_id, episode_idx)
                return episode

        # Validate episode index if we have dataset info
        if dataset is not None and (episode_idx < 0 or episode_idx >= dataset.total_episodes):
            return None

        return EpisodeData(
            meta=EpisodeMeta(
                index=episode_idx,
                length=0,
                task_index=0,
                has_annotations=episode_idx in annotated_indices,
            ),
            video_urls={},
            trajectory_data=[],
        )

    async def get_episode_trajectory(self, dataset_id: str, episode_idx: int) -> list[TrajectoryPoint]:
        """Get only the trajectory data for an episode."""
        dataset_id = dataset_id.replace("\n", "").replace("\r", "")
        episode_idx = int(episode_idx)

        cached = self._episode_cache.get(dataset_id, episode_idx)
        if cached is not None:
            return cached.trajectory_data

        return self._try_handlers(dataset_id, "get_trajectory", episode_idx) or []

    # ------------------------------------------------------------------
    # Background prefetch
    # ------------------------------------------------------------------

    def _schedule_prefetch(self, dataset_id: str, episode_idx: int) -> None:
        """Schedule background loading of adjacent episodes into the cache."""
        dataset_id = dataset_id.replace("\n", "").replace("\r", "")
        episode_idx = int(episode_idx)
        if not self._episode_cache.enabled:
            return

        dataset = self._datasets.get(dataset_id)
        total = dataset.total_episodes if dataset else 0
        if total <= 1:
            return

        indices = [
            idx
            for idx in range(
                max(0, episode_idx - self._prefetch_radius),
                min(total, episode_idx + self._prefetch_radius + 1),
            )
            if idx != episode_idx and self._episode_cache.get(dataset_id, idx) is None
        ]

        if not indices:
            return

        async def _prefetch() -> None:
            for idx in indices:
                if self._episode_cache.get(dataset_id, idx) is not None:
                    continue
                handler = self._resolve_handler(dataset_id)

                # For blob datasets, ensure data files are synced locally first
                if handler is None and dataset_id in self._blob_dataset_ids and LEROBOT_AVAILABLE:
                    synced_path = await self._ensure_blob_synced(dataset_id)
                    if synced_path is not None and self._lerobot_handler.get_loader(dataset_id, synced_path):
                        handler = self._lerobot_handler

                if handler is None and dataset_id in self._blob_dataset_ids:
                    synced_path = await self._ensure_blob_hdf5_synced(dataset_id)
                    if synced_path is not None and self._hdf5_handler.get_loader(dataset_id, synced_path):
                        handler = self._hdf5_handler

                if handler is None:
                    break
                episode = handler.load_episode(dataset_id, idx, dataset_info=dataset)
                if episode is not None:
                    self._episode_cache.put(dataset_id, idx, episode)
                    logger.debug("Prefetched episode %s/%d", dataset_id, idx)

        # Clean up completed tasks
        self._prefetch_tasks = {t for t in self._prefetch_tasks if not t.done()}

        try:
            task = asyncio.create_task(_prefetch())
            self._prefetch_tasks.add(task)
            task.add_done_callback(self._prefetch_tasks.discard)
        except RuntimeError as error:
            logger.debug("Skipping episode prefetch for episode %d: %s", episode_idx, error)

    def schedule_video_prefetch(self, dataset_id: str, episode_idx: int, camera: str) -> None:
        """Pre-generate the next episode's video in the background."""
        dataset = self._datasets.get(dataset_id)
        total = dataset.total_episodes if dataset else 0
        next_idx = episode_idx + 1
        if next_idx >= total:
            return

        if not self._hdf5_handler.has_loader(dataset_id):
            return

        from .hdf5_handler import VideoGenerationQueue

        cache_path = self._hdf5_handler._video_cache_path(dataset_id, next_idx, camera)
        if cache_path is None or cache_path.exists():
            return

        def generate() -> bool:
            return self._hdf5_handler._generate_episode_video(dataset_id, next_idx, camera, cache_path)

        self._hdf5_handler._generation_queue.submit(
            dataset_id,
            next_idx,
            camera,
            VideoGenerationQueue.PRIORITY_PREFETCH,
            cache_path,
            generate,
        )

    def schedule_bulk_video_generation(self, dataset_id: str) -> int:
        """Enqueue video generation for all uncached HDF5 episodes."""
        if not self._hdf5_handler.has_loader(dataset_id):
            return 0
        return self._hdf5_handler.schedule_bulk_video_generation(dataset_id)

    def cancel_video_generation(self, dataset_id: str) -> None:
        """Synchronously cancel and wait for any in-progress video generation."""
        self._hdf5_handler._generation_queue.cancel_dataset(dataset_id)

    # ------------------------------------------------------------------
    # Capability queries
    # ------------------------------------------------------------------

    def invalidate_episode_cache(self, dataset_id: str, episode_index: int | None = None) -> int:
        """Remove cached episode data after an external mutation (e.g. annotation save)."""
        return self._episode_cache.invalidate(dataset_id, episode_index)

    def has_hdf5_support(self) -> bool:
        """Check if HDF5 support is available."""
        return self._hdf5_handler.available

    def has_lerobot_support(self) -> bool:
        """Check if LeRobot parquet support is available."""
        return self._lerobot_handler.available

    def dataset_has_hdf5(self, dataset_id: str) -> bool:
        """Check if a dataset has HDF5 files."""
        return self._hdf5_handler.has_loader(dataset_id)

    def dataset_is_lerobot(self, dataset_id: str) -> bool:
        """Check if a dataset is in LeRobot parquet format."""
        return self._lerobot_handler.has_loader(dataset_id)

    # ------------------------------------------------------------------
    # Path and media helpers
    # ------------------------------------------------------------------

    def _get_dataset_path(self, dataset_id: str) -> Path:
        """
        Build and validate the filesystem path for a dataset.

        Supports both flat IDs (``my_dataset``) and nested IDs using
        ``--`` separator (``parent--child``) for datasets in
        subdirectories. Each path component is validated via
        ``os.path.basename`` and resolved through directory enumeration.

        Raises:
            ValueError: If any component contains path traversal or
                        the directory does not exist.
        """
        parts = dataset_id.split("--") if "--" in dataset_id else [dataset_id]
        if len(parts) > 5:
            raise ValueError(f"Dataset nesting too deep (max 5 levels): {dataset_id}")

        for part in parts:
            safe = os.path.basename(part)
            if not safe or safe != part:
                raise ValueError(f"Invalid dataset path: {dataset_id}")

        base = Path(os.path.realpath(self.base_path))
        if not base.is_dir():
            raise ValueError(f"Base path not found: {self.base_path}")
        current = base
        for part in parts:
            found = False
            for entry in current.iterdir():
                if entry.name == part and entry.is_dir():
                    current = entry
                    found = True
                    break
            if not found:
                raise ValueError(f"Dataset directory not found: {dataset_id}")
        return current

    async def get_frame_image(self, dataset_id: str, episode_idx: int, frame_idx: int, camera: str) -> bytes | None:
        """Get a single frame image from an episode."""
        dataset_id = dataset_id.replace("\n", "").replace("\r", "")
        episode_idx = int(episode_idx)
        frame_idx = int(frame_idx)
        camera = camera.replace("\n", "").replace("\r", "")

        result = self._try_handlers(dataset_id, "get_frame_image", episode_idx, frame_idx, camera)
        if result is None:
            logger.warning("No loader found for dataset %s", dataset_id)
        return result

    async def get_episode_cameras(self, dataset_id: str, episode_idx: int) -> list[str]:
        """Get list of available cameras for an episode."""
        dataset_id = dataset_id.replace("\n", "").replace("\r", "")
        episode_idx = int(episode_idx)
        return self._try_handlers(dataset_id, "get_cameras", episode_idx) or []

    def get_video_file_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        """Get the filesystem path to a video file."""
        dataset_id = dataset_id.replace("\n", "").replace("\r", "")
        episode_idx = int(episode_idx)
        handler = self._resolve_handler(dataset_id)
        if handler is not None:
            return handler.get_video_path(dataset_id, episode_idx, camera)
        return None


# Global service instance
_dataset_service: DatasetService | None = None


def get_dataset_service() -> DatasetService:
    """
    Get the global dataset service instance.

    On first call, reads application config and creates the appropriate
    storage adapter and optional BlobDatasetProvider based on HMI_STORAGE_BACKEND.

    Returns:
        DatasetService singleton.
    """
    global _dataset_service
    if _dataset_service is None:
        from ...config import create_annotation_storage, create_blob_dataset_provider, get_app_config

        config = get_app_config()
        storage = create_annotation_storage(config)
        blob_provider = create_blob_dataset_provider(config)
        _dataset_service = DatasetService(
            base_path=config.data_path,
            storage_adapter=storage,
            blob_provider=blob_provider,
            episode_cache_capacity=config.episode_cache_capacity,
            episode_cache_max_mb=config.episode_cache_max_mb,
        )
    return _dataset_service
