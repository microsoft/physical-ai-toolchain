"""Unit tests for the Azure ML and OSMO LeRobot replay evaluator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "sil" / "scripts" / "run_evaluation.py"
_SPEC = importlib.util.spec_from_file_location("run_evaluation", _SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Unable to load replay evaluator from {_SCRIPT}")
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_load_task_metadata_from_jsonl(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "tasks.jsonl").write_text(
        '{"task_index": 4, "task": "pick up the block"}\n',
        encoding="utf-8",
    )
    (meta / "episodes.jsonl").write_text(
        '{"episode_index": 7, "task_index": 4, "data/chunk_index": 1, "data/file_index": 2}\n',
        encoding="utf-8",
    )

    task_descriptions, episode_tasks, episode_records = _MOD._load_task_metadata(str(tmp_path))

    assert task_descriptions == {4: "pick up the block"}
    assert episode_tasks == {7: "pick up the block"}
    assert episode_records[7]["data/file_index"] == 2


def test_load_task_metadata_from_v3_parquet(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    episodes = meta / "episodes" / "chunk-000"
    episodes.mkdir(parents=True)
    pq.write_table(
        pa.table({"task_index": [2], "task": ["place the block"]}),
        meta / "tasks.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": [3],
                "tasks": [["place the block"]],
                "data/chunk_index": [0],
                "data/file_index": [1],
            }
        ),
        episodes / "file-000.parquet",
    )

    task_descriptions, episode_tasks, episode_records = _MOD._load_task_metadata(str(tmp_path))

    assert task_descriptions == {2: "place the block"}
    assert episode_tasks == {3: "place the block"}
    assert episode_records[3]["data/file_index"] == 1


@pytest.mark.parametrize(
    ("step", "episode_index", "data", "task_descriptions", "episode_tasks", "expected"),
    [
        (0, 1, {}, {2: "indexed task"}, {1: "episode task"}, "episode task"),
        (0, 1, {"task_index": [2]}, {2: "indexed task"}, {}, "indexed task"),
        (0, 1, {}, {2: "only task"}, {}, "only task"),
        (0, 1, {}, {2: "first", 3: "second"}, {}, ""),
        (1, 1, {"task_index": [2]}, {2: "indexed task"}, {1: "episode task"}, "episode task"),
    ],
)
def test_resolve_frame_task(
    step: int,
    episode_index: int,
    data: dict[str, list],
    task_descriptions: dict[int, str],
    episode_tasks: dict[int, str],
    expected: str,
) -> None:
    assert _MOD._resolve_frame_task(step, episode_index, data, task_descriptions, episode_tasks) == expected


def test_resolve_frame_task_tracks_transition_within_episode() -> None:
    data = {"task_index": [2, 2, 3, 3]}
    task_descriptions = {2: "pick up the block", 3: "place the block"}

    tasks = [_MOD._resolve_frame_task(step, 1, data, task_descriptions, {}) for step in range(len(data["task_index"]))]

    assert tasks == [
        "pick up the block",
        "pick up the block",
        "place the block",
        "place the block",
    ]


def test_task_from_episode_record_rejects_ambiguous_fallback() -> None:
    record = {
        "tasks": ["pick up the block", "place the block"],
        "task_index": 2,
    }

    assert _MOD._task_from_episode_record(record, {2: "pick up the block"}) == ""


def test_shared_episode_data_and_video_are_bounded(tmp_path: Path) -> None:
    data_file = tmp_path / "data" / "chunk-001" / "file-002.parquet"
    video_file = tmp_path / "videos" / "observation.images.cam" / "chunk-003" / "file-004.mp4"
    data_file.parent.mkdir(parents=True)
    video_file.parent.mkdir(parents=True)
    data_file.touch()
    video_file.touch()
    episode_record = {
        "data/chunk_index": 1,
        "data/file_index": 2,
        "videos/observation.images.cam/chunk_index": 3,
        "videos/observation.images.cam/file_index": 4,
        "videos/observation.images.cam/from_timestamp": 0.2,
        "videos/observation.images.cam/to_timestamp": 0.5,
    }
    table = pa.table(
        {
            "episode_index": [1, 2, 1],
            "timestamp": [0.0, 0.0, 0.1],
        }
    )

    filtered = _MOD._filter_episode_table(table, 1, is_shared_file=True)
    frames = [np.full((1, 1, 3), index, dtype=np.uint8) for index in range(6)]
    sliced = _MOD._slice_episode_frames(
        frames,
        episode_record,
        "observation.images.cam",
        10,
    )

    assert _MOD._find_data_file(str(tmp_path), 1, episode_record) == str(data_file)
    assert _MOD._find_video_file(str(tmp_path), "observation.images.cam", 1, episode_record) == str(video_file)
    assert filtered["episode_index"].to_pylist() == [1, 1]
    assert [int(frame[0, 0, 0]) for frame in sliced] == [2, 3, 4]


def test_write_vla_schema_v1_emits_strict_contract(tmp_path: Path) -> None:
    _MOD._write_vla_schema_v1(
        output_dir=tmp_path,
        aggregate={
            "mse": 0.25,
            "mae": 0.5,
            "avg_inference_ms": 10.0,
            "throughput_hz": 100.0,
        },
        per_episode=[
            {
                "episode": 8,
                "mse": 0.25,
                "rollout_error": "processor failed",
            }
        ],
        dataset_repo_id="owner/dataset",
        policy_repo_id="owner/policy",
    )

    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    failure_cases = [
        json.loads(line) for line in (tmp_path / "failure_cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert metrics == {
        "evaluation_schema_version": 1,
        "aggregate_verdict": "pass",
        "baseline_model_version": "none",
        "metrics": [
            {
                "name": "action_accuracy_l2",
                "value": 0.25,
                "absolute_threshold": None,
                "absolute_verdict": "pass",
                "baseline_value": None,
                "regression_pct": 0.0,
                "regression_verdict": "skipped",
            },
            {
                "name": "action_accuracy_l1",
                "value": 0.5,
                "absolute_threshold": None,
                "absolute_verdict": "pass",
                "baseline_value": None,
                "regression_pct": 0.0,
                "regression_verdict": "skipped",
            },
            {
                "name": "inference_latency_mean_ms",
                "value": 10.0,
                "absolute_threshold": None,
                "absolute_verdict": "pass",
                "baseline_value": None,
                "regression_pct": 0.0,
                "regression_verdict": "skipped",
            },
            {
                "name": "throughput_hz",
                "value": 100.0,
                "absolute_threshold": None,
                "absolute_verdict": "pass",
                "baseline_value": None,
                "regression_pct": 0.0,
                "regression_verdict": "skipped",
            },
        ],
    }
    assert failure_cases == [
        {
            "evaluation_schema_version": 1,
            "episode_id": "8",
            "dataset_id": "owner/dataset",
            "dataset_version": "unknown",
            "domain_category": None,
            "model_version": "owner/policy",
            "artifact_refs": [],
            "failure_mode": "rollout_error",
            "metric_values": {"episode": 8, "mse": 0.25},
            "metric_thresholds_violated": [],
        }
    ]


def test_write_vla_schema_v1_rejects_non_finite_metrics(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        _MOD._write_vla_schema_v1(
            output_dir=tmp_path,
            aggregate={"mse": float("nan")},
            per_episode=[],
            dataset_repo_id="owner/dataset",
            policy_repo_id="owner/policy",
        )
