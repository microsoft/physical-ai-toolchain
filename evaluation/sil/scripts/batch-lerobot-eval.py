#!/usr/bin/env python3
"""Run inference and plot trajectories for multiple episodes.

Runs ``scripts/test-lerobot-inference.py`` for each episode, generates
per-episode trajectory plots, and produces a cross-episode aggregate
summary comparing MAE, MSE, and throughput across all episodes.

Usage:
    python scripts/batch-lerobot-inference.py \\
        --policy-repo ./checkpoint/hve-robo-act-train \\
        --dataset-dir /path/to/dataset \\
        --episodes 1-10 --device mps

    python scripts/batch-lerobot-inference.py \\
        --npz-dir ./predictions \\
        --plot-only
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from inference.plotting import (
    plot_action_deltas,
    plot_aggregate_summary,
    plot_cumulative_positions,
    plot_error_heatmap,
    plot_summary_panel,
)

FPS = 30


def run_inference(
    episode: int,
    policy_repo: str,
    dataset_dir: str,
    device: str,
    out_root: Path,
) -> Path | None:
    """Run inference for one episode, return path to saved npz."""
    out_path = out_root / f"ep{episode:03d}_predictions.npz"
    if out_path.exists():
        print(f"  [ep {episode}] Using cached predictions")
        return out_path

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "test-lerobot-inference.py"),
        "--policy-repo",
        policy_repo,
        "--dataset-dir",
        dataset_dir,
        "--episode",
        str(episode),
        "--start-frame",
        "0",
        "--num-steps",
        "9999",
        "--device",
        device,
        "--output",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [ep {episode}] FAILED:\n{result.stderr}")
        return None

    for line in result.stdout.splitlines():
        if "MSE" in line or "MAE" in line or "Steps evaluated" in line:
            print(f"  [ep {episode}] {line.strip()}")

    return out_path


def plot_episode(episode: int, npz_path: Path, out_root: Path, fps: float, dpi: int) -> dict | None:
    """Generate plots for one episode and return its metrics."""
    data = np.load(npz_path)
    pred = data["predicted"]
    gt = data["ground_truth"]
    inf_times = data["inference_times"]

    ep_dir = out_root / f"episode_{episode:03d}"
    ep_dir.mkdir(exist_ok=True)

    plots = [
        ("action_deltas.png", plot_action_deltas(pred, gt, episode, fps)),
        ("cumulative_positions.png", plot_cumulative_positions(pred, gt, episode, fps)),
        ("error_heatmap.png", plot_error_heatmap(pred, gt, episode, fps)),
        ("summary_panel.png", plot_summary_panel(pred, gt, inf_times, episode, fps)),
    ]
    for filename, fig in plots:
        fig.savefig(ep_dir / filename, dpi=dpi, bbox_inches="tight")
        plt.close(fig)

    error = np.abs(pred - gt)
    metrics = {
        "episode": episode,
        "steps": len(pred),
        "mse": float(np.mean((pred - gt) ** 2)),
        "mae": float(np.mean(error)),
        "per_joint_mae": np.mean(error, axis=0).tolist(),
        "avg_inference_ms": float(np.mean(inf_times) * 1000),
        "throughput_hz": float(1.0 / np.mean(inf_times)) if np.mean(inf_times) > 0 else 0.0,
    }

    print(f"  [ep {episode}] Saved 4 plots to {ep_dir}/")
    return metrics


def parse_episode_range(spec: str) -> list[int]:
    """Parse '1-10' or '0,2,5' into a list of integers."""
    episodes = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            episodes.extend(range(int(start), int(end) + 1))
        else:
            episodes.append(int(part))
    return sorted(set(episodes))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch inference + trajectory plotting for multiple episodes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--policy-repo", default="", help="HuggingFace repo ID or local path to trained policy")
    parser.add_argument("--dataset-dir", default="", help="Path to LeRobot v3 dataset root")
    parser.add_argument("--episodes", default="1-10", help="Episode range: '1-10' or '0,2,5' (default: 1-10)")
    parser.add_argument("--device", default="cuda", help="Inference device (default: cuda)")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/trajectory_plots"), help="Output root")
    parser.add_argument("--fps", type=float, default=30.0, help="Frames per second (default: 30)")
    parser.add_argument("--dpi", type=int, default=150, help="Output image DPI (default: 150)")
    parser.add_argument("--plot-only", action="store_true", help="Skip inference, only plot from existing .npz")
    parser.add_argument(
        "--npz-dir",
        type=Path,
        default=None,
        help="Directory with existing .npz files (for --plot-only)",
    )
    args = parser.parse_args()

    episodes = parse_episode_range(args.episodes)
    out_root = args.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    if not args.plot_only and (not args.policy_repo or not args.dataset_dir):
        parser.error("--policy-repo and --dataset-dir are required unless --plot-only is set")

    npz_dir = args.npz_dir or out_root
    all_metrics = []

    for ep in episodes:
        print(f"\n{'=' * 60}")
        print(f"Episode {ep}")
        print(f"{'=' * 60}")

        if args.plot_only:
            npz_path = npz_dir / f"ep{ep:03d}_predictions.npz"
            if not npz_path.exists():
                print(f"  [ep {ep}] Skipped (no .npz found)")
                continue
        else:
            npz_path = run_inference(ep, args.policy_repo, args.dataset_dir, args.device, out_root)

        if npz_path and npz_path.exists():
            metrics = plot_episode(ep, npz_path, out_root, args.fps, args.dpi)
            if metrics:
                all_metrics.append(metrics)
        else:
            print(f"  [ep {ep}] Skipped plotting (inference failed)")

    # Aggregate summary
    if len(all_metrics) >= 2:
        print(f"\n{'=' * 60}")
        print("Generating aggregate summary")
        print(f"{'=' * 60}")

        fig = plot_aggregate_summary(all_metrics)
        agg_path = out_root / "aggregate_summary.png"
        fig.savefig(agg_path, dpi=args.dpi, bbox_inches="tight")
        plt.close(fig)
        print(f"  Saved {agg_path}")

    # Save metrics JSON
    if all_metrics:
        metrics_path = out_root / "eval_metrics.json"
        with open(metrics_path, "w") as f:
            json.dump(all_metrics, f, indent=2)
        print(f"  Saved {metrics_path}")

    print(f"\nDone. Plots in {out_root}/episode_*/")


if __name__ == "__main__":
    main()
