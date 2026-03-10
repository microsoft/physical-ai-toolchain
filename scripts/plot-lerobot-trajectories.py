#!/usr/bin/env python3
"""Plot predicted vs ground truth action trajectories from inference results.

Renders per-joint action deltas and reconstructed absolute joint positions,
with error heatmaps and summary statistics from saved .npz prediction files.

Usage:
    python scripts/plot-lerobot-trajectories.py predictions.npz
    python scripts/plot-lerobot-trajectories.py predictions.npz --output-dir ./plots
"""

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from inference.plotting import (
    plot_action_deltas,
    plot_cumulative_positions,
    plot_error_heatmap,
    plot_summary_panel,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot trajectory predictions from .npz files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("npz_file", type=Path, help="Path to predictions .npz file")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory (default: sibling of npz)")
    parser.add_argument("--episode", type=int, default=0, help="Episode label for plot titles")
    parser.add_argument("--fps", type=float, default=30.0, help="Frames per second (default: 30)")
    parser.add_argument("--dpi", type=int, default=150, help="Output image DPI (default: 150)")
    args = parser.parse_args()

    if not args.npz_file.exists():
        print(f"Error: File not found: {args.npz_file}", file=sys.stderr)
        sys.exit(1)

    out_dir = args.output_dir or args.npz_file.parent / "trajectory_plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    data = np.load(args.npz_file)
    predicted = data["predicted"]
    ground_truth = data["ground_truth"]
    inference_times = data["inference_times"]

    print(f"Loaded {predicted.shape[0]} steps, {predicted.shape[1]} joints")
    print(f"Output dir: {out_dir}\n")

    plots = [
        ("action_deltas.png", plot_action_deltas(predicted, ground_truth, args.episode, args.fps)),
        ("cumulative_positions.png", plot_cumulative_positions(predicted, ground_truth, args.episode, args.fps)),
        ("error_heatmap.png", plot_error_heatmap(predicted, ground_truth, args.episode, args.fps)),
        ("summary_panel.png", plot_summary_panel(predicted, ground_truth, inference_times, args.episode, args.fps)),
    ]

    for filename, fig in plots:
        path = out_dir / filename
        fig.savefig(path, dpi=args.dpi, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved {path}")

    print("\nAll plots saved.")


if __name__ == "__main__":
    main()
