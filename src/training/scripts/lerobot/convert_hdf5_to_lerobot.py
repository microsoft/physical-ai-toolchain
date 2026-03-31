"""Convert OSMO hexagon HDF5 episodes to LeRobot v3.0 dataset format.

Reads HDF5 episode files containing bimanual robot demonstrations and
writes them as a LeRobot v3.0 dataset with parquet data, MP4 videos,
and normalization statistics.

Usage:
    python -m training.scripts.lerobot.convert_hdf5_to_lerobot \
        --input-dir datasets/hexagon_episodes/.../2026_01_13_18_04_51 \
        --output-dir datasets/hexagon_lerobot \
        --fps 30 \
        --robot-type hexagarm \
        --task "Hexagon bimanual manipulation"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import h5py
import imageio
import numpy as np
import pandas as pd
from tqdm import tqdm


def discover_episodes(input_dir: Path) -> list[Path]:
    """Find and return sorted HDF5 episode files in the input directory."""
    pattern = re.compile(r"^episode_(\d+)\.hdf5$")
    episodes = []
    for p in input_dir.iterdir():
        if pattern.match(p.name):
            episodes.append(p)
    episodes.sort(key=lambda p: int(pattern.match(p.name).group(1)))
    return episodes


def read_episode(path: Path) -> dict[str, np.ndarray]:
    """Read a single HDF5 episode into numpy arrays.

    Returns dict with keys: action, observation_state, images.
    """
    with h5py.File(path, "r") as f:
        action = f["action"][:]
        qpos = f["observations/qpos"][:]
        images = f["observations/images/il-camera"][:]
    return {"action": action, "observation_state": qpos, "images": images}


class StatsAccumulator:
    """Welford-style running statistics for normalization."""

    def __init__(self) -> None:
        self._state_sum: np.ndarray | None = None
        self._state_sq: np.ndarray | None = None
        self._action_sum: np.ndarray | None = None
        self._action_sq: np.ndarray | None = None
        self._state_min: np.ndarray | None = None
        self._state_max: np.ndarray | None = None
        self._action_min: np.ndarray | None = None
        self._action_max: np.ndarray | None = None
        self._count = 0

    def update_batch(self, states: np.ndarray, actions: np.ndarray) -> None:
        """Update with a batch of frames (N, D) arrays."""
        if self._state_sum is None:
            d_s = states.shape[1]
            d_a = actions.shape[1]
            self._state_sum = np.zeros(d_s, dtype=np.float64)
            self._state_sq = np.zeros(d_s, dtype=np.float64)
            self._action_sum = np.zeros(d_a, dtype=np.float64)
            self._action_sq = np.zeros(d_a, dtype=np.float64)
            self._state_min = np.full(d_s, np.inf)
            self._state_max = np.full(d_s, -np.inf)
            self._action_min = np.full(d_a, np.inf)
            self._action_max = np.full(d_a, -np.inf)

        states_f = states.astype(np.float64)
        actions_f = actions.astype(np.float64)

        self._state_sum += states_f.sum(axis=0)
        self._state_sq += (states_f**2).sum(axis=0)
        self._action_sum += actions_f.sum(axis=0)
        self._action_sq += (actions_f**2).sum(axis=0)
        self._count += states.shape[0]

        self._state_min = np.minimum(self._state_min, states_f.min(axis=0))
        self._state_max = np.maximum(self._state_max, states_f.max(axis=0))
        self._action_min = np.minimum(self._action_min, actions_f.min(axis=0))
        self._action_max = np.maximum(self._action_max, actions_f.max(axis=0))

    def compute(self) -> dict:
        """Return final statistics dict for stats.json."""
        if self._count == 0:
            return {}

        s_mean = self._state_sum / self._count
        s_std = np.sqrt(np.maximum(self._state_sq / self._count - s_mean**2, 1e-8))
        a_mean = self._action_sum / self._count
        a_std = np.sqrt(np.maximum(self._action_sq / self._count - a_mean**2, 1e-8))

        return {
            "observation.state": {
                "mean": s_mean.tolist(),
                "std": s_std.tolist(),
                "min": self._state_min.tolist(),
                "max": self._state_max.tolist(),
            },
            "action": {
                "mean": a_mean.tolist(),
                "std": a_std.tolist(),
                "min": self._action_min.tolist(),
                "max": self._action_max.tolist(),
            },
        }


def write_episode_parquet(
    output_dir: Path,
    episode_index: int,
    states: np.ndarray,
    actions: np.ndarray,
    fps: int,
    global_frame_offset: int,
) -> int:
    """Write per-episode parquet data file.

    Returns the number of frames written.
    """
    n_frames = states.shape[0]
    chunk_dir = output_dir / "data" / f"chunk-{episode_index:03d}"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    timestamps = [i / fps for i in range(n_frames)]
    rows = []
    for i in range(n_frames):
        rows.append(
            {
                "timestamp": timestamps[i],
                "frame_index": i,
                "episode_index": episode_index,
                "index": global_frame_offset + i,
                "task_index": 0,
                "observation.state": states[i].tolist(),
                "action": actions[i].tolist(),
            }
        )

    df = pd.DataFrame(rows)
    column_order = [
        "timestamp",
        "frame_index",
        "episode_index",
        "index",
        "task_index",
        "observation.state",
        "action",
    ]
    df = df[column_order]
    df.to_parquet(chunk_dir / f"file-{episode_index:03d}.parquet", index=False)
    return n_frames


def write_episode_video(
    output_dir: Path,
    episode_index: int,
    images: np.ndarray,
    fps: int,
    video_key: str = "il-camera",
) -> None:
    """Encode episode images as an MP4 video file."""
    video_dir = output_dir / f"videos/observation.images.{video_key}" / f"chunk-{episode_index:03d}"
    video_dir.mkdir(parents=True, exist_ok=True)
    video_path = video_dir / f"file-{episode_index:03d}.mp4"

    writer = imageio.get_writer(
        video_path,
        fps=fps,
        codec="libx264",
        pixelformat="yuv420p",
        macro_block_size=1,
    )
    for frame in images:
        writer.append_data(frame)
    writer.close()


def write_metadata(
    output_dir: Path,
    *,
    episode_lengths: list[int],
    stats: dict,
    fps: int,
    robot_type: str,
    task_description: str,
    state_dim: int,
    action_dim: int,
    image_shape: tuple[int, int, int],
    video_key: str = "il-camera",
) -> None:
    """Write all LeRobot v3.0 metadata files."""
    meta_dir = output_dir / "meta"
    meta_dir.mkdir(parents=True, exist_ok=True)

    total_frames = sum(episode_lengths)
    total_episodes = len(episode_lengths)

    # State/action joint names based on the hexagarm bimanual robot config
    state_names = [
        "right_ee_x",
        "right_ee_y",
        "right_ee_z",
        "right_ee_qx",
        "right_ee_qy",
        "right_ee_qz",
        "right_ee_qw",
        "right_gripper_pos",
        "left_ee_x",
        "left_ee_y",
        "left_ee_z",
        "left_ee_qx",
        "left_ee_qy",
        "left_ee_qz",
        "left_ee_qw",
        "left_gripper_pos",
    ]
    action_names = [
        "right_target_x",
        "right_target_y",
        "right_target_z",
        "right_target_qx",
        "right_target_qy",
        "right_target_qz",
        "right_target_qw",
        "right_gripper_cmd",
        "left_target_x",
        "left_target_y",
        "left_target_z",
        "left_target_qx",
        "left_target_qy",
        "left_target_qz",
        "left_target_qw",
        "left_gripper_cmd",
    ]

    # Truncate names lists to actual dimensions
    state_names = state_names[:state_dim]
    action_names = action_names[:action_dim]

    features = {
        "timestamp": {"dtype": "float64", "shape": [1]},
        "frame_index": {"dtype": "int64", "shape": [1]},
        "episode_index": {"dtype": "int64", "shape": [1]},
        "index": {"dtype": "int64", "shape": [1]},
        "task_index": {"dtype": "int64", "shape": [1]},
        "observation.state": {
            "dtype": "float32",
            "shape": [state_dim],
            "names": state_names,
        },
        "action": {
            "dtype": "float32",
            "shape": [action_dim],
            "names": action_names,
        },
        f"observation.images.{video_key}": {
            "dtype": "video",
            "shape": list(image_shape),
            "names": ["height", "width", "channels"],
            "info": {
                "video.fps": fps,
                "video.codec": "h264",
                "video.pix_fmt": "yuv420p",
            },
        },
    }

    # info.json
    info = {
        "codebase_version": "v3.0",
        "robot_type": robot_type,
        "total_episodes": total_episodes,
        "total_frames": total_frames,
        "total_tasks": 1,
        "total_chunks": total_episodes,
        "chunks_size": 1000,
        "fps": fps,
        "splits": {"train": f"0:{total_episodes}"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": features,
    }
    with open(meta_dir / "info.json", "w") as f:
        json.dump(info, f, indent=2)

    # stats.json
    with open(meta_dir / "stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    # tasks.parquet
    tasks_df = pd.DataFrame([{"task_index": 0, "task": task_description}])
    tasks_df.to_parquet(meta_dir / "tasks.parquet", index=False)

    # episodes parquet
    episodes_dir = meta_dir / "episodes" / "chunk-000"
    episodes_dir.mkdir(parents=True, exist_ok=True)

    cumulative_frames = 0
    cumulative_duration = 0.0
    episodes_data = []
    for ep_idx, length in enumerate(episode_lengths):
        ep_duration = length / fps
        episodes_data.append(
            {
                "episode_index": ep_idx,
                "task_index": 0,
                "length": length,
                "dataset_from_index": cumulative_frames,
                "dataset_to_index": cumulative_frames + length,
                "data/chunk_index": ep_idx,
                "data/file_index": ep_idx,
                f"videos/observation.images.{video_key}/chunk_index": ep_idx,
                f"videos/observation.images.{video_key}/file_index": ep_idx,
                f"videos/observation.images.{video_key}/from_timestamp": cumulative_duration,
                f"videos/observation.images.{video_key}/to_timestamp": cumulative_duration + ep_duration,
            }
        )
        cumulative_frames += length
        cumulative_duration += ep_duration

    episodes_df = pd.DataFrame(episodes_data)
    episodes_df.to_parquet(episodes_dir / "file-000.parquet", index=False)


def convert(
    input_dir: Path,
    output_dir: Path,
    *,
    fps: int = 30,
    robot_type: str = "hexagarm",
    task_description: str = "Hexagon bimanual manipulation",
) -> None:
    """Convert all HDF5 episodes to LeRobot v3.0 format."""
    episode_files = discover_episodes(input_dir)
    if not episode_files:
        print(f"[ERROR] No episode_*.hdf5 files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(episode_files)} episodes in {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    stats_acc = StatsAccumulator()
    episode_lengths: list[int] = []
    global_frame_offset = 0
    state_dim = 0
    action_dim = 0
    image_shape = (0, 0, 0)

    for ep_idx, ep_path in enumerate(tqdm(episode_files, desc="Converting episodes")):
        ep_data = read_episode(ep_path)
        states = ep_data["observation_state"]
        actions = ep_data["action"]
        images = ep_data["images"]

        if state_dim == 0:
            state_dim = states.shape[1]
            action_dim = actions.shape[1]
            image_shape = (images.shape[1], images.shape[2], images.shape[3])

        stats_acc.update_batch(states, actions)

        n_frames = write_episode_parquet(
            output_dir,
            ep_idx,
            states,
            actions,
            fps,
            global_frame_offset,
        )
        write_episode_video(output_dir, ep_idx, images, fps)

        episode_lengths.append(n_frames)
        global_frame_offset += n_frames

    stats = stats_acc.compute()
    write_metadata(
        output_dir,
        episode_lengths=episode_lengths,
        stats=stats,
        fps=fps,
        robot_type=robot_type,
        task_description=task_description,
        state_dim=state_dim,
        action_dim=action_dim,
        image_shape=image_shape,
    )

    total_frames = sum(episode_lengths)
    print(f"Conversion complete: {len(episode_lengths)} episodes, {total_frames} frames")
    print(f"Output: {output_dir}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Convert OSMO hexagon HDF5 episodes to LeRobot v3.0 format",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing episode_*.hdf5 files and dataset_config.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for LeRobot dataset",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Target frame rate (default: 30)",
    )
    parser.add_argument(
        "--robot-type",
        type=str,
        default="hexagarm",
        help="Robot type identifier (default: hexagarm)",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="Hexagon bimanual manipulation",
        help="Task description for metadata",
    )
    return parser.parse_args(argv)


def main() -> int:
    """Entry point for HDF5-to-LeRobot conversion."""
    args = parse_args()
    convert(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        fps=args.fps,
        robot_type=args.robot_type,
        task_description=args.task,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
