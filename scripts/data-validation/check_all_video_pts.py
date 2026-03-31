#!/usr/bin/env python3
"""Find the worst video-parquet timestamp mismatch across all episodes.

Iterates every episode in a LeRobot v3 dataset, compares video frame PTS
(via ffprobe) against parquet timestamps, and reports per-episode max drift.
Requires ffprobe (from ffmpeg) on PATH.
"""
import argparse
import json
import os
import subprocess

import pyarrow.parquet as pq


def check_all_episodes(dataset_dir: str) -> None:
    info_path = os.path.join(dataset_dir, "meta", "info.json")
    with open(info_path) as f:
        info = json.load(f)

    total_episodes = info.get("total_episodes", 0)
    fps = info["fps"]
    print(f"Dataset: {dataset_dir}")
    print(f"FPS: {fps}, Total episodes: {total_episodes}")
    print()

    worst_diff = 0.0
    worst_ep = 0

    for ep_idx in range(total_episodes):
        vid_path = os.path.join(
            dataset_dir,
            "videos",
            "observation.images.color",
            f"chunk-{ep_idx:03d}",
            f"file-{ep_idx:03d}.mp4",
        )
        data_path = os.path.join(
            dataset_dir,
            "data",
            f"chunk-{ep_idx:03d}",
            f"file-{ep_idx:03d}.parquet",
        )

        if not os.path.exists(vid_path) or not os.path.exists(data_path):
            print(f"Ep {ep_idx:2d}: SKIP (files missing)")
            continue

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

        n = min(len(video_pts), len(parquet_ts))
        ep_max_diff = 0.0
        for i in range(n):
            diff = abs(parquet_ts[i] - video_pts[i])
            ep_max_diff = max(ep_max_diff, diff)

        if ep_max_diff > worst_diff:
            worst_diff = ep_max_diff
            worst_ep = ep_idx

        status = "WARN" if ep_max_diff > 33.0 / 1000 else "OK"
        print(
            f"Ep {ep_idx:2d}: vid={len(video_pts):4d} pq={len(parquet_ts):4d} "
            f"max_diff={ep_max_diff * 1000:7.1f}ms  [{status}]"
        )

    print(f"\nWorst: Episode {worst_ep}, max diff = {worst_diff * 1000:.1f}ms ({worst_diff:.4f}s)")
    if worst_diff > 33.0 / 1000:
        print("WARNING: Drift exceeds one frame period (33ms at 30fps)")
        print("Run fix_video_timestamps.py to realign parquet timestamps")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="Path to LeRobot v3 dataset root")
    args = parser.parse_args()
    check_all_episodes(args.dataset_dir)


if __name__ == "__main__":
    main()
