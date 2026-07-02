"""Synthetic LeRobot v3.0 dataset generator for the IL (LeRobot/ACT) e2e tests.

Produces a minimal, self-contained dataset that lerobot 0.5.1's ``LeRobotDataset``
loads directly (``codebase_version`` ``v3.0``), so the OSMO IL training and eval e2e
tests no longer depend on a HuggingFace token. Pure ``numpy``/``pyarrow``/``imageio``
— no ``lerobot`` import — so it runs on the test host (macOS/CI) where the
linux-x86_64-locked training stack cannot be installed.

The on-disk layout mirrors lerobot 0.5.1's own ``DatasetWriter`` output, verified
against a reference dataset that writer produced:

- ``meta/info.json``                              v3.0 feature/codec metadata
- ``meta/tasks.parquet``                          pandas-indexed (``task``) task table
- ``meta/episodes/chunk-000/file-000.parquet``    per-episode index records
- ``meta/stats.json``                             global per-feature normalization stats
- ``data/chunk-000/file-000.parquet``             frame records (state/action/indices)
- ``videos/<key>/chunk-000/file-000.mp4``         packed episode video

Only the episodes' non-``stats/`` columns are written: lerobot drops ``stats/``
columns on load (``load_episodes``) and reads normalization stats from
``meta/stats.json``.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tests.e2e._common import delete_blob_prefix, e2e_name, env_value, log_e2e, upload_blob_directory

# --- Embodiment spec (single source of truth) -------------------------------
# A self-contained ACT-compatible embodiment: one RGB camera ``observation.image``
# (3, 96, 96), a 2-D ``observation.state``, and a 2-D ``action``. The eval e2e mints
# its base policy from this dataset, so the dataset defines the embodiment and the
# policy matches it by construction. Frame count comfortably exceeds the ACT policy's
# default action chunk (100) so temporal action windows are not dominated by edge padding.

_FPS = 10
_NUM_FRAMES = 160
_HEIGHT = 96
_WIDTH = 96
_STATE_DIM = 2
_ACTION_DIM = 2

_VIDEO_KEY = "observation.image"
_TASK_TEXT = "push the T block to the target"
_ROBOT_TYPE = "synthetic-pusht"

_CHUNK = 0
_FILE = 0
_DATA_PATH_TEMPLATE = "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
_VIDEO_PATH_TEMPLATE = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"

# Order matters: scalar feature stats are emitted with a singleton shape, vector
# features with their per-dimension shape, and the video with per-channel shape.
_SCALAR_FEATURES: tuple[str, ...] = ("timestamp", "frame_index", "episode_index", "index", "task_index")


def _trajectory() -> tuple[np.ndarray, np.ndarray]:
    """Deterministic, non-degenerate state/action arrays (every dim has min < max).

    Non-degeneracy matters: lerobot normalizes observation/action features by
    ``(value - mean) / std`` (or ``(max - min)``), so a constant dimension would
    divide by zero and inject NaNs into training.
    """
    phases = np.linspace(0.0, 2.0 * np.pi, _NUM_FRAMES, dtype=np.float64)
    state = np.empty((_NUM_FRAMES, _STATE_DIM), dtype=np.float64)
    for d in range(_STATE_DIM):
        state[:, d] = np.sin(phases + d * 0.5) * (0.5 + 0.1 * d)
    action = np.cos(state)
    return state, action


def _frames() -> np.ndarray:
    """Deterministic RGB frames (uint8) with per-channel and temporal variation."""
    rng = np.random.default_rng(0)
    base = rng.integers(0, 256, size=(_NUM_FRAMES, _HEIGHT, _WIDTH, 3), dtype=np.uint8)
    return base


def _quantiles(values: np.ndarray, axis: int) -> dict[str, np.ndarray]:
    return {
        "q01": np.quantile(values, 0.01, axis=axis),
        "q10": np.quantile(values, 0.10, axis=axis),
        "q50": np.quantile(values, 0.50, axis=axis),
        "q90": np.quantile(values, 0.90, axis=axis),
        "q99": np.quantile(values, 0.99, axis=axis),
    }


def _vector_stats(values: np.ndarray) -> dict[str, list[float] | list[int]]:
    """Per-dimension stats for a (frames, dim) array; matches lerobot's layout."""
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.ndim == 1:
        matrix = matrix.reshape(-1, 1)
    quant = _quantiles(matrix, axis=0)
    return {
        "min": matrix.min(axis=0).tolist(),
        "max": matrix.max(axis=0).tolist(),
        "mean": matrix.mean(axis=0).tolist(),
        "std": matrix.std(axis=0).tolist(),
        "count": [matrix.shape[0]],
        "q01": quant["q01"].tolist(),
        "q10": quant["q10"].tolist(),
        "q50": quant["q50"].tolist(),
        "q90": quant["q90"].tolist(),
        "q99": quant["q99"].tolist(),
    }


def _image_stats(frames: np.ndarray) -> dict[str, object]:
    """Per-channel (3, 1, 1) stats over normalized [0, 1] pixels, as lerobot emits."""
    normalized = frames.astype(np.float64) / 255.0
    # Collapse frames, height, width -> per-channel; keep (3, 1, 1) shape.
    per_channel = normalized.reshape(-1, 3)
    quant = _quantiles(per_channel, axis=0)

    def _shape_311(vector: np.ndarray) -> list[list[list[float]]]:
        return [[[float(v)]] for v in vector]

    return {
        "min": _shape_311(per_channel.min(axis=0)),
        "max": _shape_311(per_channel.max(axis=0)),
        "mean": _shape_311(per_channel.mean(axis=0)),
        "std": _shape_311(per_channel.std(axis=0)),
        "count": [int(frames.shape[0])],
        "q01": _shape_311(quant["q01"]),
        "q10": _shape_311(quant["q10"]),
        "q50": _shape_311(quant["q50"]),
        "q90": _shape_311(quant["q90"]),
        "q99": _shape_311(quant["q99"]),
    }


def _info_json() -> dict[str, object]:
    return {
        "codebase_version": "v3.0",
        "robot_type": _ROBOT_TYPE,
        "total_episodes": 1,
        "total_frames": _NUM_FRAMES,
        "total_tasks": 1,
        "chunks_size": 1000,
        "data_files_size_in_mb": 100,
        "video_files_size_in_mb": 200,
        "fps": _FPS,
        "splits": {"train": "0:1"},
        "data_path": _DATA_PATH_TEMPLATE,
        "video_path": _VIDEO_PATH_TEMPLATE,
        "features": {
            _VIDEO_KEY: {
                "dtype": "video",
                "shape": [_HEIGHT, _WIDTH, 3],
                "names": ["height", "width", "channel"],
                "info": {
                    "video.height": _HEIGHT,
                    "video.width": _WIDTH,
                    "video.codec": "h264",
                    "video.pix_fmt": "yuv420p",
                    "video.is_depth_map": False,
                    "video.fps": _FPS,
                    "video.channels": 3,
                    "has_audio": False,
                },
            },
            "observation.state": {
                "dtype": "float32",
                "shape": [_STATE_DIM],
                "names": [f"motor_{i}" for i in range(_STATE_DIM)],
            },
            "action": {
                "dtype": "float32",
                "shape": [_ACTION_DIM],
                "names": [f"motor_{i}" for i in range(_ACTION_DIM)],
            },
            "timestamp": {"dtype": "float32", "shape": [1], "names": None},
            "frame_index": {"dtype": "int64", "shape": [1], "names": None},
            "episode_index": {"dtype": "int64", "shape": [1], "names": None},
            "index": {"dtype": "int64", "shape": [1], "names": None},
            "task_index": {"dtype": "int64", "shape": [1], "names": None},
        },
    }


def _write_info(root: Path) -> None:
    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "info.json").write_text(json.dumps(_info_json(), indent=4))


def _write_tasks(root: Path) -> None:
    # lerobot reads tasks via pandas and uses the ``task`` string as the index, so
    # the parquet must carry pandas index metadata (``index_columns: ["task"]``);
    # pyarrow alone would otherwise yield a default RangeIndex and break task lookup.
    pandas_metadata = {
        "index_columns": ["task"],
        "column_indexes": [
            {
                "name": None,
                "field_name": None,
                "pandas_type": "unicode",
                "numpy_type": "str",
                "metadata": {"encoding": "UTF-8"},
            }
        ],
        "columns": [
            {
                "name": "task_index",
                "field_name": "task_index",
                "pandas_type": "int64",
                "numpy_type": "int64",
                "metadata": None,
            },
            {"name": "task", "field_name": "task", "pandas_type": "object", "numpy_type": "str", "metadata": None},
        ],
        "creator": {"library": "pyarrow", "version": pa.__version__},
        "pandas_version": "3.0.0",
    }
    schema = pa.schema(
        [pa.field("task_index", pa.int64()), pa.field("task", pa.large_string())],
        metadata={b"pandas": json.dumps(pandas_metadata).encode("utf-8")},
    )
    table = pa.table(
        {"task_index": pa.array([0], pa.int64()), "task": pa.array([_TASK_TEXT], pa.large_string())},
        schema=schema,
    )
    pq.write_table(table, root / "meta" / "tasks.parquet")


def _write_data(root: Path, state: np.ndarray, action: np.ndarray) -> None:
    timestamps = (np.arange(_NUM_FRAMES, dtype=np.float64) / _FPS).astype(np.float32)
    table = pa.table(
        {
            "observation.state": pa.array(state.tolist(), type=pa.list_(pa.float32(), _STATE_DIM)),
            "action": pa.array(action.tolist(), type=pa.list_(pa.float32(), _ACTION_DIM)),
            "timestamp": pa.array(timestamps, type=pa.float32()),
            "frame_index": pa.array(range(_NUM_FRAMES), type=pa.int64()),
            "episode_index": pa.array([0] * _NUM_FRAMES, type=pa.int64()),
            "index": pa.array(range(_NUM_FRAMES), type=pa.int64()),
            "task_index": pa.array([0] * _NUM_FRAMES, type=pa.int64()),
        }
    )
    path = root / _DATA_PATH_TEMPLATE.format(chunk_index=_CHUNK, file_index=_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _write_episodes(root: Path) -> None:
    # Non-``stats/`` columns only: lerobot's load_episodes drops stats columns and
    # reads normalization stats from meta/stats.json instead.
    duration_s = _NUM_FRAMES / _FPS
    table = pa.table(
        {
            "episode_index": pa.array([0], pa.int64()),
            "tasks": pa.array([[_TASK_TEXT]], pa.list_(pa.string())),
            "length": pa.array([_NUM_FRAMES], pa.int64()),
            "data/chunk_index": pa.array([_CHUNK], pa.int64()),
            "data/file_index": pa.array([_FILE], pa.int64()),
            "dataset_from_index": pa.array([0], pa.int64()),
            "dataset_to_index": pa.array([_NUM_FRAMES], pa.int64()),
            f"videos/{_VIDEO_KEY}/chunk_index": pa.array([_CHUNK], pa.int64()),
            f"videos/{_VIDEO_KEY}/file_index": pa.array([_FILE], pa.int64()),
            f"videos/{_VIDEO_KEY}/from_timestamp": pa.array([0.0], pa.float64()),
            f"videos/{_VIDEO_KEY}/to_timestamp": pa.array([duration_s], pa.float64()),
            "meta/episodes/chunk_index": pa.array([_CHUNK], pa.int64()),
            "meta/episodes/file_index": pa.array([_FILE], pa.int64()),
        }
    )
    path = root / "meta" / "episodes" / f"chunk-{_CHUNK:03d}" / f"file-{_FILE:03d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _write_stats(root: Path, state: np.ndarray, action: np.ndarray, frames: np.ndarray) -> None:
    timestamps = np.arange(_NUM_FRAMES, dtype=np.float64) / _FPS
    scalar_values: dict[str, np.ndarray] = {
        "timestamp": timestamps,
        "frame_index": np.arange(_NUM_FRAMES, dtype=np.float64),
        "episode_index": np.zeros(_NUM_FRAMES, dtype=np.float64),
        "index": np.arange(_NUM_FRAMES, dtype=np.float64),
        "task_index": np.zeros(_NUM_FRAMES, dtype=np.float64),
    }
    stats: dict[str, object] = {_VIDEO_KEY: _image_stats(frames)}
    stats["observation.state"] = _vector_stats(state)
    stats["action"] = _vector_stats(action)
    for name in _SCALAR_FEATURES:
        stats[name] = _vector_stats(scalar_values[name].reshape(-1, 1))
    (root / "meta" / "stats.json").write_text(json.dumps(stats, indent=4))


def _write_video(root: Path, frames: np.ndarray) -> None:
    import imageio.v2 as imageio

    path = root / _VIDEO_PATH_TEMPLATE.format(video_key=_VIDEO_KEY, chunk_index=_CHUNK, file_index=_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio.get_writer(
        str(path),
        format="FFMPEG",
        mode="I",
        fps=_FPS,
        codec="libx264",
        pixelformat="yuv420p",
        macro_block_size=None,
    )
    try:
        for frame in frames:
            writer.append_data(frame)
    finally:
        writer.close()


def build_synthetic_dataset(root: Path) -> Path:
    """Write a complete lerobot-v3.0-loadable dataset under ``root`` and return it."""
    state, action = _trajectory()
    frames = _frames()
    _write_info(root)
    _write_tasks(root)
    _write_data(root, state, action)
    _write_episodes(root)
    _write_stats(root, state, action, frames)
    _write_video(root, frames)
    return root


def validate_synthetic_dataset(root: Path) -> None:
    """Cheap structural self-check; raises AssertionError on the first mismatch.

    Catches generation regressions on the test host before the slow GPU job, but
    does not replace lerobot's loader (which only runs inside the training container).
    """
    expected = [
        "meta/info.json",
        "meta/tasks.parquet",
        "meta/stats.json",
        f"meta/episodes/chunk-{_CHUNK:03d}/file-{_FILE:03d}.parquet",
        _DATA_PATH_TEMPLATE.format(chunk_index=_CHUNK, file_index=_FILE),
        _VIDEO_PATH_TEMPLATE.format(video_key=_VIDEO_KEY, chunk_index=_CHUNK, file_index=_FILE),
    ]
    missing = [rel for rel in expected if not (root / rel).is_file()]
    if missing:
        raise AssertionError(f"Synthetic dataset is missing files: {missing}")

    info = json.loads((root / "meta" / "info.json").read_text())
    if info["codebase_version"] != "v3.0":
        raise AssertionError(f"info.json codebase_version is {info['codebase_version']!r}, expected 'v3.0'")
    if info["total_frames"] != _NUM_FRAMES:
        raise AssertionError(f"info.json total_frames is {info['total_frames']}, expected {_NUM_FRAMES}")

    data_table = pq.read_table(root / _DATA_PATH_TEMPLATE.format(chunk_index=_CHUNK, file_index=_FILE))
    if data_table.num_rows != _NUM_FRAMES:
        raise AssertionError(f"data parquet has {data_table.num_rows} rows, expected {_NUM_FRAMES}")
    required_columns = {
        "observation.state",
        "action",
        "timestamp",
        "frame_index",
        "episode_index",
        "index",
        "task_index",
    }
    if not required_columns.issubset(set(data_table.column_names)):
        raise AssertionError(f"data parquet missing columns: {sorted(required_columns - set(data_table.column_names))}")

    stats = json.loads((root / "meta" / "stats.json").read_text())
    for feature in ("observation.state", "action", _VIDEO_KEY):
        if feature not in stats:
            raise AssertionError(f"stats.json missing feature {feature!r}")
        std = np.asarray(stats[feature]["std"], dtype=np.float64)
        if not np.all(std > 0):
            raise AssertionError(f"stats.json {feature!r} has a zero-std dimension (would divide by zero)")


# --- Blob staging -----------------------------------------------------------
# The OSMO data container ("osmo") always exists and the OSMO workflow identity
# holds account-scoped Storage Blob Data Contributor, so a training/eval pod can
# read a dataset staged here via its workload identity (no SAS needed). Mirrors
# the VLA dataset staging in tests/e2e/_osmo.py.

_IL_DATASET_CONTAINER_ENV = "E2E_IL_DATASET_CONTAINER"
_DEFAULT_IL_DATASET_CONTAINER = "osmo"


@dataclass(frozen=True)
class StagedDataset:
    storage_account: str
    container: str
    prefix: str

    @property
    def blob_url(self) -> str:
        return f"https://{self.storage_account}.blob.core.windows.net/{self.container}/{self.prefix}"


def stage_synthetic_lerobot_dataset(
    request: pytest.FixtureRequest, repo_root: Path, storage_account: str
) -> StagedDataset:
    """Generate the synthetic dataset, upload it to blob, and register teardown cleanup.

    Returns the staged location so a test can drive the OSMO training (``--blob-url``)
    or eval (``--from-blob-dataset``) submission without a HuggingFace token.
    """
    container = env_value(_IL_DATASET_CONTAINER_ENV, _DEFAULT_IL_DATASET_CONTAINER) or _DEFAULT_IL_DATASET_CONTAINER

    work_dir = Path(tempfile.mkdtemp(prefix="il-e2e-dataset-"))
    request.addfinalizer(lambda: shutil.rmtree(work_dir, ignore_errors=True))

    dataset_dir = work_dir / "dataset"
    log_e2e("Generating synthetic LeRobot v3.0 dataset")
    build_synthetic_dataset(dataset_dir)
    validate_synthetic_dataset(dataset_dir)

    prefix = f"e2e-il-datasets/{e2e_name('lerobot')}"
    log_e2e(f"Uploading synthetic LeRobot dataset to {storage_account}/{container}/{prefix}")
    # Register cleanup before the upload so a partial upload is still torn down.
    request.addfinalizer(
        lambda: delete_blob_prefix(
            repo_root,
            storage_account,
            container,
            prefix,
            description="staged synthetic LeRobot dataset",
        )
    )
    upload_blob_directory(
        repo_root,
        storage_account,
        container,
        prefix,
        dataset_dir,
        description="synthetic LeRobot dataset",
    )

    return StagedDataset(storage_account=storage_account, container=container, prefix=prefix)
