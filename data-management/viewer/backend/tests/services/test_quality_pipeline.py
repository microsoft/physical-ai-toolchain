"""Behavior tests for deterministic dataset quality validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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
