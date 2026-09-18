"""Property-based tests for dataviewer backend pure functions.

Uses Hypothesis to verify invariants across large input spaces for
validation, sanitization, caching, serialization, and path utilities.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import hypothesis.strategies as st
import numpy as np
import pytest
from fastapi import HTTPException
from hypothesis import assume, given, settings
from hypothesis.extra.numpy import arrays
from numpy.typing import NDArray

from src.api.models.datasources import DatasetInfo
from src.api.services.dataset_service import DatasetService
from src.api.services.dataset_service.lerobot_handler import LeRobotFormatHandler
from src.api.services.episode_cache import CacheStats, EpisodeCache
from src.api.services.frame_interpolation import (
    interpolate_frame_data,
    interpolate_image,
)
from src.api.services.trajectory_analysis import TrajectoryAnalyzer
from src.api.storage.paths import dataset_id_to_blob_prefix
from src.api.storage.serializers import DateTimeEncoder
from src.api.validation import (
    SAFE_CAMERA_NAME_PATTERN,
    SAFE_DATASET_ID_PATTERN,
    SanitizedModel,
    path_string_param,
    sanitize_user_string,
    validate_safe_string,
)

# ===================================================================
# Strategies
# ===================================================================

_valid_dataset_ids = st.from_regex(re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,50}"), fullmatch=True)

_valid_nested_dataset_id_parts = st.from_regex(
    re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9._-]{0,19}[a-zA-Z0-9._])?"),
    fullmatch=True,
)

_valid_camera_names = st.from_regex(re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,30}"), fullmatch=True)

_nested_json = st.recursive(
    st.one_of(st.text(max_size=50), st.integers(), st.floats(allow_nan=False), st.none()),
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.tuples(children),
        st.dictionaries(st.text(max_size=10), children, max_size=5),
    ),
    max_leaves=20,
)


class NestedSanitizedModel(SanitizedModel):
    value: object


# ===================================================================
# sanitize_user_string
# ===================================================================


class TestSanitizeUserStringProperties:
    @given(text=st.text(max_size=500))
    def test_output_never_contains_cr_or_lf(self, text: str) -> None:
        result = sanitize_user_string(text)
        assert "\r" not in result
        assert "\n" not in result

    @given(text=st.text(max_size=500))
    def test_idempotent(self, text: str) -> None:
        once = sanitize_user_string(text)
        twice = sanitize_user_string(once)
        assert once == twice

    @given(text=st.text(max_size=500))
    def test_preserves_non_crlf_characters(self, text: str) -> None:
        result = sanitize_user_string(text)
        expected = text.replace("\r", "").replace("\n", "")
        assert result == expected

    @given(text=st.text(max_size=500))
    def test_length_never_increases(self, text: str) -> None:
        assert len(sanitize_user_string(text)) <= len(text)


# ===================================================================
# SanitizedModel
# ===================================================================


class TestSanitizedModelProperties:
    @given(value=_nested_json)
    def test_preserves_top_level_container_type(self, value: object) -> None:
        result = NestedSanitizedModel(value=value).value
        assert type(result) is type(value)

    @given(value=st.text(max_size=200))
    def test_sanitizes_string_values(self, value: str) -> None:
        result = NestedSanitizedModel(value=value).value
        assert result == sanitize_user_string(value)

    @given(items=st.lists(st.text(max_size=50), max_size=10))
    def test_sanitizes_all_list_elements(self, items: list[str]) -> None:
        result = NestedSanitizedModel(value=items).value
        assert result == [sanitize_user_string(item) for item in items]

    @given(mapping=st.dictionaries(st.text(max_size=20), st.text(max_size=50), max_size=10))
    def test_sanitizes_all_dict_keys_and_values(self, mapping: dict[str, str]) -> None:
        result = NestedSanitizedModel(value=mapping).value
        expected = {sanitize_user_string(key): sanitize_user_string(value) for key, value in mapping.items()}
        assert result == expected

    @given(value=st.one_of(st.integers(), st.floats(allow_nan=False), st.none()))
    def test_preserves_non_string_values(self, value: int | float | None) -> None:
        result = NestedSanitizedModel(value=value).value
        assert result == value


# ===================================================================
# validate_safe_string
# ===================================================================


class TestValidateSafeStringProperties:
    @given(value=_valid_dataset_ids)
    def test_valid_dataset_ids_accepted(self, value: str) -> None:
        result = validate_safe_string(value, pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")
        assert result == value

    @given(value=_valid_camera_names)
    def test_valid_camera_names_accepted(self, value: str) -> None:
        result = validate_safe_string(value, pattern=SAFE_CAMERA_NAME_PATTERN, label="camera")
        assert result == value

    @given(value=st.text(min_size=1, max_size=100))
    def test_null_bytes_always_rejected(self, value: str) -> None:
        injected = value[:1] + "\x00" + value[1:]

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_string(injected, pattern=SAFE_DATASET_ID_PATTERN, label="test")

        assert exc_info.value.status_code == 400

    @given(prefix=st.text(min_size=1, max_size=50))
    def test_slash_always_rejected(self, prefix: str) -> None:
        assume("\x00" not in prefix and prefix not in (".", ".."))

        for char in ("/", "\\"):
            with pytest.raises(HTTPException) as exc_info:
                validate_safe_string(prefix + char, pattern=SAFE_DATASET_ID_PATTERN, label="test")

            assert exc_info.value.status_code == 400

    @given(value=_valid_dataset_ids)
    def test_idempotent_for_valid_inputs(self, value: str) -> None:
        first = validate_safe_string(value, pattern=SAFE_DATASET_ID_PATTERN, label="test")
        second = validate_safe_string(first, pattern=SAFE_DATASET_ID_PATTERN, label="test")
        assert first == second


# ===================================================================
# Dataset ID validation through public callers
# ===================================================================


class TestDatasetIdValidationProperties:
    @given(parts=st.lists(_valid_nested_dataset_id_parts, min_size=1, max_size=5))
    def test_valid_nested_ids_are_accepted_by_path_dependency(self, parts: list[str]) -> None:
        dataset_id = "--".join(parts)
        dependency = path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")

        assert dependency(dataset_id) == dataset_id

    @pytest.mark.asyncio
    async def test_six_level_existing_dataset_is_rejected_by_public_lookup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        valid_parts = ["one", "two", "three", "four", "five"]
        invalid_parts = [*valid_parts, "six"]
        tmp_path.joinpath(*invalid_parts).mkdir(parents=True)
        monkeypatch.setattr(LeRobotFormatHandler, "can_handle", lambda _self, _path: True)
        monkeypatch.setattr(
            LeRobotFormatHandler,
            "discover",
            lambda _self, dataset_id, _path: DatasetInfo(
                id=dataset_id,
                name=dataset_id,
                total_episodes=0,
                fps=30.0,
            ),
        )
        service = DatasetService(base_path=str(tmp_path), episode_cache_capacity=0)

        accepted = await service.get_dataset("--".join(valid_parts))
        rejected = await service.get_dataset("--".join(invalid_parts))

        assert accepted is not None
        assert accepted.id == "--".join(valid_parts)
        assert rejected is None

    @given(value=st.text(min_size=1, max_size=100))
    def test_traversal_is_rejected_by_path_dependency(self, value: str) -> None:
        dependency = path_string_param("dataset_id", pattern=SAFE_DATASET_ID_PATTERN, label="dataset_id")

        for separator in ("/", "\\"):
            candidate = value + separator
            with pytest.raises(HTTPException) as exc_info:
                dependency(candidate)

            assert exc_info.value.status_code == 400
            sanitized = sanitize_user_string(candidate)
            assert exc_info.value.detail == f"Invalid dataset_id: '{sanitized}'"


# ===================================================================
# dataset_id_to_blob_prefix
# ===================================================================


class TestDatasetIdToBlobPrefixProperties:
    @given(value=st.text(max_size=200))
    def test_no_double_dash_in_output(self, value: str) -> None:
        result = dataset_id_to_blob_prefix(value)
        assert "--" not in result

    @given(value=st.text(max_size=200))
    def test_dash_count_equals_slash_count(self, value: str) -> None:
        dd_count = value.count("--")
        result = dataset_id_to_blob_prefix(value)
        new_slashes = result.count("/") - value.count("/")
        assert new_slashes == dd_count

    @given(
        parts=st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=5),
    )
    def test_roundtrip_with_join_split(self, parts: list[str]) -> None:
        assume(all("--" not in p and "/" not in p and "-" not in p for p in parts))
        dataset_id = "--".join(parts)
        blob_prefix = dataset_id_to_blob_prefix(dataset_id)
        assert blob_prefix == "/".join(parts)

    @given(value=st.text(max_size=200))
    def test_idempotent_when_no_double_dashes(self, value: str) -> None:
        assume("--" not in value)
        assert dataset_id_to_blob_prefix(value) == value


# ===================================================================
# DateTimeEncoder
# ===================================================================


class TestDateTimeEncoderProperties:
    @given(
        dt=st.datetimes(
            min_value=datetime(1, 1, 1),
            max_value=datetime(9999, 12, 31),
            timezones=st.just(UTC),
        )
    )
    def test_datetime_produces_iso_string(self, dt: datetime) -> None:
        result = json.loads(json.dumps({"ts": dt}, cls=DateTimeEncoder))
        assert isinstance(result["ts"], str)
        parsed = datetime.fromisoformat(result["ts"])
        assert parsed == dt

    @given(
        dt=st.datetimes(
            min_value=datetime(1, 1, 1),
            max_value=datetime(9999, 12, 31),
            timezones=st.just(UTC),
        )
    )
    def test_roundtrip_preserves_value(self, dt: datetime) -> None:
        encoded = json.dumps({"ts": dt}, cls=DateTimeEncoder)
        decoded = json.loads(encoded)
        restored = datetime.fromisoformat(decoded["ts"])
        assert restored == dt

    @given(value=st.one_of(st.integers(), st.text(max_size=50), st.booleans()))
    def test_non_datetime_passes_through(self, value: int | str | bool) -> None:
        result = json.loads(json.dumps({"v": value}, cls=DateTimeEncoder))
        assert result["v"] == value


# ===================================================================
# CacheStats
# ===================================================================


class TestCacheStatsProperties:
    @given(
        hits=st.integers(min_value=0, max_value=10_000),
        misses=st.integers(min_value=0, max_value=10_000),
    )
    def test_hit_rate_in_unit_interval(self, hits: int, misses: int) -> None:
        stats = CacheStats(capacity=32, size=0, hits=hits, misses=misses, total_bytes=0, max_memory_bytes=0)
        assert 0.0 <= stats.hit_rate <= 1.0

    @given(hits=st.integers(min_value=0, max_value=10_000))
    def test_zero_misses_gives_perfect_rate(self, hits: int) -> None:
        assume(hits > 0)
        stats = CacheStats(capacity=32, size=0, hits=hits, misses=0, total_bytes=0, max_memory_bytes=0)
        assert stats.hit_rate == 1.0

    def test_zero_total_gives_zero_rate(self) -> None:
        stats = CacheStats(capacity=32, size=0, hits=0, misses=0, total_bytes=0, max_memory_bytes=0)
        assert stats.hit_rate == 0.0


# ===================================================================
# EpisodeCache (stateful)
# ===================================================================


class TestEpisodeCacheProperties:
    @given(
        capacity=st.integers(min_value=1, max_value=20),
        n_puts=st.integers(min_value=1, max_value=50),
    )
    def test_capacity_never_exceeded(self, capacity: int, n_puts: int) -> None:
        cache = EpisodeCache(capacity=capacity, max_memory_bytes=0)
        for i in range(n_puts):
            cache.put("ds", i, _make_minimal_episode(i))
        assert cache.stats().size == min(capacity, n_puts)

    @given(index=st.integers(min_value=0, max_value=100))
    def test_get_after_put_returns_same_object(self, index: int) -> None:
        cache = EpisodeCache(capacity=32, max_memory_bytes=0)
        episode = _make_minimal_episode(index)
        cache.put("ds", index, episode)
        retrieved = cache.get("ds", index)
        assert retrieved is episode

    @given(index=st.integers(min_value=0, max_value=100))
    def test_miss_returns_none(self, index: int) -> None:
        cache = EpisodeCache(capacity=32, max_memory_bytes=0)
        assert cache.get("ds", index) is None

    @given(index=st.integers(min_value=0, max_value=100))
    def test_miss_increments_miss_counter(self, index: int) -> None:
        cache = EpisodeCache(capacity=32, max_memory_bytes=0)
        cache.get("ds", index)
        stats = cache.stats()
        assert stats.hits == 0
        assert stats.misses == 1

    @given(index=st.integers(min_value=0, max_value=100))
    def test_hit_increments_hit_counter(self, index: int) -> None:
        cache = EpisodeCache(capacity=32, max_memory_bytes=0)
        cache.put("ds", index, _make_minimal_episode(index))
        cache.get("ds", index)
        stats = cache.stats()
        assert stats.hits == 1
        assert stats.misses == 0

    @given(
        capacity=st.integers(min_value=2, max_value=10),
        extra=st.integers(min_value=1, max_value=5),
    )
    def test_lru_eviction_order(self, capacity: int, extra: int) -> None:
        cache = EpisodeCache(capacity=capacity, max_memory_bytes=0)
        total = capacity + extra
        for i in range(total):
            cache.put("ds", i, _make_minimal_episode(i))
        for i in range(extra):
            assert cache.get("ds", i) is None
        for i in range(extra, total):
            assert cache.get("ds", i) is not None

    def test_disabled_cache_always_misses(self) -> None:
        cache = EpisodeCache(capacity=0)
        cache.put("ds", 0, _make_minimal_episode(0))
        assert cache.get("ds", 0) is None

    @given(
        capacity=st.integers(min_value=1, max_value=10),
        n_puts=st.integers(min_value=0, max_value=20),
    )
    def test_stats_hits_plus_misses_equals_total_gets(self, capacity: int, n_puts: int) -> None:
        cache = EpisodeCache(capacity=capacity, max_memory_bytes=0)
        total_gets = 0
        for i in range(n_puts):
            cache.put("ds", i, _make_minimal_episode(i))
        for i in range(n_puts + 5):
            cache.get("ds", i)
            total_gets += 1
        stats = cache.stats()
        assert stats.hits + stats.misses == total_gets


# ===================================================================
# Helpers
# ===================================================================


@dataclass(frozen=True)
class _MinimalTrajectoryPoint:
    timestamp: float = 0.0
    frame: int = 0
    gripper_state: float = 0.0
    joint_positions: list[float] = field(default_factory=list)
    joint_velocities: list[float] = field(default_factory=list)
    end_effector_pose: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class _MinimalEpisodeMeta:
    episode_index: int = 0
    length: int = 0
    dataset_id: str = ""


@dataclass(frozen=True)
class _MinimalEpisode:
    meta: _MinimalEpisodeMeta = field(default_factory=_MinimalEpisodeMeta)
    video_urls: dict[str, str] = field(default_factory=dict)
    trajectory_data: list[_MinimalTrajectoryPoint] = field(default_factory=list)


def _make_minimal_episode(index: int, length: int = 5) -> _MinimalEpisode:
    """Build a lightweight episode-like object for cache tests."""
    return _MinimalEpisode(
        meta=_MinimalEpisodeMeta(episode_index=index, length=length, dataset_id="ds"),
        video_urls={},
        trajectory_data=[_MinimalTrajectoryPoint() for _ in range(length)],
    )


# ===================================================================
# Strategies — Frame Interpolation & Trajectory Analysis
# ===================================================================

_uint8_images = st.shared(
    st.tuples(
        st.integers(min_value=1, max_value=64),
        st.integers(min_value=1, max_value=64),
        st.integers(min_value=1, max_value=4),
    ),
    key="img_shape",
).flatmap(
    lambda shape: st.tuples(
        arrays(np.uint8, shape, elements=st.integers(0, 255)),
        arrays(np.uint8, shape, elements=st.integers(0, 255)),
    )
)

_interp_factor = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

_small_float_array = st.integers(min_value=3, max_value=50).flatmap(
    lambda n: st.tuples(
        st.just(n),
        arrays(np.float64, (n, 3), elements=st.floats(-1e3, 1e3, allow_nan=False, allow_infinity=False)),
    )
)


# ===================================================================
# Frame Interpolation — Property Tests
# ===================================================================


class TestInterpolateImageProperties:
    """Property tests for interpolate_image."""

    @given(images=_uint8_images, t=_interp_factor)
    @settings(max_examples=80)
    def test_output_shape_matches_input(self, images: tuple, t: float) -> None:
        img1, img2 = images
        result = interpolate_image(img1, img2, t)
        assert result.shape == img1.shape

    @given(images=_uint8_images, t=_interp_factor)
    @settings(max_examples=80)
    def test_output_dtype_is_uint8(self, images: tuple, t: float) -> None:
        img1, img2 = images
        result = interpolate_image(img1, img2, t)
        assert result.dtype == np.uint8

    @given(images=_uint8_images)
    @settings(max_examples=40, deadline=None)
    def test_t_zero_returns_first_image(self, images: tuple) -> None:
        img1, img2 = images
        result = interpolate_image(img1, img2, t=0.0)
        np.testing.assert_array_equal(result, img1)

    @given(images=_uint8_images)
    @settings(max_examples=40, deadline=None)
    def test_t_one_returns_second_image(self, images: tuple) -> None:
        img1, img2 = images
        result = interpolate_image(img1, img2, t=1.0)
        np.testing.assert_array_equal(result, img2)

    @given(
        shape_a=st.tuples(st.integers(1, 8), st.integers(1, 8), st.just(3)),
        shape_b=st.tuples(st.integers(1, 8), st.integers(1, 8), st.just(3)),
    )
    def test_shape_mismatch_raises_value_error(self, shape_a: tuple, shape_b: tuple) -> None:
        assume(shape_a != shape_b)
        img1 = np.zeros(shape_a, dtype=np.uint8)
        img2 = np.zeros(shape_b, dtype=np.uint8)
        with pytest.raises(ValueError):
            interpolate_image(img1, img2)

    @given(images=_uint8_images, t=_interp_factor)
    @settings(max_examples=60)
    def test_output_values_in_valid_range(self, images: tuple, t: float) -> None:
        img1, img2 = images
        result = interpolate_image(img1, img2, t)
        assert result.min() >= 0
        assert result.max() <= 255


class TestInterpolateFrameDataProperties:
    """Property tests for interpolate_frame_data."""

    @given(
        n=st.integers(min_value=2, max_value=30),
        cols=st.integers(min_value=1, max_value=6),
        t=_interp_factor,
    )
    @settings(max_examples=80)
    def test_output_shape_matches_row(self, n: int, cols: int, t: float) -> None:
        data = np.random.default_rng(42).standard_normal((n, cols))
        idx = np.random.default_rng(0).integers(0, n - 1)
        result = interpolate_frame_data(data, int(idx), t)
        assert result.shape == (cols,)

    @given(
        n=st.integers(min_value=2, max_value=20),
        t=_interp_factor,
    )
    @settings(max_examples=60)
    def test_integer_dtype_preserved(self, n: int, t: float) -> None:
        data = np.random.default_rng(42).integers(0, 100, size=(n, 3)).astype(np.int32)
        result = interpolate_frame_data(data, 0, t)
        assert result.dtype == np.int32

    @given(
        n=st.integers(min_value=2, max_value=20),
        t=_interp_factor,
    )
    @settings(max_examples=60)
    def test_float_dtype_returns_float(self, n: int, t: float) -> None:
        data = np.random.default_rng(42).standard_normal((n, 3)).astype(np.float64)
        result = interpolate_frame_data(data, 0, t)
        assert np.issubdtype(result.dtype, np.floating)

    @given(n=st.integers(min_value=2, max_value=20))
    @settings(max_examples=40)
    def test_negative_index_raises_index_error(self, n: int) -> None:
        data = np.zeros((n, 3))
        with pytest.raises(IndexError):
            interpolate_frame_data(data, -1)

    @given(n=st.integers(min_value=2, max_value=20))
    @settings(max_examples=40)
    def test_out_of_range_index_raises_index_error(self, n: int) -> None:
        data = np.zeros((n, 3))
        with pytest.raises(IndexError):
            interpolate_frame_data(data, n - 1)

    @given(
        n=st.integers(min_value=2, max_value=20),
        cols=st.integers(min_value=1, max_value=6),
    )
    @settings(max_examples=40)
    def test_t_zero_returns_first_frame(self, n: int, cols: int) -> None:
        data = np.random.default_rng(42).standard_normal((n, cols))
        result = interpolate_frame_data(data, 0, t=0.0)
        np.testing.assert_allclose(result, data[0], atol=1e-10)

    @given(
        n=st.integers(min_value=2, max_value=20),
        cols=st.integers(min_value=1, max_value=6),
    )
    @settings(max_examples=40)
    def test_t_one_returns_second_frame(self, n: int, cols: int) -> None:
        data = np.random.default_rng(42).standard_normal((n, cols))
        result = interpolate_frame_data(data, 0, t=1.0)
        np.testing.assert_allclose(result, data[1], atol=1e-10)


# ===================================================================
# Trajectory Analysis — Property Tests
# ===================================================================


class TestTrajectoryAnalyzerIntegrationProperties:
    """Property tests for TrajectoryAnalyzer.analyze end-to-end."""

    @given(
        n=st.integers(min_value=0, max_value=2),
        dims=st.integers(min_value=1, max_value=6),
    )
    @settings(max_examples=40)
    def test_short_trajectory_returns_safe_defaults(self, n: int, dims: int) -> None:
        positions = np.zeros((n, dims))
        timestamps = np.arange(n, dtype=np.float64)
        analyzer = TrajectoryAnalyzer()
        result = analyzer.analyze(positions, timestamps)
        assert result.smoothness == 1.0
        assert result.efficiency == 1.0
        assert result.jitter == 0.0
        assert result.hesitation_count == 0
        assert result.correction_count == 0
        assert result.overall_score == 3
        assert result.flags == []

    @given(data=_small_float_array)
    @settings(max_examples=60, deadline=None)
    def test_analyze_returns_valid_metric_types(self, data: tuple) -> None:
        n, positions = data
        timestamps = np.cumsum(np.full(n, 0.033))
        analyzer = TrajectoryAnalyzer()
        result = analyzer.analyze(positions, timestamps)

        assert isinstance(result.smoothness, float)
        assert isinstance(result.efficiency, float)
        assert isinstance(result.jitter, float)
        assert isinstance(result.hesitation_count, int)
        assert isinstance(result.correction_count, int)
        assert 0.0 <= result.smoothness <= 1.0
        assert 0.0 <= result.normalized_smoothness <= 1.0
        assert 0.0 <= result.efficiency <= 1.0
        assert 0.0 <= result.jitter <= 1.0
        assert result.overall_score in {1, 2, 3, 4, 5}

        expected_flags = []
        if result.smoothness < 0.5:
            expected_flags.append("jittery")
        if result.jitter > 0.3:
            expected_flags.append("high_frequency_noise")
        if result.hesitation_count > 2:
            expected_flags.append("hesitant")
        if result.correction_count > 5:
            expected_flags.append("excessive_corrections")
        assert result.flags == expected_flags

    @given(
        start=arrays(np.float64, (3,), elements=st.floats(-100, 100, allow_nan=False, allow_infinity=False)),
        end=arrays(np.float64, (3,), elements=st.floats(-100, 100, allow_nan=False, allow_infinity=False)),
        n=st.integers(min_value=4, max_value=20),
    )
    @settings(max_examples=60, deadline=None)
    def test_straight_line_trajectory_has_ideal_metrics(self, start: NDArray, end: NDArray, n: int) -> None:
        analyzer = TrajectoryAnalyzer()
        speed = np.linalg.norm(end - start) / (n - 1)
        assume(speed > analyzer.velocity_threshold * 2)
        positions = np.linspace(start, end, n)
        timestamps = np.arange(n, dtype=np.float64)

        result = analyzer.analyze(positions, timestamps)

        assert result.smoothness == pytest.approx(1.0)
        assert result.efficiency == pytest.approx(1.0)
        assert result.jitter == 0.0
        assert result.hesitation_count == 0
        assert result.correction_count == 0
        assert result.overall_score == 5
        assert result.flags == []

    def test_slow_straight_line_is_counted_as_one_hesitation(self) -> None:
        analyzer = TrajectoryAnalyzer()
        frame_count = analyzer.hesitation_min_frames + 2
        step = analyzer.velocity_threshold / 2
        positions = (np.arange(frame_count, dtype=np.float64) * step).reshape(-1, 1)
        timestamps = np.arange(frame_count, dtype=np.float64)

        result = analyzer.analyze(positions, timestamps)

        assert result.smoothness == pytest.approx(1.0)
        assert result.efficiency == pytest.approx(1.0)
        assert result.jitter == 0.0
        assert result.hesitation_count == 1
        assert result.correction_count == 0
        assert result.overall_score == 5
        assert result.flags == []

    def test_repeated_reversals_are_reported_through_analysis(self) -> None:
        positions = np.tile([0.0, 1.0], 20).reshape(-1, 1)
        timestamps = np.arange(len(positions), dtype=np.float64)

        result = TrajectoryAnalyzer().analyze(positions, timestamps)

        assert result.smoothness < 0.5
        assert result.correction_count > 5
        assert "jittery" in result.flags
        assert "excessive_corrections" in result.flags

    @given(data=_small_float_array)
    @settings(max_examples=60)
    def test_smoothness_and_efficiency_in_unit_interval(self, data: tuple) -> None:
        n, positions = data
        timestamps = np.cumsum(np.full(n, 0.033))
        analyzer = TrajectoryAnalyzer()
        result = analyzer.analyze(positions, timestamps)
        assert 0.0 <= result.smoothness <= 1.0
        assert 0.0 <= result.efficiency <= 1.0
        assert 0.0 <= result.jitter <= 1.0

    @given(data=_small_float_array)
    @settings(max_examples=60)
    def test_counts_are_non_negative(self, data: tuple) -> None:
        n, positions = data
        timestamps = np.cumsum(np.full(n, 0.033))
        analyzer = TrajectoryAnalyzer()
        result = analyzer.analyze(positions, timestamps)
        assert result.hesitation_count >= 0
        assert result.correction_count >= 0
