"""
LRU episode cache for parsed episode data.

Provides a fixed-capacity circular buffer that caches fully parsed
EpisodeData objects, avoiding repeated parquet/HDF5 reads and
numpy-to-JSON conversion on every episode request.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...models.datasources import EpisodeData

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheStats:
    """Snapshot of cache performance metrics."""

    capacity: int
    size: int
    hits: int
    misses: int
    total_bytes: int
    max_memory_bytes: int

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


@dataclass
class EpisodeCache:
    """
    LRU cache for parsed episode data.

    Enforces two eviction thresholds: a max entry *capacity* and a
    *max_memory_bytes* budget.  Whichever limit is hit first triggers
    LRU eviction.  Set either to ``0`` to disable that dimension
    (capacity ``0`` disables caching entirely; memory ``0`` means
    count-only eviction).
    """

    capacity: int = 32
    max_memory_bytes: int = 100 * 1024 * 1024  # 100 MB default
    _entries: OrderedDict[tuple[str, int], EpisodeData] = field(
        default_factory=OrderedDict,
        init=False,
        repr=False,
    )
    _entry_sizes: dict[tuple[str, int], int] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _total_bytes: int = field(default=0, init=False, repr=False)
    _hits: int = field(default=0, init=False, repr=False)
    _misses: int = field(default=0, init=False, repr=False)

    @staticmethod
    def _estimate_episode_bytes(data: EpisodeData) -> int:
        """Estimate the in-memory byte size of a cached EpisodeData."""
        n_points = len(data.trajectory_data)
        if n_points == 0:
            return 200

        sample = data.trajectory_data[0]
        floats_per_point = (
            len(sample.joint_positions)
            + len(sample.joint_velocities)
            + len(sample.end_effector_pose)
            + 3  # timestamp, frame, gripper_state
        )
        # ~8 bytes per float + Pydantic model overhead (~80 bytes per TrajectoryPoint)
        per_point = floats_per_point * 8 + 80
        return n_points * per_point + 500  # 500 bytes for meta + video_urls overhead

    @property
    def enabled(self) -> bool:
        return self.capacity > 0

    def get(self, dataset_id: str, episode_index: int) -> EpisodeData | None:
        """Retrieve a cached episode, promoting it to most-recently-used."""
        if not self.enabled:
            return None

        key = (dataset_id, episode_index)
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
            self._hits += 1
            return entry

        self._misses += 1
        return None

    def put(self, dataset_id: str, episode_index: int, data: EpisodeData) -> None:
        """Insert or update a cache entry, evicting LRU entries until both
        count and memory thresholds are satisfied."""
        if not self.enabled:
            return

        key = (dataset_id, episode_index)
        entry_bytes = self._estimate_episode_bytes(data)

        if key in self._entries:
            old_bytes = self._entry_sizes.get(key, 0)
            self._total_bytes -= old_bytes
            self._entries.move_to_end(key)
            self._entries[key] = data
            self._entry_sizes[key] = entry_bytes
            self._total_bytes += entry_bytes
            return

        # Evict until count capacity is satisfied
        while len(self._entries) >= self.capacity:
            evicted_key, _ = self._entries.popitem(last=False)
            self._total_bytes -= self._entry_sizes.pop(evicted_key, 0)
            logger.debug("Episode cache evicted %s (count)", evicted_key)

        # Evict until memory budget is satisfied (0 = unlimited)
        if self.max_memory_bytes > 0:
            while self._entries and self._total_bytes + entry_bytes > self.max_memory_bytes:
                evicted_key, _ = self._entries.popitem(last=False)
                self._total_bytes -= self._entry_sizes.pop(evicted_key, 0)
                logger.debug("Episode cache evicted %s (memory)", evicted_key)

        self._entries[key] = data
        self._entry_sizes[key] = entry_bytes
        self._total_bytes += entry_bytes

    def invalidate(self, dataset_id: str, episode_index: int | None = None) -> int:
        """
        Remove cache entries.

        Args:
            dataset_id: Dataset to invalidate.
            episode_index: Specific episode to remove. When ``None``,
                           all episodes for the dataset are removed.

        Returns:
            Number of entries removed.
        """
        if not self.enabled:
            return 0

        if episode_index is not None:
            key = (dataset_id, episode_index)
            if key in self._entries:
                self._total_bytes -= self._entry_sizes.pop(key, 0)
                del self._entries[key]
                return 1
            return 0

        keys_to_remove = [k for k in self._entries if k[0] == dataset_id]
        for key in keys_to_remove:
            self._total_bytes -= self._entry_sizes.pop(key, 0)
            del self._entries[key]

        return len(keys_to_remove)

    def clear(self) -> None:
        """Remove all entries and reset counters."""
        self._entries.clear()
        self._entry_sizes.clear()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0

    def stats(self) -> CacheStats:
        """Return a snapshot of cache performance metrics."""
        return CacheStats(
            capacity=self.capacity,
            size=len(self._entries),
            hits=self._hits,
            misses=self._misses,
            total_bytes=self._total_bytes,
            max_memory_bytes=self.max_memory_bytes,
        )
