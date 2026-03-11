"""Unit tests for the LRU episode cache."""

import pytest

from src.api.models.datasources import EpisodeData, EpisodeMeta, TrajectoryPoint
from src.api.services.episode_cache import CacheStats, EpisodeCache


def _make_episode(index: int, length: int = 10) -> EpisodeData:
    """Build a minimal EpisodeData for cache testing."""
    return EpisodeData(
        meta=EpisodeMeta(index=index, length=length, task_index=0, has_annotations=False),
        video_urls={},
        trajectory_data=[
            TrajectoryPoint(
                frame=f,
                timestamp=f * 0.1,
                joint_positions=[0.0] * 6,
                joint_velocities=[],
                end_effector_pose=[],
                gripper_state=0.0,
            )
            for f in range(length)
        ],
    )


class TestEpisodeCacheBasics:
    """Core get/put/eviction behavior."""

    def test_put_and_get_returns_cached_episode(self):
        cache = EpisodeCache(capacity=4)
        ep = _make_episode(0)
        cache.put("ds", 0, ep)

        assert cache.get("ds", 0) is ep

    def test_get_miss_returns_none(self):
        cache = EpisodeCache(capacity=4)
        assert cache.get("ds", 99) is None

    def test_evicts_lru_when_at_capacity(self):
        cache = EpisodeCache(capacity=2)
        cache.put("ds", 0, _make_episode(0))
        cache.put("ds", 1, _make_episode(1))
        cache.put("ds", 2, _make_episode(2))

        assert cache.get("ds", 0) is None, "oldest entry should be evicted"
        assert cache.get("ds", 1) is not None
        assert cache.get("ds", 2) is not None

    def test_access_promotes_entry(self):
        cache = EpisodeCache(capacity=2)
        cache.put("ds", 0, _make_episode(0))
        cache.put("ds", 1, _make_episode(1))

        # Access episode 0 to promote it
        cache.get("ds", 0)

        # Insert a third — episode 1 (LRU) should be evicted, not 0
        cache.put("ds", 2, _make_episode(2))

        assert cache.get("ds", 0) is not None, "recently accessed should survive"
        assert cache.get("ds", 1) is None, "LRU entry should be evicted"

    def test_put_updates_existing_entry(self):
        cache = EpisodeCache(capacity=4)
        ep_a = _make_episode(0, length=5)
        ep_b = _make_episode(0, length=10)
        cache.put("ds", 0, ep_a)
        cache.put("ds", 0, ep_b)

        result = cache.get("ds", 0)
        assert result is ep_b
        assert cache.stats().size == 1

    def test_cross_dataset_isolation(self):
        cache = EpisodeCache(capacity=4)
        ep_a = _make_episode(0)
        ep_b = _make_episode(0)
        cache.put("ds_a", 0, ep_a)
        cache.put("ds_b", 0, ep_b)

        assert cache.get("ds_a", 0) is ep_a
        assert cache.get("ds_b", 0) is ep_b


class TestEpisodeCacheInvalidation:
    """Entry removal and cache clearing."""

    def test_invalidate_single_episode(self):
        cache = EpisodeCache(capacity=4)
        cache.put("ds", 0, _make_episode(0))
        cache.put("ds", 1, _make_episode(1))

        removed = cache.invalidate("ds", 0)

        assert removed == 1
        assert cache.get("ds", 0) is None
        assert cache.get("ds", 1) is not None

    def test_invalidate_all_episodes_for_dataset(self):
        cache = EpisodeCache(capacity=8)
        for i in range(4):
            cache.put("ds_a", i, _make_episode(i))
        cache.put("ds_b", 0, _make_episode(0))

        removed = cache.invalidate("ds_a")

        assert removed == 4
        assert cache.stats().size == 1
        assert cache.get("ds_b", 0) is not None

    def test_invalidate_missing_returns_zero(self):
        cache = EpisodeCache(capacity=4)
        assert cache.invalidate("ds", 99) == 0

    def test_clear_removes_all_and_resets_counters(self):
        cache = EpisodeCache(capacity=4)
        cache.put("ds", 0, _make_episode(0))
        cache.get("ds", 0)
        cache.get("ds", 99)

        cache.clear()

        assert cache.stats().size == 0
        assert cache.stats().hits == 0
        assert cache.stats().misses == 0


class TestEpisodeCacheStats:
    """Performance metrics reporting."""

    def test_stats_tracks_hits_and_misses(self):
        cache = EpisodeCache(capacity=4)
        cache.put("ds", 0, _make_episode(0))

        cache.get("ds", 0)
        cache.get("ds", 0)
        cache.get("ds", 99)

        stats = cache.stats()
        assert stats.hits == 2
        assert stats.misses == 1
        assert stats.size == 1
        assert stats.capacity == 4

    def test_hit_rate_calculation(self):
        stats = CacheStats(capacity=10, size=5, hits=80, misses=20, total_bytes=1024, max_memory_bytes=0)
        assert stats.hit_rate == pytest.approx(0.8)

    def test_hit_rate_zero_when_no_requests(self):
        stats = CacheStats(capacity=10, size=0, hits=0, misses=0, total_bytes=0, max_memory_bytes=0)
        assert stats.hit_rate == 0.0


class TestEpisodeCacheEvictDatasetIntegration:
    """Cache invalidation when a dataset is evicted from the service."""

    def test_invalidate_clears_all_dataset_episodes(self):
        cache = EpisodeCache(capacity=16)
        for i in range(5):
            cache.put("target", i, _make_episode(i))
        cache.put("other", 0, _make_episode(0))

        removed = cache.invalidate("target")

        assert removed == 5
        assert cache.stats().size == 1
        assert cache.get("other", 0) is not None

    def test_invalidate_single_after_annotation_save(self):
        cache = EpisodeCache(capacity=16)
        cache.put("ds", 0, _make_episode(0))
        cache.put("ds", 1, _make_episode(1))

        cache.invalidate("ds", 0)

        assert cache.get("ds", 0) is None
        assert cache.get("ds", 1) is not None


class TestEpisodeCacheDisabled:
    """Behavior when capacity is 0 (disabled)."""

    def test_disabled_cache_skips_put(self):
        cache = EpisodeCache(capacity=0)
        cache.put("ds", 0, _make_episode(0))
        assert cache.get("ds", 0) is None

    def test_disabled_cache_reports_not_enabled(self):
        cache = EpisodeCache(capacity=0)
        assert cache.enabled is False

    def test_disabled_invalidate_returns_zero(self):
        cache = EpisodeCache(capacity=0)
        assert cache.invalidate("ds") == 0

    def test_disabled_stats_show_zero(self):
        cache = EpisodeCache(capacity=0)
        stats = cache.stats()
        assert stats.size == 0
        assert stats.hits == 0
        assert stats.misses == 0


class TestEpisodeCacheMemoryBudget:
    """Memory-budget eviction alongside count-based eviction."""

    def test_evicts_by_memory_budget(self):
        # Use a very small budget so 2 entries exceed it
        ep = _make_episode(0, length=100)
        entry_size = EpisodeCache._estimate_episode_bytes(ep)

        cache = EpisodeCache(capacity=100, max_memory_bytes=int(entry_size * 1.5))
        cache.put("ds", 0, _make_episode(0, length=100))
        cache.put("ds", 1, _make_episode(1, length=100))

        assert cache.get("ds", 0) is None, "should be evicted by memory budget"
        assert cache.get("ds", 1) is not None

    def test_tracks_total_bytes(self):
        cache = EpisodeCache(capacity=10, max_memory_bytes=100 * 1024 * 1024)
        cache.put("ds", 0, _make_episode(0, length=50))
        cache.put("ds", 1, _make_episode(1, length=100))

        stats = cache.stats()
        assert stats.total_bytes > 0
        assert stats.max_memory_bytes == 100 * 1024 * 1024

    def test_total_bytes_decreases_on_eviction(self):
        ep = _make_episode(0, length=100)
        entry_size = EpisodeCache._estimate_episode_bytes(ep)

        cache = EpisodeCache(capacity=2)
        cache.put("ds", 0, _make_episode(0, length=100))
        cache.put("ds", 1, _make_episode(1, length=100))
        bytes_at_two = cache.stats().total_bytes

        cache.put("ds", 2, _make_episode(2, length=100))
        bytes_at_eviction = cache.stats().total_bytes

        assert bytes_at_eviction < bytes_at_two + entry_size

    def test_total_bytes_zero_after_clear(self):
        cache = EpisodeCache(capacity=10)
        cache.put("ds", 0, _make_episode(0, length=50))
        cache.clear()

        assert cache.stats().total_bytes == 0

    def test_total_bytes_decreases_on_invalidate(self):
        cache = EpisodeCache(capacity=10)
        cache.put("ds", 0, _make_episode(0, length=50))
        cache.put("ds", 1, _make_episode(1, length=50))
        bytes_before = cache.stats().total_bytes

        cache.invalidate("ds", 0)

        assert cache.stats().total_bytes < bytes_before

    def test_estimate_episode_bytes_scales_with_length(self):
        small = _make_episode(0, length=10)
        large = _make_episode(1, length=1000)

        small_bytes = EpisodeCache._estimate_episode_bytes(small)
        large_bytes = EpisodeCache._estimate_episode_bytes(large)

        assert large_bytes > small_bytes * 50

    def test_unlimited_memory_uses_count_only(self):
        cache = EpisodeCache(capacity=2, max_memory_bytes=0)
        cache.put("ds", 0, _make_episode(0, length=100))
        cache.put("ds", 1, _make_episode(1, length=100))
        cache.put("ds", 2, _make_episode(2, length=100))

        assert cache.get("ds", 0) is None
        assert cache.stats().size == 2
