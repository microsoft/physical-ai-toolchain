"""Copy SUCCESS-labeled HDF5 episodes from hexagon_episodes to a filtered output directory.

Maps LeRobot episode indices (from episode_labels.json) back to the original
hexagon_episodes HDF5 files using the same merge order as tmp/merge_and_convert.py,
then copies only SUCCESS episodes with sequential renumbering.

Usage:
    python scripts/copy_success_episodes.py \
        --labels datasets/aeon_houston/pick/left/2026_03_11_16_18_03/meta/episode_labels.json \
        --source datasets/hexagon_episodes \
        --sessions 2026_03_11_16_18_03 2026_03_11_16_34_20 2026_03_11_19_01_35 \
        --output datasets/hexagon_episodes_success
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


def collect_episode_paths(source: Path, sessions: list[str]) -> list[Path]:
    """Collect HDF5 episode paths in the same order as merge_and_convert.py."""
    pattern = re.compile(r"^episode_(\d+)\.hdf5$")
    all_files: list[Path] = []

    for session in sessions:
        session_dir = source / session
        if not session_dir.exists():
            raise FileNotFoundError(f"Session directory not found: {session_dir}")

        eps: list[tuple[int, Path]] = []
        for p in session_dir.iterdir():
            m = pattern.match(p.name)
            if m:
                eps.append((int(m.group(1)), p))
        eps.sort(key=lambda x: x[0])
        all_files.extend(path for _, path in eps)

    return all_files


def load_success_indices(labels_path: Path) -> list[int]:
    """Load episode indices labeled SUCCESS from episode_labels.json."""
    with open(labels_path) as f:
        data = json.load(f)

    return sorted(int(idx) for idx, labels in data["episodes"].items() if "SUCCESS" in labels)


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy SUCCESS HDF5 episodes to a filtered directory")
    parser.add_argument("--labels", type=Path, required=True, help="Path to episode_labels.json")
    parser.add_argument("--source", type=Path, required=True, help="Root hexagon_episodes directory")
    parser.add_argument(
        "--sessions",
        nargs="+",
        required=True,
        help="Session subdirectories in merge order",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output directory for filtered episodes")
    parser.add_argument("--symlink", action="store_true", help="Create symlinks instead of copying files")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be copied without copying")
    args = parser.parse_args()

    episode_paths = collect_episode_paths(args.source, args.sessions)
    print(f"Total HDF5 episodes across sessions: {len(episode_paths)}")

    success_indices = load_success_indices(args.labels)
    print(f"SUCCESS episodes from labels: {len(success_indices)}")
    print(f"Indices: {success_indices}")

    # Validate indices are within range
    max_labeled = max(success_indices)
    if max_labeled >= len(episode_paths):
        raise ValueError(f"Label index {max_labeled} exceeds available episodes ({len(episode_paths)})")

    # Map SUCCESS indices to source HDF5 paths
    success_files = [(idx, episode_paths[idx]) for idx in success_indices]

    if args.dry_run:
        print(f"\nWould copy {len(success_files)} episodes to {args.output}/")
        for new_idx, (orig_idx, path) in enumerate(success_files):
            print(f"  episode_{new_idx}.hdf5 <- [{orig_idx}] {path.parent.name}/{path.name}")
        return

    args.output.mkdir(parents=True, exist_ok=True)

    # Copy dataset_config.json from the first non-empty session
    for session in args.sessions:
        config_src = args.source / session / "dataset_config.json"
        if config_src.exists():
            shutil.copy2(config_src, args.output / "dataset_config.json")
            print(f"Copied dataset_config.json from {session}")
            break

    # Copy and renumber episodes
    op = "Symlinked" if args.symlink else "Copied"
    for new_idx, (orig_idx, src_path) in enumerate(success_files):
        dst_path = args.output / f"episode_{new_idx}.hdf5"
        if args.symlink:
            dst_path.symlink_to(src_path.resolve())
        else:
            shutil.copy2(src_path, dst_path)
        print(f"  episode_{new_idx}.hdf5 <- [{orig_idx}] {src_path.parent.name}/{src_path.name}")

    print(f"\n{op} {len(success_files)} SUCCESS episodes to {args.output}")


if __name__ == "__main__":
    main()
