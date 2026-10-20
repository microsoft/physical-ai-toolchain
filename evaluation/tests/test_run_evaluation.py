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


def test_camera_keys_include_every_recorded_camera() -> None:
    features = {
        "observation.state": {"dtype": "float32"},
        "observation.images.scene": {"dtype": "video"},
        "observation.images.tool": {"dtype": "video"},
        "action": {"dtype": "float32"},
    }

    assert _MOD._camera_keys(features) == ["observation.images.scene", "observation.images.tool"]


def test_replay_observation_carries_the_state_and_every_camera() -> None:
    frames = {
        "observation.images.scene": [np.full((2, 3, 3), 255, dtype=np.uint8)],
        "observation.images.tool": [np.zeros((2, 3, 3), dtype=np.uint8)],
    }

    obs = _MOD._replay_observation(np.arange(7, dtype=np.float32), frames, 0)

    assert sorted(obs) == ["observation.images.scene", "observation.images.tool", "observation.state"]
    assert tuple(obs["observation.images.scene"].shape) == (3, 2, 3)
    assert float(obs["observation.images.scene"].max()) == 1.0
    assert float(obs["observation.images.tool"].max()) == 0.0
    assert obs["observation.state"].tolist() == list(range(7))


def _two_camera_episode(tmp_path: Path, *, with_tool_video: bool = True) -> dict:
    """Lay out an episode's scene and tool videos the way a LeRobot v3 dataset shares them, and return its record."""
    record: dict = {}
    for camera, chunk in (("observation.images.scene", 1), ("observation.images.tool", 2)):
        record |= {
            f"videos/{camera}/chunk_index": chunk,
            f"videos/{camera}/file_index": 0,
            f"videos/{camera}/from_timestamp": 0.1,
            f"videos/{camera}/to_timestamp": 0.3,
        }
        if camera == "observation.images.scene" or with_tool_video:
            video = tmp_path / "videos" / camera / f"chunk-{chunk:03d}" / "file-000.mp4"
            video.parent.mkdir(parents=True)
            video.touch()
    return record


def _decoded_by_chunk(video_file: str) -> list[np.ndarray]:
    """Stand in for the decoder: six frames whose value is the chunk index, then the frame index."""
    chunk = int(Path(video_file).parent.name.removeprefix("chunk-"))
    return [np.full((1, 1, 3), chunk * 10 + index, dtype=np.uint8) for index in range(6)]


def test_load_episode_frames_slices_every_camera_to_the_episode(tmp_path: Path) -> None:
    record = _two_camera_episode(tmp_path)
    cameras = ["observation.images.scene", "observation.images.tool"]

    frames = _MOD._load_episode_frames(str(tmp_path), cameras, 3, record, 10, _decoded_by_chunk)

    assert frames is not None
    assert {camera: [int(frame[0, 0, 0]) for frame in frames[camera]] for camera in cameras} == {
        "observation.images.scene": [11, 12],
        "observation.images.tool": [21, 22],
    }


def test_load_episode_frames_skips_an_episode_missing_a_camera_video(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _two_camera_episode(tmp_path, with_tool_video=False)
    cameras = ["observation.images.scene", "observation.images.tool"]

    assert _MOD._load_episode_frames(str(tmp_path), cameras, 3, record, 10, _decoded_by_chunk) is None
    assert "No video for episode 3 (observation.images.tool)" in capsys.readouterr().out


def test_load_episode_frames_skips_an_episode_outside_its_shared_video(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _two_camera_episode(tmp_path) | {
        "videos/observation.images.tool/from_timestamp": 1.0,
        "videos/observation.images.tool/to_timestamp": 1.2,
    }
    cameras = ["observation.images.scene", "observation.images.tool"]

    assert _MOD._load_episode_frames(str(tmp_path), cameras, 3, record, 10, _decoded_by_chunk) is None
    assert "No video frames for episode 3 (observation.images.tool)" in capsys.readouterr().out


def test_decode_video_returns_rgb_frames(tmp_path: Path) -> None:
    av = pytest.importorskip("av")
    video_file = tmp_path / "camera.mp4"
    with av.open(str(video_file), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 16, 16, "yuv420p"
        for value in (0, 128, 255):
            frame = av.VideoFrame.from_ndarray(np.full((16, 16, 3), value, dtype=np.uint8), format="rgb24")
            container.mux(stream.encode(frame))
        container.mux(stream.encode())

    frames = _MOD._decode_video(str(video_file))

    assert len(frames) == 3
    assert all(frame.shape == (16, 16, 3) and frame.dtype == np.uint8 for frame in frames)
    assert int(frames[0].mean()) < int(frames[-1].mean())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("  ", None),
        (
            '{"observation.images.external": "observation.images.camera1"}',
            {"observation.images.external": "observation.images.camera1"},
        ),
    ],
)
def test_parse_rename_map(raw: str, expected: dict | None) -> None:
    assert _MOD._parse_rename_map(raw) == expected


@pytest.mark.parametrize("raw", ['["observation.images.external"]', '{"observation.images.external": 1}'])
def test_parse_rename_map_rejects_non_string_mappings(raw: str) -> None:
    with pytest.raises(ValueError, match="RENAME_MAP must be a JSON object"):
        _MOD._parse_rename_map(raw)


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


def test_seed_replay_reproduces_policy_noise() -> None:
    import torch

    _MOD._seed_replay(0)
    first = (torch.randn(4), np.random.rand(4))
    _MOD._seed_replay(0)
    second = (torch.randn(4), np.random.rand(4))

    assert torch.equal(first[0], second[0])
    assert np.array_equal(first[1], second[1])


def test_aggregate_metrics_averages_evaluated_episodes() -> None:
    episodes = [
        {"mse": 1.0, "mae": 0.5, "avg_inference_ms": 10.0, "throughput_hz": 100.0},
        {"mse": 3.0, "mae": 1.5, "avg_inference_ms": 30.0, "throughput_hz": 50.0},
    ]

    assert _MOD._aggregate_metrics(episodes) == {
        "mse": 2.0,
        "mae": 1.0,
        "avg_inference_ms": 20.0,
        "throughput_hz": 75.0,
    }


def test_aggregate_metrics_rejects_an_evaluation_without_episodes() -> None:
    with pytest.raises(ValueError, match="No episode was evaluated"):
        _MOD._aggregate_metrics([])


class _RecordingMlflow:
    def __init__(self) -> None:
        self.metrics: dict[str, float] = {}
        self.artifacts: list[str] = []

    def log_metrics(self, metrics: dict[str, float]) -> None:
        self.metrics.update(metrics)

    def log_artifact(self, path: str) -> None:
        self.artifacts.append(Path(path).name)


def test_log_evaluation_results_records_the_evaluated_count_and_schema_artifacts(tmp_path: Path) -> None:
    for name in ("eval_results.json", "metrics.json", "failure_cases.jsonl"):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    recorder = _RecordingMlflow()

    _MOD._log_evaluation_results(
        recorder,
        tmp_path,
        aggregate={"mse": 2.0, "mae": 1.0, "avg_inference_ms": 20.0, "throughput_hz": 75.0},
        episodes_evaluated=2,
    )

    assert recorder.metrics == {
        "aggregate_mse": 2.0,
        "aggregate_mae": 1.0,
        "aggregate_avg_inference_ms": 20.0,
        "aggregate_throughput_hz": 75.0,
        "episodes_evaluated": 2,
    }
    assert recorder.artifacts == ["eval_results.json", "metrics.json", "failure_cases.jsonl"]
