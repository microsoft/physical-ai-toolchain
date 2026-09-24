"""Behavior tests for deterministic dataset quality validation."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.api.models.reviews import QualityOutcome
from src.api.quality.adapters import HDF5SourceAdapter, LeRobotSourceAdapter
from src.api.quality.profiles import CalibrationRequirement, FeatureRequirement, QualityProfile
from src.api.quality.service import QualityService, required_checks_pass


@pytest.fixture
def profile() -> QualityProfile:
    return QualityProfile(
        profile_id="robot-manipulation",
        version="1.0.0",
        fps=10.0,
        timestamp_tolerance_seconds=0.001,
        required_features=(
            FeatureRequirement(name="observation.state", dtype="float32", shape=(2,)),
            FeatureRequirement(name="action", dtype="float32", shape=(2,)),
        ),
        optional_features=(FeatureRequirement(name="observation.velocity", dtype="float32", shape=(2,)),),
        required_metadata_files=("meta/info.json", "meta/tasks.parquet"),
        calibration=CalibrationRequirement(
            relative_path="meta/calibration.json",
            schema_version="1.0.0",
            required_sensors=("wrist-camera",),
        ),
        require_task_label=True,
    )


def _write_lerobot_dataset(
    root: Path,
    *,
    timestamps: list[float] | None = None,
    frame_indices: list[int] | None = None,
    include_action: bool = True,
    include_task_label: bool = True,
    calibration_sensors: list[str] | None = None,
) -> Path:
    timestamps = timestamps or [0.0, 0.1, 0.2]
    frame_indices = frame_indices or [0, 1, 2]
    metadata = root / "meta"
    metadata.mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "fps": 10,
        "total_episodes": 1,
        "total_frames": 3,
        "features": {
            "observation.state": {"dtype": "float32", "shape": [2]},
            "action": {"dtype": "float32", "shape": [2]},
        },
    }
    (metadata / "info.json").write_text(json.dumps(info), encoding="utf-8")
    pq.write_table(
        pa.table(
            {
                "task_index": pa.array([0], type=pa.int64()),
                "task": pa.array(["pick object" if include_task_label else ""], type=pa.string()),
            }
        ),
        metadata / "tasks.parquet",
    )
    (metadata / "calibration.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "sensors": calibration_sensors if calibration_sensors is not None else ["wrist-camera"],
            }
        ),
        encoding="utf-8",
    )
    columns: dict[str, pa.Array] = {
        "observation.state": pa.array([[0.0, 1.0], [0.1, 1.1], [0.2, 1.2]], type=pa.list_(pa.float32(), 2)),
        "timestamp": pa.array(timestamps, type=pa.float32()),
        "frame_index": pa.array(frame_indices, type=pa.int64()),
        "episode_index": pa.array([0, 0, 0], type=pa.int64()),
        "task_index": pa.array([0, 0, 0], type=pa.int64()),
    }
    if include_action:
        columns["action"] = pa.array([[1.0, 0.0], [1.1, 0.1], [1.2, 0.2]], type=pa.list_(pa.float32(), 2))
    data_path = root / "data" / "chunk-000" / "file-000.parquet"
    data_path.parent.mkdir(parents=True)
    pq.write_table(pa.table(columns), data_path)
    return root


def _outcomes(report) -> dict[str, QualityOutcome]:
    return {check.check_id: check.outcome for check in report.episode_checks}


def _check(report, check_id: str):
    return next(item for item in report.episode_checks if item.check_id == check_id)


async def test_given_valid_lerobot_source_when_checked_then_required_gate_passes(
    tmp_path: Path,
    profile: QualityProfile,
) -> None:
    # Arrange
    root = _write_lerobot_dataset(tmp_path / "dataset")
    service = QualityService()

    # Act
    report = await service.run(
        adapter=LeRobotSourceAdapter(),
        dataset_root=root,
        dataset_id="sample-dataset",
        episode_index=0,
        profile=profile,
        run_id="quality-01",
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    # Assert
    assert required_checks_pass(report) is True
    assert set(_outcomes(report).values()) == {QualityOutcome.PASS}


async def test_given_valid_source_when_checked_then_release_grade_measurements_are_recorded(
    tmp_path: Path,
    profile: QualityProfile,
) -> None:
    # Arrange
    root = _write_lerobot_dataset(tmp_path / "dataset")

    # Act
    report = await QualityService().run(
        adapter=LeRobotSourceAdapter(),
        dataset_root=root,
        dataset_id="sample-dataset",
        episode_index=0,
        profile=profile,
        run_id="quality-01",
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    # Assert
    timestamp_check = _check(report, "timestamps.valid")
    assert timestamp_check.measurements == {
        "sample_count": 3,
        "start_seconds": 0.0,
        "end_seconds": pytest.approx(0.2),
        "observed_delta_min_seconds": pytest.approx(0.1),
        "observed_delta_max_seconds": pytest.approx(0.1),
        "finite": True,
        "monotonic": True,
        "streams": [],
    }
    assert timestamp_check.thresholds == {
        "expected_delta_seconds": 0.1,
        "alignment_tolerance_seconds": 0.001,
    }

    stream_check = _check(report, "streams.complete")
    assert stream_check.measurements["required"]["observation.state"] == {
        "observed": True,
        "expected_dtype": "float32",
        "observed_dtype": "float32",
        "expected_shape": [2],
        "observed_shape": [2],
        "row_count": 3,
        "null_count": 0,
        "non_finite_count": 0,
    }
    assert stream_check.measurements["optional"]["observation.velocity"]["disposition"] == "missing"

    calibration_check = _check(report, "calibration.valid")
    assert calibration_check.measurements["relative_path"] == "meta/calibration.json"
    assert calibration_check.measurements["size_bytes"] > 0
    assert len(calibration_check.measurements["sha256"]) == 64
    assert calibration_check.measurements["required_sensors"] == ["wrist-camera"]
    assert calibration_check.measurements["observed_sensors"] == ["wrist-camera"]

    metadata_check = _check(report, "metadata.consistent")
    assert metadata_check.measurements["required_artifacts"] == ["meta/info.json", "meta/tasks.parquet"]
    assert metadata_check.measurements["observed_frame_count"] == 3
    assert metadata_check.measurements["declared_frame_count"] == 3

    label_check = _check(report, "labels.valid")
    assert label_check.measurements == {"label_count": 1, "nonempty_label_count": 1}


async def test_given_multiple_episode_camera_videos_when_checked_then_exact_segments_are_read(
    tmp_path: Path,
    profile: QualityProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    root = _write_lerobot_dataset(tmp_path / "dataset")
    info_path = root / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    for camera in ("observation.images.wrist", "observation.images.overhead"):
        info["features"][camera] = {"dtype": "video", "shape": [3, 8, 8]}
    info_path.write_text(json.dumps(info), encoding="utf-8")

    episode_table = pa.table(
        {
            "episode_index": pa.array([0, 1], type=pa.int64()),
            "length": pa.array([3, 3], type=pa.int64()),
            "videos/observation.images.wrist/chunk_index": pa.array([0, 1], type=pa.int64()),
            "videos/observation.images.wrist/file_index": pa.array([0, 4], type=pa.int64()),
            "videos/observation.images.wrist/from_timestamp": pa.array([0.0, 1.0], type=pa.float64()),
            "videos/observation.images.wrist/to_timestamp": pa.array([0.3, 1.3], type=pa.float64()),
            "videos/observation.images.overhead/chunk_index": pa.array([0, 2], type=pa.int64()),
            "videos/observation.images.overhead/file_index": pa.array([0, 5], type=pa.int64()),
            "videos/observation.images.overhead/from_timestamp": pa.array([0.0, 2.0], type=pa.float64()),
            "videos/observation.images.overhead/to_timestamp": pa.array([0.3, 2.3], type=pa.float64()),
        }
    )
    episodes_path = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    episodes_path.parent.mkdir(parents=True)
    pq.write_table(episode_table, episodes_path)
    expected_paths = {
        root / "videos" / "observation.images.wrist" / "chunk-001" / "file-004.mp4",
        root / "videos" / "observation.images.overhead" / "chunk-002" / "file-005.mp4",
    }
    for path in expected_paths:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"video")
    opened_paths: list[Path] = []

    class _FakeContainer(AbstractContextManager[Any]):
        streams = SimpleNamespace(video=[SimpleNamespace(time_base=0.1)])

        def __init__(self, path: str) -> None:
            opened_paths.append(Path(path))

        def __exit__(self, *args: object) -> None:
            return None

        def seek(self, *args: object, **kwargs: object) -> None:
            return None

        def decode(self, *args: object, **kwargs: object):
            first_pts = 20 if "overhead" in opened_paths[-1].as_posix() else 10
            return iter(SimpleNamespace(pts=pts, time_base=0.1) for pts in range(first_pts, first_pts + 4))

    monkeypatch.setattr("av.open", _FakeContainer)
    video_profile = profile.model_copy(
        update={
            "required_features": (
                *profile.required_features,
                FeatureRequirement(name="observation.images.wrist", dtype="video", shape=(3, 8, 8)),
                FeatureRequirement(name="observation.images.overhead", dtype="video", shape=(3, 8, 8)),
            )
        }
    )

    # Act
    snapshot = LeRobotSourceAdapter().inspect(root, "sample-dataset", 1, video_profile)

    # Assert
    assert set(opened_paths) == expected_paths
    assert snapshot.readback_errors == ()
    assert {(item.feature_name, item.frame_count) for item in snapshot.video_observations} == {
        ("observation.images.wrist", 3),
        ("observation.images.overhead", 3),
    }

    missing_path = root / "videos" / "observation.images.overhead" / "chunk-002" / "file-005.mp4"
    missing_path.unlink()
    missing_snapshot = LeRobotSourceAdapter().inspect(root, "sample-dataset", 1, video_profile)
    assert "video:observation.images.overhead:missing" in missing_snapshot.readback_errors


async def test_given_cross_camera_duration_skew_when_checked_then_timestamp_gate_fails(
    tmp_path: Path,
    profile: QualityProfile,
) -> None:
    # Arrange
    snapshot = LeRobotSourceAdapter().inspect(
        _write_lerobot_dataset(tmp_path / "dataset"),
        "sample-dataset",
        0,
        profile,
    )
    snapshot = replace(
        snapshot,
        video_observations=(
            SimpleNamespace(
                feature_name="observation.images.wrist",
                relative_path="videos/wrist.mp4",
                frame_count=3,
                duration_seconds=0.3,
                window_start_seconds=1.0,
                window_end_seconds=1.3,
            ),
            SimpleNamespace(
                feature_name="observation.images.overhead",
                relative_path="videos/overhead.mp4",
                frame_count=3,
                duration_seconds=0.45,
                window_start_seconds=2.0,
                window_end_seconds=2.45,
            ),
        ),
    )

    class _SnapshotAdapter:
        def inspect(self, *args: object, **kwargs: object):
            return snapshot

    # Act
    report = await QualityService().run(
        adapter=_SnapshotAdapter(),
        dataset_root=tmp_path,
        dataset_id="sample-dataset",
        episode_index=0,
        profile=profile,
        run_id="quality-01",
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    # Assert
    timestamp_check = _check(report, "timestamps.valid")
    assert timestamp_check.outcome == QualityOutcome.FAIL
    assert "stream-duration-out-of-tolerance" in timestamp_check.reason_codes
    assert timestamp_check.measurements["streams"][0]["aligned"] is True
    assert timestamp_check.measurements["streams"][1]["aligned"] is False


async def test_given_one_frame_camera_duration_skew_when_checked_then_timestamp_gate_passes(
    tmp_path: Path,
    profile: QualityProfile,
) -> None:
    # Arrange
    snapshot = LeRobotSourceAdapter().inspect(
        _write_lerobot_dataset(tmp_path / "dataset"),
        "sample-dataset",
        0,
        profile,
    )
    snapshot = replace(
        snapshot,
        video_observations=(
            SimpleNamespace(
                feature_name="observation.images.wrist",
                relative_path="videos/wrist.mp4",
                frame_count=3,
                duration_seconds=0.3,
                window_start_seconds=0.0,
                window_end_seconds=0.3,
            ),
            SimpleNamespace(
                feature_name="observation.images.overhead",
                relative_path="videos/overhead.mp4",
                frame_count=2,
                duration_seconds=0.4,
                window_start_seconds=0.0,
                window_end_seconds=0.4,
            ),
        ),
    )

    class _SnapshotAdapter:
        def inspect(self, *args: object, **kwargs: object):
            return snapshot

    # Act
    report = await QualityService().run(
        adapter=_SnapshotAdapter(),
        dataset_root=tmp_path,
        dataset_id="sample-dataset",
        episode_index=0,
        profile=profile,
        run_id="quality-01",
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    # Assert
    timestamp_check = _check(report, "timestamps.valid")
    assert timestamp_check.outcome == QualityOutcome.PASS
    assert all(stream["aligned"] for stream in timestamp_check.measurements["streams"])


@pytest.mark.parametrize(
    ("dataset_options", "failed_check", "reason_code"),
    [
        ({"include_action": False}, "streams.complete", "required-feature-missing"),
        ({"timestamps": [0.0, 0.2, 0.1]}, "timestamps.valid", "timestamps-not-monotonic"),
        ({"frame_indices": [0, 2, 3]}, "frames.contiguous", "frame-index-gap"),
        ({"calibration_sensors": []}, "calibration.valid", "calibration-sensor-missing"),
        ({"include_task_label": False}, "labels.valid", "task-label-missing"),
    ],
)
async def test_given_invalid_lerobot_source_when_checked_then_failure_remains_visible(
    tmp_path: Path,
    profile: QualityProfile,
    dataset_options: dict[str, object],
    failed_check: str,
    reason_code: str,
) -> None:
    # Arrange
    root = _write_lerobot_dataset(tmp_path / "dataset", **dataset_options)

    # Act
    report = await QualityService().run(
        adapter=LeRobotSourceAdapter(),
        dataset_root=root,
        dataset_id="sample-dataset",
        episode_index=0,
        profile=profile,
        run_id="quality-01",
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    # Assert
    check = next(item for item in report.episode_checks if item.check_id == failed_check)
    assert check.outcome == QualityOutcome.FAIL
    assert reason_code in check.reason_codes
    assert required_checks_pass(report) is False


async def test_given_nan_and_wrong_shape_when_checked_then_stream_failure_reports_both_reasons(
    tmp_path: Path,
    profile: QualityProfile,
) -> None:
    # Arrange
    root = _write_lerobot_dataset(tmp_path / "dataset")
    data_path = root / "data" / "chunk-000" / "file-000.parquet"
    table = pq.read_table(data_path).drop(["observation.state"])
    table = table.append_column(
        "observation.state",
        pa.array([[0.0], [float("nan")], [0.2]], type=pa.list_(pa.float32(), 1)),
    )
    pq.write_table(table, data_path)

    # Act
    report = await QualityService().run(
        adapter=LeRobotSourceAdapter(),
        dataset_root=root,
        dataset_id="sample-dataset",
        episode_index=0,
        profile=profile,
        run_id="quality-01",
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    # Assert
    stream_check = next(item for item in report.episode_checks if item.check_id == "streams.complete")
    assert set(stream_check.reason_codes) >= {"feature-shape-mismatch", "feature-non-finite"}


async def test_given_required_metadata_missing_when_checked_then_gate_fails(
    tmp_path: Path,
    profile: QualityProfile,
) -> None:
    # Arrange
    root = _write_lerobot_dataset(tmp_path / "dataset")
    (root / "meta" / "tasks.parquet").unlink()

    # Act
    report = await QualityService().run(
        adapter=LeRobotSourceAdapter(),
        dataset_root=root,
        dataset_id="sample-dataset",
        episode_index=0,
        profile=profile,
        run_id="quality-01",
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    # Assert
    metadata_check = next(item for item in report.episode_checks if item.check_id == "metadata.consistent")
    assert metadata_check.reason_codes == ("metadata-file-missing",)
    assert required_checks_pass(report) is False


async def test_given_hdf5_without_timestamps_when_checked_then_no_viewer_fallback_is_accepted(
    tmp_path: Path,
    profile: QualityProfile,
) -> None:
    # Arrange
    h5py = pytest.importorskip("h5py")
    root = tmp_path / "hdf5-dataset"
    root.mkdir()
    with h5py.File(root / "episode_000000.hdf5", "w") as dataset:
        dataset.create_dataset("observation/state", data=np.ones((3, 2), dtype=np.float32))
        dataset.create_dataset("action", data=np.ones((3, 2), dtype=np.float32))
        dataset.create_dataset("frame_index", data=np.arange(3, dtype=np.int64))
        dataset.attrs["fps"] = 10.0
        dataset.attrs["task"] = "pick object"

    # Act
    report = await QualityService().run(
        adapter=HDF5SourceAdapter(),
        dataset_root=root,
        dataset_id="sample-dataset",
        episode_index=0,
        profile=profile.model_copy(update={"required_metadata_files": (), "calibration": None}),
        run_id="quality-01",
        actor_id="localuser",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    # Assert
    assert _outcomes(report)["timestamps.valid"] == QualityOutcome.FAIL
    assert required_checks_pass(report) is False
