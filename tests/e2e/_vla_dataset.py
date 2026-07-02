"""
Synthetic GR00T (NVIDIA Isaac-GR00T N1.5) fine-tuning dataset generator.

Produces a minimal, self-contained LeRobot v2.0 dataset plus the GR00T-specific
``meta/modality.json`` so the OSMO VLA fine-tuning e2e test no longer depends on a
manually pre-staged dataset. A single embodiment spec drives both the on-disk
dataset and the injected ``--data-config`` Python fragment, so the two cannot drift.

Schema is pinned to Isaac-GR00T ``796ca8d`` (the N1.5 ref used by
``training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh``):
GR00T ships its own reader (``gr00t/data/dataset.py``), so the ``lerobot`` pip
package is not required to load this dataset.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

# --- Embodiment spec (single source of truth) -------------------------------
# A single 6-DoF arm (5 joints + 1 gripper) with one RGB camera and one language
# instruction. Mirrors the built-in ``So100DataConfig`` shape but is injected
# under a test-owned key so it is decoupled from the upstream config registry.

DATA_CONFIG_KEY = "e2e_synth"
EMBODIMENT_TAG = "new_embodiment"

# (modality subkey, dimension) — contiguous slices of observation.state / action.
_STATE_FIELDS: tuple[tuple[str, int], ...] = (("single_arm", 5), ("gripper", 1))
_ACTION_FIELDS: tuple[tuple[str, int], ...] = (("single_arm", 5), ("gripper", 1))
_STATE_DIM = sum(dim for _, dim in _STATE_FIELDS)
_ACTION_DIM = sum(dim for _, dim in _ACTION_FIELDS)

_VIDEO_KEY = "webcam"
_VIDEO_ORIGINAL_KEY = "observation.images.webcam"
_ANNOTATION_KEY = "human.task_description"
_TASK_TEXT = "pick up the cube and place it on the plate"

_NUM_FRAMES = 32
_FPS = 10.0
_FRAME_SIZE = 224
_ACTION_HORIZON = 16


def _field_ranges(fields: tuple[tuple[str, int], ...]) -> dict[str, dict[str, int]]:
    ranges: dict[str, dict[str, int]] = {}
    cursor = 0
    for name, dim in fields:
        ranges[name] = {"start": cursor, "end": cursor + dim}
        cursor += dim
    return ranges


def _modality_json() -> dict[str, object]:
    return {
        "state": _field_ranges(_STATE_FIELDS),
        "action": _field_ranges(_ACTION_FIELDS),
        "video": {_VIDEO_KEY: {"original_key": _VIDEO_ORIGINAL_KEY}},
        "annotation": {_ANNOTATION_KEY: {"original_key": "task_index"}},
    }


def _info_json() -> dict[str, object]:
    return {
        "codebase_version": "v2.0",
        "robot_type": DATA_CONFIG_KEY,
        "total_episodes": 1,
        "total_frames": _NUM_FRAMES,
        "total_tasks": 1,
        "total_videos": 1,
        "total_chunks": 1,
        "chunks_size": 1000,
        "fps": _FPS,
        "splits": {"train": "0:1"},
        "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
        "features": {
            _VIDEO_ORIGINAL_KEY: {
                "dtype": "video",
                "shape": [_FRAME_SIZE, _FRAME_SIZE, 3],
                "names": ["height", "width", "channel"],
                "video_info": {
                    "video.fps": _FPS,
                    "video.codec": "h264",
                    "video.pix_fmt": "yuv420p",
                    "video.is_depth_map": False,
                    "has_audio": False,
                },
            },
            "observation.state": {
                "dtype": "float64",
                "shape": [_STATE_DIM],
                "names": [f"motor_{i}" for i in range(_STATE_DIM)],
            },
            "action": {
                "dtype": "float64",
                "shape": [_ACTION_DIM],
                "names": [f"motor_{i}" for i in range(_ACTION_DIM)],
            },
            "timestamp": {"dtype": "float64", "shape": [1]},
            "task_index": {"dtype": "int64", "shape": [1]},
            "episode_index": {"dtype": "int64", "shape": [1]},
            "index": {"dtype": "int64", "shape": [1]},
            "next.reward": {"dtype": "float64", "shape": [1]},
            "next.done": {"dtype": "bool", "shape": [1]},
        },
    }


def _trajectory_arrays() -> tuple[np.ndarray, np.ndarray]:
    """Deterministic, non-degenerate state/action (every dim has min < max).

    Non-degeneracy matters: GR00T applies ``min_max`` normalization to each
    state/action sub-field, which divides by ``(max - min)``.
    """
    phases = np.linspace(0.0, 1.0, _NUM_FRAMES, dtype=np.float64)
    state = np.empty((_NUM_FRAMES, _STATE_DIM), dtype=np.float64)
    for d in range(_STATE_DIM):
        state[:, d] = 0.5 * np.sin(2.0 * math.pi * (phases + d / max(_STATE_DIM, 1)))
    # Distinct-but-correlated action signal so it is not identical to state.
    action = np.cos(state)
    return state, action


def _stats_entry(values: np.ndarray) -> dict[str, list[float]]:
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.ndim == 1:
        matrix = matrix.reshape(-1, 1)
    return {
        "min": matrix.min(axis=0).tolist(),
        "max": matrix.max(axis=0).tolist(),
        "mean": matrix.mean(axis=0).tolist(),
        "std": matrix.std(axis=0).tolist(),
        "q01": np.quantile(matrix, 0.01, axis=0).tolist(),
        "q99": np.quantile(matrix, 0.99, axis=0).tolist(),
    }


def _write_video(path: Path) -> None:
    import imageio.v2 as imageio

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
        gradient = np.linspace(0, 255, _FRAME_SIZE, dtype=np.int64)
        row = np.broadcast_to(gradient, (_FRAME_SIZE, _FRAME_SIZE))
        for frame_index in range(_NUM_FRAMES):
            shift = int(255 * frame_index / _NUM_FRAMES)
            frame = np.empty((_FRAME_SIZE, _FRAME_SIZE, 3), dtype=np.uint8)
            frame[..., 0] = ((row + shift) % 256).astype(np.uint8)
            frame[..., 1] = ((row.T + shift) % 256).astype(np.uint8)
            frame[..., 2] = np.uint8(shift)
            writer.append_data(frame)
    finally:
        writer.close()


def _write_parquet(path: Path, state: np.ndarray, action: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamps = (np.arange(_NUM_FRAMES, dtype=np.float64) / _FPS).tolist()
    done = [False] * (_NUM_FRAMES - 1) + [True]
    reward = [0.0] * (_NUM_FRAMES - 1) + [1.0]

    table = pa.table(
        {
            "observation.state": pa.array(state.tolist(), type=pa.list_(pa.float64(), _STATE_DIM)),
            "action": pa.array(action.tolist(), type=pa.list_(pa.float64(), _ACTION_DIM)),
            "timestamp": pa.array(timestamps, type=pa.float64()),
            "task_index": pa.array([0] * _NUM_FRAMES, type=pa.int64()),
            "episode_index": pa.array([0] * _NUM_FRAMES, type=pa.int64()),
            "index": pa.array(list(range(_NUM_FRAMES)), type=pa.int64()),
            "next.reward": pa.array(reward, type=pa.float64()),
            "next.done": pa.array(done, type=pa.bool_()),
        }
    )
    pq.write_table(table, str(path))


def _write_stats(path: Path, state: np.ndarray, action: np.ndarray) -> None:
    stats: dict[str, dict[str, list[float]]] = {
        "observation.state": _stats_entry(state),
        "action": _stats_entry(action),
    }
    timestamps = np.arange(_NUM_FRAMES, dtype=np.float64) / _FPS
    scalar_values: dict[str, np.ndarray] = {
        "timestamp": timestamps,
        "task_index": np.zeros(_NUM_FRAMES, dtype=np.float64),
        "episode_index": np.zeros(_NUM_FRAMES, dtype=np.float64),
        "index": np.arange(_NUM_FRAMES, dtype=np.float64),
        "next.reward": np.array([0.0] * (_NUM_FRAMES - 1) + [1.0], dtype=np.float64),
        "next.done": np.array([0.0] * (_NUM_FRAMES - 1) + [1.0], dtype=np.float64),
    }
    for name, values in scalar_values.items():
        stats[name] = _stats_entry(values)
    path.write_text(json.dumps(stats, indent=2))


def render_data_config_py() -> str:
    """Python fragment appended to ``gr00t/experiment/data_config.py`` at runtime.

    ``So100DataConfig`` and ``DATA_CONFIG_MAP`` are provided by the module this
    fragment is concatenated onto, so they are intentionally free names here.
    """
    state_keys = [f"state.{name}" for name, _ in _STATE_FIELDS]
    action_keys = [f"action.{name}" for name, _ in _ACTION_FIELDS]
    state_list = ", ".join(f'"{key}"' for key in state_keys)
    action_list = ", ".join(f'"{key}"' for key in action_keys)
    return (
        "\n\n"
        f"class E2ESyntheticDataConfig(So100DataConfig):\n"
        f'    video_keys = ["video.{_VIDEO_KEY}"]\n'
        f"    state_keys = [{state_list}]\n"
        f"    action_keys = [{action_list}]\n"
        f'    language_keys = ["annotation.{_ANNOTATION_KEY}"]\n'
        f"    observation_indices = [0]\n"
        f"    action_indices = list(range({_ACTION_HORIZON}))\n"
        "\n\n"
        f'DATA_CONFIG_MAP["{DATA_CONFIG_KEY}"] = E2ESyntheticDataConfig()\n'
    )


def write_data_config_file(path: Path) -> Path:
    path.write_text(render_data_config_py())
    return path


def build_synthetic_dataset(root: Path) -> Path:
    """Write a complete GR00T-loadable dataset under ``root`` and return it."""
    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)

    (meta / "modality.json").write_text(json.dumps(_modality_json(), indent=2))
    (meta / "info.json").write_text(json.dumps(_info_json(), indent=2))
    (meta / "episodes.jsonl").write_text(
        json.dumps({"episode_index": 0, "tasks": [_TASK_TEXT], "length": _NUM_FRAMES}) + "\n"
    )
    (meta / "tasks.jsonl").write_text(json.dumps({"task_index": 0, "task": _TASK_TEXT}) + "\n")

    state, action = _trajectory_arrays()
    _write_parquet(root / "data" / "chunk-000" / "episode_000000.parquet", state, action)
    _write_stats(meta / "stats.json", state, action)
    _write_video(root / "videos" / "chunk-000" / _VIDEO_ORIGINAL_KEY / "episode_000000.mp4")
    return root


def validate_synthetic_dataset(root: Path) -> None:
    """Cheap structural self-check; raises AssertionError on the first mismatch.

    Catches generation regressions locally before the slow GPU job, but does not
    replace the GR00T loader (which only runs inside the fine-tuning container).
    """
    expected = [
        "meta/modality.json",
        "meta/info.json",
        "meta/episodes.jsonl",
        "meta/tasks.jsonl",
        "meta/stats.json",
        "data/chunk-000/episode_000000.parquet",
        f"videos/chunk-000/{_VIDEO_ORIGINAL_KEY}/episode_000000.mp4",
    ]
    missing = [rel for rel in expected if not (root / rel).is_file()]
    if missing:
        raise AssertionError(f"Synthetic dataset is missing files: {missing}")

    modality = json.loads((root / "meta" / "modality.json").read_text())
    table = pq.read_table(root / "data" / "chunk-000" / "episode_000000.parquet")
    columns = set(table.column_names)
    required_columns = {"observation.state", "action", "timestamp", "task_index", "index"}
    if not required_columns.issubset(columns):
        raise AssertionError(f"Parquet missing columns: {sorted(required_columns - columns)}")
    if table.num_rows != _NUM_FRAMES:
        raise AssertionError(f"Parquet has {table.num_rows} rows, expected {_NUM_FRAMES}")

    state_dim = max(field["end"] for field in modality["state"].values())
    action_dim = max(field["end"] for field in modality["action"].values())
    state_widths = {len(row) for row in table.column("observation.state").to_pylist()}
    if state_widths != {state_dim}:
        raise AssertionError(f"observation.state width {state_widths} != modality dim {state_dim}")
    action_widths = {len(row) for row in table.column("action").to_pylist()}
    if action_widths != {action_dim}:
        raise AssertionError(f"action width {action_widths} != modality dim {action_dim}")

    import imageio.v2 as imageio

    reader = imageio.get_reader(
        str(root / "videos" / "chunk-000" / _VIDEO_ORIGINAL_KEY / "episode_000000.mp4"),
        format="FFMPEG",
    )
    try:
        first_frame = reader.get_data(0)
    finally:
        reader.close()
    if first_frame.shape[:2] != (_FRAME_SIZE, _FRAME_SIZE):
        raise AssertionError(f"Decoded frame shape {first_frame.shape[:2]} != ({_FRAME_SIZE}, {_FRAME_SIZE})")
