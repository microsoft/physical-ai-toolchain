#!/usr/bin/env python3
"""Check episode metadata video timestamps vs data parquet timestamps.

Validates that episode metadata from_timestamp/to_timestamp values align
with the actual per-episode data timestamps and video durations.
"""
import argparse
import pyarrow.parquet as pq


def check_episode_timestamps(dataset_dir: str, max_episodes: int = 0) -> None:
    import json
    import os

    info_path = os.path.join(dataset_dir, "meta", "info.json")
    with open(info_path) as f:
        info = json.load(f)
    fps = info["fps"]

    ep_meta = pq.read_table(os.path.join(dataset_dir, "meta", "episodes", "chunk-000", "file-000.parquet"))
    n_episodes = len(ep_meta)
    if max_episodes > 0:
        n_episodes = min(n_episodes, max_episodes)

    print(f"Dataset: {dataset_dir}")
    print(f"FPS: {fps}, Episodes: {len(ep_meta)}")
    print()

    issues = 0
    for i in range(n_episodes):
        ft = ep_meta["videos/observation.images.color/from_timestamp"][i].as_py()
        tt = ep_meta["videos/observation.images.color/to_timestamp"][i].as_py()
        length = ep_meta["length"][i].as_py()
        expected_duration = length / fps

        chunk_idx = ep_meta["data/chunk_index"][i].as_py()
        file_idx = ep_meta["data/file_index"][i].as_py()
        data_path = os.path.join(dataset_dir, "data", f"chunk-{chunk_idx:03d}", f"file-{file_idx:03d}.parquet")

        data_ts_range = ""
        if os.path.exists(data_path):
            data = pq.read_table(data_path)
            ts = data["timestamp"].to_pylist()
            data_ts_range = f"data_ts=[{ts[0]:.4f}..{ts[-1]:.4f}]"

        is_cumulative = abs(ft) > 0.01 and i > 0
        status = "WARN cumulative" if is_cumulative else "OK"
        if is_cumulative:
            issues += 1

        print(
            f"Ep {i:2d}: from_ts={ft:8.2f}  to_ts={tt:8.2f}  "
            f"duration={tt - ft:6.2f}  expected={expected_duration:6.2f}  "
            f"{data_ts_range}  [{status}]"
        )

    print()
    if issues:
        print(f"Found {issues} episodes with cumulative timestamps (need fix)")
    else:
        print("All episodes have per-episode timestamps")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="Path to LeRobot v3 dataset root")
    parser.add_argument(
        "-n",
        "--max-episodes",
        type=int,
        default=0,
        help="Max episodes to check (0 = all)",
    )
    args = parser.parse_args()
    check_episode_timestamps(args.dataset_dir, args.max_episodes)


if __name__ == "__main__":
    main()
