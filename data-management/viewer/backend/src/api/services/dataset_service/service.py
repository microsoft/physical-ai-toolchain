"""
Dataset service orchestrator.

Delegates format-specific operations to registered DatasetFormatHandler
implementations (LeRobot, HDF5) and manages blob storage integration.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...models.datasources import (
    AcceptedDatasetContract,
    DatasetCatalogPage,
    DatasetInfo,
    DatasetSummary,
    EpisodeData,
    EpisodeMeta,
    FeatureSchema,
    TrajectoryPoint,
)
from ...storage import LocalStorageAdapter, StorageAdapter
from ...storage.paths import dataset_id_to_blob_prefix
from ..episode_cache import EpisodeCache
from .base import DatasetFormatHandler, normalize_feature_names
from .hdf5_handler import HDF5FormatHandler
from .lerobot_handler import LEROBOT_AVAILABLE, LeRobotFormatHandler

if TYPE_CHECKING:
    from evaluation.vlm_judge.dataset import EpisodeRecord

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
        blob_provider: BlobDatasetProvider | None = None,
        episode_cache_capacity: int = 32,
        episode_cache_max_mb: int = 100,
        video_cache_max_bytes: int = 1024 * 1024 * 1024,
    ):
        if base_path is None:
            base_path = os.environ.get("DATA_DIR", "./data")
        self.base_path = base_path
        self._datasets: dict[str, DatasetInfo] = {}
        self._dataset_formats: dict[str, str] = {}
        self._catalog_snapshots: dict[str, tuple[float, list[DatasetSummary]]] = {}
        self._catalog_current: str | None = None
        self._catalog_refresh_sequence = 0
        self._catalog_refresh_failed = False
        self._catalog_lock = asyncio.Lock()
        if storage_adapter is not None:
            self._storage: StorageAdapter = storage_adapter
        else:
            self._storage = LocalStorageAdapter(base_path)
        self._local_dataset_ids: set[str] = set()
        self._blob_dataset_ids: set[str] = set()
        self._blob_provider: BlobDatasetProvider | None = blob_provider
        self._blob_synced: dict[str, Path] = {}
        self._blob_hdf5_synced: dict[str, Path] = {}
        self._blob_meta_synced: dict[str, Path] = {}
        self._media_metadata_revisions: dict[str, tuple[str, str]] = {}
        self._edit_blob_synced: tuple[str, Path] | None = None
        self._edit_blob_sync_lock = asyncio.Lock()
        # Per-blob locks to serialize concurrent video materialization for the same blob.
        # Without this, parallel range requests race on the shared .part file and the
        # loser's tmp.replace(target) raises FileNotFoundError after the winner renames it.
        self._blob_video_locks: dict[str, asyncio.Lock] = {}
        self._blob_video_locks_guard = asyncio.Lock()
        self._blob_video_cache_dir: Path | None = None
        self._blob_video_cache_bytes = 0
        self._blob_video_max_bytes = video_cache_max_bytes

        # Format handlers (ordered by priority — LeRobot checked first)
        self._lerobot_handler = LeRobotFormatHandler()
        self._hdf5_handler = HDF5FormatHandler()
        self._handlers = [self._lerobot_handler, self._hdf5_handler]

        self._episode_cache = EpisodeCache(
            capacity=episode_cache_capacity,
            max_memory_bytes=episode_cache_max_mb * 1024 * 1024 if episode_cache_max_mb > 0 else 0,
        )
        self._edit_context_cache = EpisodeCache(
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

    async def _ensure_blob_synced(self, dataset_id: str, episode_idx: int) -> Path | None:
        """Prepare metadata and the requested episode shard without videos."""
        if self._blob_provider is None:
            return None

        dataset_id = _validate_dataset_id(dataset_id)

        existing = self._blob_synced.get(dataset_id)
        tmp_dir = existing or Path(tempfile.mkdtemp(prefix="dvw_"))
        # codeql[py/path-injection]
        success = await self._blob_provider.sync_episode_to_local(dataset_id, tmp_dir, episode_idx)
        if success:
            self._blob_synced[dataset_id] = tmp_dir
            return tmp_dir

        if existing is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.warning(
            "Selected Blob episode preparation failed for dataset '%s'",
            dataset_id.replace("\r", "").replace("\n", ""),
        )
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
        logger.warning(
            "Blob meta sync failed for dataset '%s'",
            dataset_id.replace("\r", "").replace("\n", ""),
        )
        return None

    async def _ensure_blob_hdf5_synced(self, dataset_id: str) -> Path | None:
        """Download HDF5 blob dataset placeholder files to a local temp dir."""
        if self._blob_provider is None:
            return None

        dataset_id = _validate_dataset_id(dataset_id)

        if dataset_id in self._blob_hdf5_synced:
            return self._blob_hdf5_synced[dataset_id]

        tmp_dir = Path(tempfile.mkdtemp(prefix="dvwh_"))
        success = await self._blob_provider.sync_hdf5_dataset_to_local(dataset_id, tmp_dir)
        if success:
            self._blob_hdf5_synced[dataset_id] = tmp_dir
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
        self._dataset_formats[dataset_id] = "hdf5"
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
                names=normalize_feature_names(feat.get("names")),
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
        if self._blob_provider is None:
            return None
        return await self._blob_provider.resolve_video_blob_path(dataset_id, episode_idx, camera)

    async def blob_video_is_browser_compatible(self, dataset_id: str, camera: str) -> bool:
        if self._blob_provider is None:
            return False
        info = await self._blob_provider.get_info_json(dataset_id)
        feature = (info or {}).get("features", {}).get(camera, {})
        video_info = feature.get("video_info") or feature.get("info") or {}
        codec = str(video_info.get("video.codec", video_info.get("codec", ""))).lower()
        return codec in {"h264", "vp8", "vp9", "av1", "hevc"}

    async def materialize_blob_video(self, blob_path: str) -> Path | None:
        """Materialize a versioned selected video within a fixed service scratch budget."""
        if self._blob_provider is None:
            return None
        provider = self._blob_provider
        properties = await provider.get_blob_properties(blob_path)
        if not properties or not isinstance(properties.get("etag"), str) or not properties["etag"]:
            logger.warning("Video materialization requires a source validator")
            return None
        size = properties.get("size", 0)
        if not isinstance(size, int) or size <= 0 or size > self._blob_video_max_bytes:
            logger.warning("Video exceeds the scratch byte budget")
            return None
        identity = json.dumps(["azure", provider.account_name, provider.container_name, blob_path, properties["etag"]])
        key = hashlib.sha256(identity.encode()).hexdigest()
        if self._blob_video_cache_dir is None:
            self._blob_video_cache_dir = Path(tempfile.mkdtemp(prefix="dvw_media_"))
        cache_dir = self._blob_video_cache_dir
        suffix = Path(blob_path).suffix or ".mp4"
        target = cache_dir / f"{key}{suffix}"
        if target.exists() and target.stat().st_size == size:
            return target
        async with self._blob_video_locks_guard:
            lock = self._blob_video_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if target.exists() and target.stat().st_size == size:
                return target
            async with self._blob_video_locks_guard:
                if self._blob_video_cache_bytes + size > self._blob_video_max_bytes:
                    logger.warning("Video scratch capacity exhausted; existing readers retain their files")
                    return None
                self._blob_video_cache_bytes += size
            temporary = None
            published = False
            try:
                descriptor, temporary_name = tempfile.mkstemp(prefix=key, suffix=".part", dir=cache_dir)
                temporary = Path(temporary_name)
                transferred = 0
                with os.fdopen(descriptor, "wb") as stream:
                    async for chunk in provider.stream_video(blob_path, etag=properties["etag"]):
                        transferred += len(chunk)
                        if transferred > size:
                            raise ValueError("Video transfer exceeded declared size")
                        stream.write(chunk)
                after = await provider.get_blob_properties(blob_path)
                if transferred != size or not after or after.get("etag") != properties["etag"]:
                    raise ValueError("Video source changed during materialization")
                temporary.replace(target)
                published = True
                logger.info("Materialized selected video bytes=%d reason=decoder-or-conversion", transferred)
            except Exception as exc:
                logger.warning("Video materialization failed: %s", type(exc).__name__)
                return None
            finally:
                if not published:
                    self._blob_video_cache_bytes -= size
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return target

    async def get_blob_video_stream(
        self,
        blob_path: str,
        offset: int | None = None,
        length: int | None = None,
    ) -> tuple[dict[str, str], str, AsyncIterator] | None:
        """Stream video from blob storage with optional byte-range support.

        Returns (headers, media_type, async_iterator) or None.
        """
        if self._blob_provider is None:
            return None

        props = await self._blob_provider.get_blob_properties(blob_path)
        if not props:
            return None
        headers: dict[str, str] = {"Accept-Ranges": "bytes"}
        media_type = "video/mp4"
        if props:
            total_size = props["size"]
            mime = props.get("content_type", "")
            if mime and mime.startswith("video/"):
                media_type = mime

            if offset is not None:
                if offset < 0 or offset >= total_size:
                    headers["Content-Length"] = "0"
                    headers["Content-Range"] = f"bytes */{total_size}"
                else:
                    length = min(length if length is not None else total_size - offset, total_size - offset)
                    end_byte = offset + length - 1
                    headers["Content-Length"] = str(length)
                    headers["Content-Range"] = f"bytes {offset}-{end_byte}/{total_size}"
            else:
                headers["Content-Length"] = str(total_size)
            if props.get("etag"):
                headers["ETag"] = props["etag"]

        blob_provider = self._blob_provider
        if blob_provider is None:
            return None

        async def _stream():
            conditions = {"etag": props["etag"]} if props.get("etag") else {}
            async for chunk in blob_provider.stream_video(blob_path, offset=offset, length=length, **conditions):
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
            self._dataset_formats[dataset_id] = "lerobot" if handler is self._lerobot_handler else "hdf5"
            if "--" in dataset_id:
                dataset_info.group = "--".join(dataset_id.split("--")[:-1])
            self._datasets[dataset_id] = dataset_info
            self._local_dataset_ids.add(dataset_id)
        return dataset_info

    def _evict_dataset(self, dataset_id: str) -> None:
        """Remove cached dataset metadata, handler state, and temp dirs for a dataset."""
        self._datasets.pop(dataset_id, None)
        self._dataset_formats.pop(dataset_id, None)
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
        if self._blob_video_cache_dir is not None:
            shutil.rmtree(self._blob_video_cache_dir, ignore_errors=True)
            self._blob_video_cache_dir = None
            self._blob_video_cache_bytes = 0
            self._blob_video_locks.clear()
        if self._edit_blob_synced is not None:
            shutil.rmtree(self._edit_blob_synced[1], ignore_errors=True)
            self._edit_blob_synced = None
        for path in self._blob_synced.values():
            shutil.rmtree(path, ignore_errors=True)
        self._blob_synced.clear()
        for path in self._blob_meta_synced.values():
            shutil.rmtree(path, ignore_errors=True)
        self._blob_meta_synced.clear()
        self._media_metadata_revisions.clear()
        for path in self._blob_hdf5_synced.values():
            shutil.rmtree(path, ignore_errors=True)
        self._blob_hdf5_synced.clear()

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

    async def list_datasets(self, *, strict: bool = False, refresh_cached: bool = False) -> list[DatasetInfo]:
        """List all available datasets."""
        # Single-pass blob container scan for both LeRobot and HDF5 datasets
        if self._blob_provider is not None:
            try:
                scan = (
                    await self._blob_provider.scan_all_dataset_ids(strict=True)
                    if strict
                    else await self._blob_provider.scan_all_dataset_ids()
                )
            except Exception as e:
                if strict:
                    raise
                logger.warning("Failed to scan blob datasets: %s", e)
                scan = {}
            if strict:
                discovered_blob_ids = set(scan.get("lerobot", [])) | set(scan.get("hdf5", []))
                for dataset_id in self._blob_dataset_ids - discovered_blob_ids:
                    self._evict_dataset(dataset_id)
            for dataset_id in scan.get("lerobot", []):
                self._dataset_formats[dataset_id] = "lerobot"
                if dataset_id in self._datasets and not refresh_cached:
                    continue
                try:
                    discovered = await self._discover_blob_dataset(dataset_id)
                    if strict and discovered is None:
                        raise ValueError("Catalog dataset metadata unavailable")
                except Exception as e:
                    if strict:
                        raise
                    logger.warning("Failed to discover blob dataset %s: %s", dataset_id, e)
            for dataset_id in scan.get("hdf5", []):
                self._dataset_formats[dataset_id] = "hdf5"
                if dataset_id in self._datasets and not refresh_cached:
                    continue
                try:
                    discovered = await self._discover_blob_hdf5_dataset(dataset_id)
                    if strict and discovered is None:
                        raise ValueError("Catalog dataset metadata unavailable")
                except Exception as e:
                    if strict:
                        raise
                    logger.warning("Failed to discover blob HDF5 dataset %s: %s", dataset_id, e)

        base = Path(self.base_path)
        if not base.exists():
            return list(self._datasets.values())

        discovered_ids: set[str] = set()
        try:
            self._scan_directory(base, [], discovered_ids)
        except OSError:
            if strict:
                raise
            return list(self._datasets.values())

        self._prune_missing_local_datasets(discovered_ids)

        for dataset_id in discovered_ids:
            if dataset_id not in self._datasets or refresh_cached:
                discovered = self._discover_dataset(dataset_id)
                if strict and discovered is None:
                    raise ValueError("Catalog dataset metadata unavailable")

        return list(self._datasets.values())

    async def query_catalog(
        self,
        *,
        query: str = "",
        group: str | None = None,
        sort: str = "name",
        offset: int = 0,
        limit: int = 25,
        snapshot_id: str | None = None,
        refresh: bool = False,
    ) -> DatasetCatalogPage:
        if offset < 0 or not 1 <= limit <= 100 or sort not in {"name", "episodes", "episodes-desc"}:
            raise ValueError("Invalid catalog page")
        if snapshot_id is not None and snapshot_id not in self._catalog_snapshots:
            raise ValueError("Catalog snapshot expired; refresh the catalog")
        if refresh or self._catalog_current is None:
            previous = self._catalog_refresh_sequence
            async with self._catalog_lock:
                if previous == self._catalog_refresh_sequence:
                    started = time.monotonic()
                    try:
                        datasets = await self.list_datasets(strict=True, refresh_cached=refresh)
                        summaries = [
                            DatasetSummary(
                                id=item.id,
                                name=item.name,
                                group=item.group,
                                total_episodes=item.total_episodes,
                                format=self._dataset_formats.get(item.id),
                            )
                            for item in sorted(datasets, key=lambda item: item.id)
                        ]
                        revision = hashlib.sha256(
                            json.dumps(
                                [item.model_dump() for item in summaries],
                                sort_keys=True,
                            ).encode()
                        ).hexdigest()
                        self._catalog_snapshots.pop(revision, None)
                        self._catalog_snapshots[revision] = (time.monotonic(), summaries)
                        self._catalog_current = revision
                        self._catalog_refresh_failed = False
                        while len(self._catalog_snapshots) > 4:
                            self._catalog_snapshots.pop(next(iter(self._catalog_snapshots)))
                        logger.info(
                            "Catalog refreshed datasets=%d duration_ms=%d",
                            len(summaries),
                            int((time.monotonic() - started) * 1000),
                        )
                    except Exception as error:
                        self._catalog_refresh_failed = True
                        logger.warning(
                            "Catalog refresh failed category=%s retained=%s",
                            type(error).__name__,
                            self._catalog_current is not None,
                        )
                        if self._catalog_current is None:
                            raise
                    finally:
                        self._catalog_refresh_sequence += 1
        selected = snapshot_id if snapshot_id is not None and not refresh else self._catalog_current
        if selected is None:
            raise RuntimeError("Catalog discovery unavailable")
        created, summaries = self._catalog_snapshots[selected]
        search = query.casefold().strip()
        items = [
            item
            for item in summaries
            if (group is None or item.group == group)
            and (not search or search in " ".join((item.id, item.name, item.group or "")).casefold())
        ]
        if sort == "name":
            items.sort(key=lambda item: (item.name.casefold(), item.id))
        else:
            items.sort(key=lambda item: (item.total_episodes * (-1 if sort == "episodes-desc" else 1), item.id))
        return DatasetCatalogPage(
            items=items[offset : offset + limit],
            total=len(items),
            catalog_total=len(summaries),
            groups=sorted({item.group for item in summaries if item.group is not None}),
            snapshot_id=selected,
            offset=offset,
            limit=limit,
            stale=self._catalog_refresh_failed or time.monotonic() - created > 60 or selected != self._catalog_current,
            refresh_failed=self._catalog_refresh_failed,
        )

    async def get_dataset(self, dataset_id: str) -> DatasetInfo | None:
        """Get metadata for a specific dataset."""
        if dataset_id in self._local_dataset_ids and dataset_id not in self._blob_dataset_ids:
            try:
                self._get_dataset_path(dataset_id)
            except ValueError:
                self._evict_dataset(dataset_id)
                return None

        dataset = self._datasets.get(dataset_id)
        if dataset is not None:
            return dataset

        # Try blob discovery (LeRobot, then HDF5)
        if self._blob_provider is not None:
            blob_result = await self._discover_blob_dataset(dataset_id)
            if blob_result is not None:
                return blob_result
            hdf5_result = await self._discover_blob_hdf5_dataset(dataset_id)
            if hdf5_result is not None:
                return hdf5_result

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
        require_actual: bool = False,
    ) -> list[EpisodeMeta]:
        """List episodes for a dataset with filtering."""
        dataset = self._datasets.get(dataset_id)
        annotated_indices = set(await self._storage.list_annotated_episodes(dataset_id))

        episode_indices: list[int] = []
        episode_info_map: dict[int, dict] = {}
        use_blob_metadata = self._blob_provider is not None and dataset_id not in self._local_dataset_ids

        # Blob datasets: sync only meta/ files, build episode list from meta/episodes
        if use_blob_metadata:
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

        # Blob HDF5 datasets: sync placeholders and list via HDF5 handler
        if not episode_indices and use_blob_metadata:
            synced_path = await self._ensure_blob_hdf5_synced(dataset_id)
            if synced_path is not None and self._hdf5_handler.get_loader(dataset_id, synced_path):
                episode_indices, episode_info_map = self._hdf5_handler.list_episodes(dataset_id)

        # Fallback: generate indices from dataset metadata
        if not episode_indices and dataset is not None and not require_actual:
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

    async def _get_blob_source_revision(self, dataset_id: str, episode_idx: int) -> tuple[str, str]:
        try:
            from azure.core.exceptions import ResourceNotFoundError

            provider = self._blob_provider
            if provider is None:
                raise ValueError("Blob provider unavailable")
            prefix = dataset_id_to_blob_prefix(dataset_id)
            client = await provider._get_client()
            container = client.get_container_client(provider.container_name)
            candidates = [f"{prefix}/meta/info.json"]
            candidates.extend(
                f"{prefix}/{folder}{name}.hdf5"
                for folder in ("", "data/", "episodes/")
                for name in (
                    f"episode_{episode_idx:06d}",
                    f"episode_{episode_idx}",
                    f"ep_{episode_idx:06d}",
                    f"ep_{episode_idx}",
                )
            )
            for blob_path in candidates:
                try:
                    properties = await container.get_blob_client(blob_path).get_blob_properties()
                except ResourceNotFoundError as error:
                    if getattr(error, "error_code", None) == "BlobNotFound":
                        continue
                    raise
                if not isinstance(properties.etag, str) or not properties.etag:
                    raise ValueError("Missing source validator")
                identity = json.dumps(["azure", provider.account_name, provider.container_name, prefix])
                metadata_revision = (
                    await provider.get_metadata_revision(dataset_id) if blob_path.endswith("/meta/info.json") else None
                )
                generation = json.dumps([blob_path, properties.etag, metadata_revision])
                logger.debug(
                    "Resolved Blob source generation dataset=%s episode=%d",
                    dataset_id.replace("\r", "").replace("\n", ""),
                    int(episode_idx),
                )
                return hashlib.sha256(identity.encode()).hexdigest(), hashlib.sha256(generation.encode()).hexdigest()
            raise ValueError("Missing source object")
        except Exception as error:
            logger.error("Blob source generation lookup failed: %s", type(error).__name__)
            raise ValueError("Source generation is unavailable") from None

    async def get_episode_media_record(self, dataset_id: str, episode_idx: int) -> EpisodeRecord | None:
        """Resolve versioned camera references without downloading any video."""
        from evaluation.vlm_judge.dataset import iter_episodes

        if await self.get_dataset(dataset_id) is None:
            return None
        source = await self.get_source_revision(dataset_id, episode_idx)
        remote = dataset_id in self._blob_dataset_ids and dataset_id not in self._local_dataset_ids
        if remote:
            async with self._edit_blob_sync_lock:
                if self._media_metadata_revisions.get(dataset_id) != source:
                    previous = self._blob_meta_synced.pop(dataset_id, None)
                    if previous is not None:
                        await asyncio.to_thread(shutil.rmtree, previous, ignore_errors=True)
                root = await self._ensure_blob_meta_synced(dataset_id)
                if root is None:
                    raise ValueError("Media metadata unavailable")
                self._media_metadata_revisions[dataset_id] = source
                record = await asyncio.to_thread(
                    lambda: next(iter_episodes(root, indices=[episode_idx], limit=1), None)
                )
        else:
            root = self._get_dataset_path(dataset_id)
            record = await asyncio.to_thread(lambda: next(iter_episodes(root, indices=[episode_idx], limit=1), None))
        if record is None:
            return None
        paths = {}
        identities = {}
        for camera, path in record.video_paths.items():
            path = path.resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("Invalid media path")
            if remote:
                provider = self._blob_provider
                if provider is None:
                    raise ValueError("Media storage unavailable")
                blob_path = f"{provider.get_blob_prefix(dataset_id)}/{path.relative_to(root.resolve()).as_posix()}"
                properties = await provider.get_blob_properties(blob_path)
                if not properties or not isinstance(properties.get("etag"), str):
                    raise ValueError("Versioned media unavailable")
                identity = ["azure", provider.account_name, provider.container_name, blob_path, properties["etag"]]
                paths[camera] = Path(blob_path)
            else:
                stat = path.stat()
                identity = [
                    "local",
                    str(path),
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                ]
                paths[camera] = path
            identities[camera] = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        return replace(record, video_paths=paths, media_identity=identities)

    async def materialize_episode_media(self, dataset_id: str, paths: dict[str, Path]) -> dict[str, Path]:
        """Provide decoder paths for selected views only."""
        if dataset_id not in self._blob_dataset_ids or dataset_id in self._local_dataset_ids:
            return paths
        materialized = {}
        for camera, path in paths.items():
            local = await self.materialize_blob_video(path.as_posix())
            if local is None:
                raise ValueError("Selected camera media unavailable")
            materialized[camera] = local
        return materialized

    async def get_source_revision(self, dataset_id: str, episode_idx: int) -> tuple[str, str]:
        """Identify the source and its current dataset generation without curation files."""
        _validate_dataset_id(dataset_id)
        if dataset_id in self._blob_dataset_ids and dataset_id not in self._local_dataset_ids:
            return await self._get_blob_source_revision(dataset_id, episode_idx)

        def local_revision() -> tuple[str, str]:
            root = self._get_dataset_path(dataset_id).resolve()
            root.relative_to(Path(self.base_path).resolve())
            manifest = root / "meta" / "info.json"
            if not manifest.is_file():
                if not self._hdf5_handler.get_loader(dataset_id, root):
                    raise ValueError("Source generation is unavailable")
                loader = self._hdf5_handler._get_loader(dataset_id)
                if loader is None:
                    raise ValueError("Source generation is unavailable")
                manifest = Path(loader._find_episode_file(episode_idx))
            metadata_paths = (
                sorted(
                    path
                    for path in (root / "meta").rglob("*")
                    if path.is_file()
                    and path.suffix in {".json", ".jsonl", ".parquet"}
                    and path.name != "episode_labels.json"
                )
                if manifest == root / "meta" / "info.json"
                else [manifest]
            )
            versions = []
            for metadata_path in metadata_paths:
                metadata_path = metadata_path.resolve()
                relative = metadata_path.relative_to(root).as_posix()
                metadata = metadata_path.stat()
                versions.append(
                    [
                        relative,
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_size,
                        metadata.st_mtime_ns,
                        metadata.st_ctime_ns,
                    ]
                )
            source_id = hashlib.sha256(f"local:{root}".encode()).hexdigest()
            generation = json.dumps(versions)
            return source_id, hashlib.sha256(generation.encode()).hexdigest()

        try:
            result = await asyncio.to_thread(local_revision)
        except (OSError, ValueError) as error:
            logger.warning("Source generation lookup failed: %s", type(error).__name__)
            raise ValueError("Source generation is unavailable") from None
        logger.debug(
            "Resolved source generation dataset=%s episode=%d",
            dataset_id.replace("\r", "").replace("\n", ""),
            int(episode_idx),
        )
        return result

    async def _get_edit_episode(self, dataset_id: str, episode_idx: int) -> EpisodeData | None:
        source_id, revision = await self.get_source_revision(dataset_id, episode_idx)
        cache_key = json.dumps([dataset_id, source_id, revision, episode_idx])
        cached = self._edit_context_cache.get(cache_key, episode_idx)
        if cached is not None:
            return cached

        def load(path: Path) -> EpisodeData | None:
            for handler in (LeRobotFormatHandler(), HDF5FormatHandler()):
                if handler.get_loader(dataset_id, path):
                    metadata = handler.discover(dataset_id, path)
                    return handler.load_episode(dataset_id, episode_idx, dataset_info=metadata)
            return None

        if dataset_id in self._blob_dataset_ids and dataset_id not in self._local_dataset_ids:
            provider = self._blob_provider
            if provider is None:
                raise ValueError("Source storage unavailable")
            async with self._edit_blob_sync_lock:
                if self._edit_blob_synced is None or self._edit_blob_synced[0] != cache_key:
                    if self._edit_blob_synced is not None:
                        await asyncio.to_thread(shutil.rmtree, self._edit_blob_synced[1], ignore_errors=True)
                        self._edit_blob_synced = None
                    path = Path(tempfile.mkdtemp(prefix="dvw_edit_"))
                    try:
                        if not await provider.sync_episode_to_local(dataset_id, path, episode_idx):
                            if not await provider.sync_hdf5_dataset_to_local(dataset_id, path):
                                raise ValueError("Source synchronization unavailable")
                            if not await provider.sync_hdf5_episode_to_local(dataset_id, path, episode_idx):
                                raise ValueError("Source synchronization unavailable")
                    except BaseException:
                        await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
                        raise
                    self._edit_blob_synced = (cache_key, path)
                path = self._edit_blob_synced[1]
                episode = await asyncio.to_thread(load, path)
        else:
            episode = await asyncio.to_thread(load, self._get_dataset_path(dataset_id))

        if episode is not None:
            if await self.get_source_revision(dataset_id, episode_idx) != (source_id, revision):
                logger.warning(
                    "Source changed during episode read dataset=%s episode=%d",
                    dataset_id.replace("\r", "").replace("\n", ""),
                    int(episode_idx),
                )
                raise ValueError("Source changed during episode read")
            episode.source_id = source_id
            episode.source_revision = revision
            self._edit_context_cache.put(cache_key, episode_idx, episode)
        logger.debug(
            "Loaded fresh edit context dataset=%s episode=%d",
            dataset_id.replace("\r", "").replace("\n", ""),
            int(episode_idx),
        )
        return episode

    async def get_episode(self, dataset_id: str, episode_idx: int, *, fresh: bool = False) -> EpisodeData | None:
        """Get complete data for a specific episode."""
        if fresh or (dataset_id in self._blob_dataset_ids and dataset_id not in self._local_dataset_ids):
            episode = await self._get_edit_episode(dataset_id, episode_idx)
            if episode is not None:
                annotated_indices = set(await self._storage.list_annotated_episodes(dataset_id))
                episode.meta.has_annotations = episode_idx in annotated_indices
            return episode
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
            synced_path = await self._ensure_blob_synced(dataset_id, episode_idx)
            if synced_path is not None and self._lerobot_handler.get_loader(dataset_id, synced_path):
                handler = self._lerobot_handler

        # Try blob-synced HDF5
        if handler is None and self._blob_provider is not None:
            synced_path = await self._ensure_blob_hdf5_synced(dataset_id)
            if synced_path is not None and self._hdf5_handler.get_loader(dataset_id, synced_path):
                await self._blob_provider.sync_hdf5_episode_to_local(dataset_id, synced_path, episode_idx)
                handler = self._hdf5_handler

        # HDF5 blob datasets: ensure episode file is downloaded even when
        # the handler was already registered during list_episodes (placeholders).
        if handler is self._hdf5_handler and self._blob_provider is not None:
            synced_path = self._blob_hdf5_synced.get(dataset_id)
            if synced_path is not None:
                await self._blob_provider.sync_hdf5_episode_to_local(dataset_id, synced_path, episode_idx)

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
        cached = self._episode_cache.get(dataset_id, episode_idx)
        if cached is not None:
            return cached.trajectory_data

        return self._try_handlers(dataset_id, "get_trajectory", episode_idx) or []

    # ------------------------------------------------------------------
    # Background prefetch
    # ------------------------------------------------------------------

    def _schedule_prefetch(self, dataset_id: str, episode_idx: int) -> None:
        """Schedule background loading of adjacent episodes into the cache."""
        if dataset_id in self._blob_dataset_ids and dataset_id not in self._local_dataset_ids:
            return
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
                    synced_path = await self._ensure_blob_synced(dataset_id, idx)
                    if synced_path is not None and self._lerobot_handler.get_loader(dataset_id, synced_path):
                        handler = self._lerobot_handler

                if handler is None and dataset_id in self._blob_dataset_ids:
                    synced_path = await self._ensure_blob_hdf5_synced(dataset_id)
                    if synced_path is not None and self._hdf5_handler.get_loader(dataset_id, synced_path):
                        if self._blob_provider is not None:
                            await self._blob_provider.sync_hdf5_episode_to_local(dataset_id, synced_path, idx)
                        handler = self._hdf5_handler

                # HDF5 blob datasets: ensure episode file is downloaded
                if handler is self._hdf5_handler and self._blob_provider is not None:
                    synced_path = self._blob_hdf5_synced.get(dataset_id)
                    if synced_path is not None:
                        await self._blob_provider.sync_hdf5_episode_to_local(dataset_id, synced_path, idx)

                if handler is None:
                    break
                episode = handler.load_episode(dataset_id, idx, dataset_info=dataset)
                if episode is not None:
                    self._episode_cache.put(dataset_id, idx, episode)
                    logger.debug(
                        "Prefetched episode %s/%d",
                        dataset_id.replace("\r", "").replace("\n", ""),
                        int(idx),
                    )

        # Clean up completed tasks
        self._prefetch_tasks = {t for t in self._prefetch_tasks if not t.done()}

        coro = _prefetch()
        try:
            task = asyncio.create_task(coro)
            self._prefetch_tasks.add(task)
            task.add_done_callback(self._prefetch_tasks.discard)
        except RuntimeError as error:
            coro.close()
            logger.debug("Skipping episode prefetch for episode %d: %s", int(episode_idx), error)

    def is_safe_video_path(self, video_path: str) -> bool:
        """Check whether a video path falls within the base path or a blob-synced temp dir."""
        normalized = os.path.normpath(os.path.realpath(video_path))
        safe_base = os.path.realpath(self.base_path)
        if normalized.startswith(safe_base + os.sep) or normalized == safe_base:
            return True
        for synced_dirs in (self._blob_synced, self._blob_hdf5_synced):
            for synced_dir in synced_dirs.values():
                safe_synced = os.path.realpath(str(synced_dir))
                if normalized.startswith(safe_synced + os.sep) or normalized == safe_synced:
                    return True
        return False

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

    def get_dataset_contract(self, dataset_id: str) -> AcceptedDatasetContract | None:
        """Return validated descriptor metadata with matching artifact digests."""
        try:
            dataset_path = self._get_dataset_path(dataset_id)
            descriptor_path = dataset_path / "accepted-dataset.json"
            if not descriptor_path.is_file():
                return None
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            if not isinstance(descriptor, dict) or descriptor.get("schema_version") != 1:
                raise ValueError("unsupported accepted-dataset descriptor schema")
            if descriptor.get("dataset_id") != dataset_id:
                raise ValueError("accepted-dataset descriptor ID differs from its directory")
            artifacts = descriptor.get("artifacts")
            if not isinstance(artifacts, dict):
                raise ValueError("accepted-dataset descriptor has no artifact identities")
            expected_files = {
                "capture_provenance": "capture-provenance.json",
                "export_validation": "export-validation.json",
            }
            hashes = {}
            for name, filename in expected_files.items():
                identity = artifacts.get(name)
                if not isinstance(identity, dict) or identity.get("file") != filename:
                    raise ValueError(f"accepted-dataset {name} identity is invalid")
                path = dataset_path / filename
                if not path.is_file():
                    raise ValueError(f"accepted-dataset artifact is missing: {filename}")
                with path.open("rb") as stream:
                    actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                if identity.get("sha256") != actual_hash:
                    raise ValueError(f"accepted-dataset artifact hash differs: {filename}")
                hashes[name] = actual_hash
            return AcceptedDatasetContract.model_validate(
                {
                    "dataset_id": dataset_id,
                    "output_adapter_id": descriptor.get("output_adapter_id"),
                    "output_adapter_version": descriptor.get("output_adapter_version"),
                    "viewer_adapter_id": descriptor.get("viewer_adapter_id"),
                    "profile_id": descriptor.get("profile_id"),
                    "profile_sha256": descriptor.get("profile_sha256"),
                    "capture_provenance_sha256": hashes["capture_provenance"],
                    "export_validation_sha256": hashes["export_validation"],
                    "capture_features": descriptor.get("capture_features"),
                    "sensors": descriptor.get("sensors"),
                }
            )
        except (OSError, ValueError) as error:
            logger.warning(
                "Ignoring invalid accepted-dataset contract for '%s': %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                error,
            )
            return None

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
        """Get a single frame image from an episode.

        When no local video is available (blob-only datasets, or local
        datasets that only carry meta/), falls back to materializing the
        episode video from blob storage and extracting the frame.
        """
        result = self._try_handlers(dataset_id, "get_frame_image", episode_idx, frame_idx, camera)
        if result is not None:
            return result

        if self._blob_provider is None:
            logger.warning(
                "No loader found for dataset %s",
                dataset_id.replace("\r", "").replace("\n", ""),
            )
            return None

        # Ensure the dataset is registered as blob-backed so downstream
        # blob lookups (info.json cache, video index) succeed.
        if dataset_id not in self._blob_dataset_ids:
            discovered = await self._discover_blob_dataset(dataset_id)
            if discovered is None:
                logger.warning(
                    "No loader and no blob dataset for %s",
                    dataset_id.replace("\r", "").replace("\n", ""),
                )
                return None

        blob_path = await self.get_blob_video_path(dataset_id, episode_idx, camera)
        if blob_path is None:
            logger.warning(
                "No blob video found for dataset %s ep %d camera %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                int(episode_idx),
                camera.replace("\r", "").replace("\n", ""),
            )
            return None

        local_path = await self.materialize_blob_video(blob_path)
        if local_path is None:
            return None

        dataset = self._datasets.get(dataset_id)
        fps = float(dataset.fps) if dataset and dataset.fps else 30.0
        window = await self._blob_provider.get_episode_video_window(dataset_id, episode_idx, camera)
        if frame_idx < 0 or (window is not None and frame_idx / fps >= window[1] - window[0]):
            return None
        if window is not None:
            frame_idx += round(window[0] * fps)
        frame = await asyncio.to_thread(self._lerobot_handler._extract_frame_ffmpeg, str(local_path), frame_idx, fps)
        if frame is not None:
            return frame
        return await asyncio.to_thread(self._lerobot_handler._extract_frame_cv2, str(local_path), frame_idx)

    async def get_episode_cameras(self, dataset_id: str, episode_idx: int) -> list[str]:
        """Get list of available cameras for an episode."""
        cameras = self._try_handlers(dataset_id, "get_cameras", episode_idx)
        if cameras:
            return cameras
        if self._blob_provider is not None:
            info = await self._blob_provider.get_info_json(dataset_id)
            if info and 0 <= episode_idx < int(info.get("total_episodes", 0)):
                return [name for name, feature in info.get("features", {}).items() if feature.get("dtype") == "video"]
        return []

    def get_video_file_path(self, dataset_id: str, episode_idx: int, camera: str) -> str | None:
        """Get the filesystem path to a video file, generating on-demand for HDF5.

        When a video is generated for an HDF5 dataset with blob storage,
        uploads the result to blob for caching across container restarts.
        """
        handler = self._resolve_handler(dataset_id)
        if handler is None and self._hdf5_handler.has_loader(dataset_id):
            handler = self._hdf5_handler
        if handler is None:
            return None

        if handler is self._hdf5_handler:
            cache_path = self._hdf5_handler._video_cache_path(dataset_id, episode_idx, camera)
            if cache_path is None:
                return None
            already_existed = cache_path.exists()
            result = handler.get_video_path(dataset_id, episode_idx, camera)
            if result and not already_existed and self._blob_provider is not None:
                self._upload_video_to_blob(dataset_id, episode_idx, camera, cache_path)
            return result

        return handler.get_video_path(dataset_id, episode_idx, camera)

    def _upload_video_to_blob(self, dataset_id: str, episode_idx: int, camera: str, cache_path: Path) -> None:
        """Upload a generated video to blob storage for caching."""
        if self._blob_provider is None:
            return

        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._blob_provider.upload_video(dataset_id, camera, episode_idx, cache_path))
            loop.close()
        except Exception as exc:
            logger.warning(
                "Blob upload failed for %s ep %d: %s",
                dataset_id.replace("\r", "").replace("\n", ""),
                int(episode_idx),
                exc,
            )


# Global service instance
_dataset_service: DatasetService | None = None


def get_dataset_service() -> DatasetService:
    """
    Get the global dataset service instance.

    On first call, reads application config and creates the appropriate
    storage adapter and optional BlobDatasetProvider based on STORAGE_BACKEND.

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
