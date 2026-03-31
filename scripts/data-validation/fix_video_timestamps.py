#!/usr/bin/env python3
"""Fix LeRobot v3 dataset timestamp issues for training compatibility.

Addresses two common problems in LeRobot v3 datasets:

1. **Cumulative episode timestamps**: Episode metadata has cumulative
   from_timestamp/to_timestamp values (e.g., ep 4 starts at 87s) but
   video files have per-episode timestamps starting at 0. Resets
   from_timestamp to 0 and to_timestamp to length/fps.

2. **Parquet timestamp drift**: Robot sensor timestamps drift from the
   video's exact PTS grid (i/fps). Realigns parquet timestamps to the
   1/fps grid when drift exceeds --drift-threshold.
"""
import argparse
import json
import os

import pyarrow as pa
import pyarrow.parquet as pq


def fix_episode_metadata(dataset_dir: str, fps: float, video_keys: list[str]) -> int:
    """Reset cumulative from/to timestamps to per-episode values."""
    episodes_dir = os.path.join(dataset_dir, "meta", "episodes")
    fixed = 0
    for root, _dirs, files in os.walk(episodes_dir):
        for fname in files:
            if not fname.endswith(".parquet"):
                continue
            fpath = os.path.join(root, fname)
            table = pq.read_table(fpath)
            columns = {c: table[c].to_pylist() for c in table.column_names}

            modified = False
            for vk in video_keys:
                from_col = f"videos/{vk}/from_timestamp"
                to_col = f"videos/{vk}/to_timestamp"
                if from_col not in columns or to_col not in columns:
                    continue
                lengths = columns["length"]
                for i in range(len(lengths)):
                    new_from = 0.0
                    new_to = lengths[i] / fps
                    if abs(columns[from_col][i] - new_from) > 0.01 or abs(columns[to_col][i] - new_to) > 0.01:
                        columns[from_col][i] = new_from
                        columns[to_col][i] = new_to
                        modified = True

            if modified:
                new_table = pa.table({c: columns[c] for c in table.column_names})
                pq.write_table(new_table, fpath)
                fixed += 1
                print(f"Fixed cumulative timestamps in {os.path.relpath(fpath, dataset_dir)}")

    return fixed


def fix_parquet_timestamps(
    dataset_dir: str,
    fps: float,
    drift_threshold: float,
) -> int:
    """Realign parquet data timestamps to the exact i/fps grid."""
    data_dir = os.path.join(dataset_dir, "data")
    fixed = 0
    for root, _dirs, files in os.walk(data_dir):
        for fname in sorted(files):
            if not fname.endswith(".parquet"):
                continue
            fpath = os.path.join(root, fname)
            table = pq.read_table(fpath)
            ts = table["timestamp"].to_pylist()
            if not ts:
                continue

            aligned_ts = [i / fps for i in range(len(ts))]
            max_drift = max(abs(a - b) for a, b in zip(ts, aligned_ts))
            if max_drift > drift_threshold:
                col_idx = table.column_names.index("timestamp")
                new_col = pa.array(aligned_ts, type=pa.float64())
                table = table.set_column(col_idx, "timestamp", new_col)
                pq.write_table(table, fpath)
                fixed += 1
                rel = os.path.relpath(fpath, dataset_dir)
                print(f"Realigned {rel} (drift was {max_drift * 1000:.0f}ms)")

    return fixed


def fix_video_timestamps(
    dataset_dir: str,
    drift_threshold: float = 0.02,
    dry_run: bool = False,
) -> None:
    info_path = os.path.join(dataset_dir, "meta", "info.json")
    with open(info_path) as f:
        info = json.load(f)

    fps = info["fps"]
    video_keys = [k for k, v in info.get("features", {}).items() if v.get("dtype") in ("video", "image")]

    if not video_keys:
        print("No video features found, nothing to fix")
        return

    print(f"Dataset: {dataset_dir}")
    print(f"FPS: {fps}, Video features: {video_keys}")
    print(f"Drift threshold: {drift_threshold * 1000:.0f}ms")
    if dry_run:
        print("DRY RUN: no files will be modified")
        return
    print()

    ep_fixed = fix_episode_metadata(dataset_dir, fps, video_keys)
    ts_fixed = fix_parquet_timestamps(dataset_dir, fps, drift_threshold)

    print()
    if ep_fixed or ts_fixed:
        print(f"Fixed {ep_fixed} episode metadata file(s), {ts_fixed} data file(s)")
    else:
        print("No fixes needed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", help="Path to LeRobot v3 dataset root")
    parser.add_argument(
        "--drift-threshold",
        type=float,
        default=0.02,
        help="Realign timestamps when drift exceeds this value in seconds (default: 0.02)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report issues without modifying files",
    )
    args = parser.parse_args()
    fix_video_timestamps(args.dataset_dir, args.drift_threshold, args.dry_run)


if __name__ == "__main__":
    main()
