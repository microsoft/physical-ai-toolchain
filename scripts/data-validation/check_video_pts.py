#!/usr/bin/env python3
"""Compare video frame PTS against parquet timestamps for a single episode.

Runs ffprobe to extract actual video frame presentation timestamps, then
compares them frame-by-frame against the parquet timestamp column to detect
drift between the robot sensor clock and the video encoder.
"""
import argparse
import json
import os
import subprocess

import pyarrow.parquet as pq


def check_video_pts(dataset_dir: str, episode: int) -> float:
    vid_path = os.path.join(
        dataset_dir,
        "videos",
        "observation.images.color",
        f"chunk-{episode:03d}",
        f"file-{episode:03d}.mp4",
    )
    data_path = os.path.join(
        dataset_dir,
        "data",
        f"chunk-{episode:03d}",
        f"file-{episode:03d}.parquet",
    )

    if not os.path.exists(vid_path):
        print(f"Video not found: {vid_path}")
        return 0.0
    if not os.path.exists(data_path):
        print(f"Parquet not found: {data_path}")
        return 0.0

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=pts_time",
            "-of",
            "json",
            vid_path,
        ],
        capture_output=True,
        text=True,
    )
    frames = json.loads(result.stdout)["frames"]
    video_pts = [float(f["pts_time"]) for f in frames]

    data = pq.read_table(data_path)
    parquet_ts = data["timestamp"].to_pylist()

    print(f"Episode {episode}")
    print(f"  Video frames: {len(video_pts)}, Parquet frames: {len(parquet_ts)}")
    print(f"  Video PTS range: [{video_pts[0]:.6f}..{video_pts[-1]:.6f}]")
    print(f"  Parquet ts range: [{parquet_ts[0]:.6f}..{parquet_ts[-1]:.6f}]")

    n = min(len(video_pts), len(parquet_ts))
    print(f"\n  {'Frame':>6} {'Video PTS':>12} {'Parquet TS':>12} {'Diff (ms)':>10}")
    for i in range(min(10, n)):
        diff_ms = (parquet_ts[i] - video_pts[i]) * 1000
        print(f"  {i:>6} {video_pts[i]:>12.6f} {parquet_ts[i]:>12.6f} {diff_ms:>10.3f}")

    max_diff = 0.0
    max_idx = 0
    for i in range(n):
        diff = abs(parquet_ts[i] - video_pts[i])
        if diff > max_diff:
            max_diff = diff
            max_idx = i

    print(f"\n  Max diff: {max_diff * 1000:.3f}ms at frame {max_idx}")
    print(f"    Video PTS: {video_pts[max_idx]:.6f}")
    print(f"    Parquet TS: {parquet_ts[max_idx]:.6f}")
    return max_diff


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="Path to LeRobot v3 dataset root")
    parser.add_argument(
        "-e",
        "--episode",
        type=int,
        default=0,
        help="Episode index to check (default: 0)",
    )
    args = parser.parse_args()
    check_video_pts(args.dataset_dir, args.episode)


if __name__ == "__main__":
    main()
