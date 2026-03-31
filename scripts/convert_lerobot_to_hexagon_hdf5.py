#!/usr/bin/env python3
"""Convert LeRobot v3.0 dataset to Hexagon-compatible HDF5 format.

Reads Parquet trajectory data and MP4 video from a LeRobot dataset directory
and writes per-episode HDF5 files compatible with the Hexagon
`aeon_il_training_for_microsoft` ACT training pipeline.

Supports filtering episodes by label (e.g., SUCCESS) using `meta/episode_labels.json`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import av
import h5py
import numpy as np
import pyarrow.parquet as pq


def load_info(dataset_path: Path) -> dict:
    """Load and return LeRobot meta/info.json."""
    info_path = dataset_path / "meta" / "info.json"
    if not info_path.exists():
        raise FileNotFoundError(f"info.json not found: {info_path}")
    with open(info_path) as f:
        return json.load(f)


def load_episode_labels(dataset_path: Path) -> dict[int, list[str]]:
    """Load episode labels from meta/episode_labels.json.

    Returns dict mapping episode_index (int) -> list of label strings.
    """
    labels_path = dataset_path / "meta" / "episode_labels.json"
    if not labels_path.exists():
        return {}
    with open(labels_path) as f:
        data = json.load(f)
    episodes = data.get("episodes", {})
    return {int(k): v for k, v in episodes.items()}


def get_episode_indices(
    info: dict,
    labels: dict[int, list[str]],
    filter_label: str | None,
) -> list[int]:
    """Return sorted list of episode indices, optionally filtered by label."""
    total = info["total_episodes"]
    all_indices = list(range(total))

    if filter_label is None:
        return all_indices

    return sorted(idx for idx in all_indices if filter_label in labels.get(idx, []))


def read_parquet_episode(dataset_path: Path, episode_index: int) -> dict[str, np.ndarray]:
    """Read trajectory data for a single episode from its Parquet file."""
    parquet_path = dataset_path / "data" / f"chunk-{episode_index:03d}" / f"file-{episode_index:03d}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"Parquet file not found: {parquet_path}")

    table = pq.read_table(parquet_path)

    state = np.array(table.column("observation.state").to_pylist(), dtype=np.float32)
    action = np.array(table.column("action").to_pylist(), dtype=np.float32)

    return {"qpos": state, "action": action}


def decode_video_frames(video_path: Path) -> np.ndarray:
    """Decode all frames from an MP4 file into a uint8 numpy array.

    Returns array of shape (T, H, W, 3).
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    frames = []
    container = av.open(str(video_path))
    for frame in container.decode(video=0):
        arr = frame.to_ndarray(format="rgb24")
        frames.append(arr)
    container.close()

    return np.stack(frames, axis=0)


def write_hdf5_episode(
    output_path: Path,
    qpos: np.ndarray,
    action: np.ndarray,
    images: np.ndarray,
    camera_name: str,
) -> None:
    """Write a single episode HDF5 file in Hexagon training format.

    Structure:
        /action                           — (T, action_dim) float32
        /observations/qpos                — (T, obs_dim) float32
        /observations/images/<camera>     — (T, H, W, 3) uint8
    """
    with h5py.File(output_path, "w") as f:
        f.create_dataset("action", data=action, dtype=np.float32)

        obs_group = f.create_group("observations")
        obs_group.create_dataset("qpos", data=qpos, dtype=np.float32)

        img_group = obs_group.create_group("images")
        img_group.create_dataset(camera_name, data=images, dtype=np.uint8)


def convert_dataset(
    dataset_path: Path,
    output_dir: Path,
    config_source: Path | None,
    filter_label: str | None,
    camera_name: str,
) -> None:
    """Convert LeRobot v3.0 dataset to Hexagon HDF5 format."""
    info = load_info(dataset_path)
    labels = load_episode_labels(dataset_path)
    episodes = get_episode_indices(info, labels, filter_label)

    if not episodes:
        print("No episodes match the filter criteria.")
        sys.exit(1)

    print(f"Converting {len(episodes)} episodes (filter: {filter_label or 'none'})")
    print(f"Source: {dataset_path}")
    print(f"Output: {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Video key in LeRobot format
    video_key = f"observation.images.{camera_name}"

    for out_idx, ep_idx in enumerate(episodes):
        print(f"  [{out_idx + 1}/{len(episodes)}] Episode {ep_idx} → episode_{out_idx}.hdf5")

        # Read trajectory
        traj = read_parquet_episode(dataset_path, ep_idx)

        # Decode video
        video_path = dataset_path / "videos" / video_key / f"chunk-{ep_idx:03d}" / f"file-{ep_idx:03d}.mp4"
        images = decode_video_frames(video_path)

        # Validate frame counts match
        n_traj = len(traj["qpos"])
        n_video = len(images)
        if n_traj != n_video:
            print(
                f"    Warning: trajectory frames ({n_traj}) != video frames ({n_video}), using min({n_traj}, {n_video})"
            )
            n = min(n_traj, n_video)
            traj["qpos"] = traj["qpos"][:n]
            traj["action"] = traj["action"][:n]
            images = images[:n]

        # Write HDF5
        output_path = output_dir / f"episode_{out_idx}.hdf5"
        write_hdf5_episode(output_path, traj["qpos"], traj["action"], images, camera_name)

    # Copy dataset_config.json
    if config_source and config_source.exists():
        dest_config = output_dir / "dataset_config.json"
        shutil.copy2(config_source, dest_config)
        print(f"Copied dataset_config.json from {config_source}")
    else:
        print(
            "Warning: No dataset_config.json source provided or found. "
            "You must create one manually for the Hexagon training code."
        )

    print(f"\nConversion complete: {len(episodes)} episodes written to {output_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert LeRobot v3.0 dataset to Hexagon-compatible HDF5 format")
    parser.add_argument(
        "--dataset-path",
        type=Path,
        required=True,
        help="Path to LeRobot v3.0 dataset directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for HDF5 files",
    )
    parser.add_argument(
        "--filter-label",
        type=str,
        default=None,
        help="Filter episodes by label (e.g., SUCCESS). Omit to include all episodes.",
    )
    parser.add_argument(
        "--config-source",
        type=Path,
        default=None,
        help="Path to dataset_config.json to copy into output directory",
    )
    parser.add_argument(
        "--camera-name",
        type=str,
        default="il-camera",
        help="Camera name used in HDF5 paths (default: il-camera)",
    )
    args = parser.parse_args()

    convert_dataset(
        dataset_path=args.dataset_path,
        output_dir=args.output_dir,
        config_source=args.config_source,
        filter_label=args.filter_label,
        camera_name=args.camera_name,
    )


if __name__ == "__main__":
    main()
