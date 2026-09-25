"""Behavior tests for immutable release trajectory statistics."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.api.models.releases import canonical_json_bytes
from src.api.release.statistics import generate_release_statistics


def _write_staged_package(package_root: Path) -> None:
    meta_root = package_root / "meta"
    data_root = package_root / "data" / "chunk-000"
    meta_root.mkdir(parents=True)
    data_root.mkdir(parents=True)
    (meta_root / "info.json").write_text(
        '{"codebase_version":"v3.0","robot_type":"arm","total_episodes":2,'
        '"total_frames":8,"total_tasks":1,"total_chunks":1,"chunks_size":1000,'
        '"fps":10.0,"splits":{"train":"0:2"},'
        '"data_path":"data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",'
        '"video_path":"videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",'
        '"features":{"observation.state":{"dtype":"float64","shape":[2]},'
        '"action":{"dtype":"float64","shape":[2]}}}',
        encoding="utf-8",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": [0, 0, 0, 0, 1, 1, 1, 1],
                "frame_index": [0, 1, 2, 3, 0, 1, 2, 3],
                "timestamp": [0.0, 0.1, 0.2, 0.3, 0.0, 0.1, 0.2, 0.3],
                "task_index": [0] * 8,
                "observation.state": [
                    [0.0, 0.0],
                    [1.0, 1.0],
                    [2.0, 2.0],
                    [3.0, 3.0],
                    [0.0, 0.0],
                    [0.0, 0.0],
                    [0.0, 0.0],
                    [0.0, 0.0],
                ],
                "action": [[0.0, 0.0]] * 8,
            }
        ),
        data_root / "file-000.parquet",
    )


def test_given_staged_v3_package_when_statistics_generated_then_released_episodes_are_analyzed(
    tmp_path: Path,
) -> None:
    # Arrange
    _write_staged_package(tmp_path)
    feature_schema = {
        "action": {"dtype": "float64", "shape": [2]},
        "observation.state": {"dtype": "float64", "shape": [2]},
    }

    # Act
    statistics = generate_release_statistics(tmp_path, feature_schema, episode_count=2)

    # Assert
    assert statistics.schema_version == "1.0.0"
    assert statistics.statistics_profile.smoothness_mode == "log-scaled"
    assert set(statistics.tool_versions) == {"numpy", "scipy"}
    assert [episode.release_episode_index for episode in statistics.episodes] == [0, 1]
    assert statistics.episodes[0].frame_count == 4
    assert statistics.episodes[0].duration_seconds == 0.3
    assert canonical_json_bytes(statistics) == canonical_json_bytes(
        generate_release_statistics(tmp_path, feature_schema, episode_count=2)
    )


def test_given_different_feature_key_order_when_statistics_generated_then_schema_digest_is_stable(
    tmp_path: Path,
) -> None:
    # Arrange
    _write_staged_package(tmp_path)
    first_schema = {
        "observation.state": {"shape": [2], "dtype": "float64"},
        "action": {"shape": [2], "dtype": "float64"},
    }
    second_schema = {
        "action": {"dtype": "float64", "shape": [2]},
        "observation.state": {"dtype": "float64", "shape": [2]},
    }

    # Act
    first = generate_release_statistics(tmp_path, first_schema, episode_count=2)
    second = generate_release_statistics(tmp_path, second_schema, episode_count=2)

    # Assert
    assert first.feature_schema_sha256 == second.feature_schema_sha256
