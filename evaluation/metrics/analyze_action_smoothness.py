"""Analyze action smoothness and consistency across LeRobot episodes.

Computes per-episode and aggregate metrics from the action sequences in a
LeRobot v3.0 dataset:

* `delta_rms`     — RMS of consecutive action differences (per-step velocity)
* `accel_rms`     — RMS of second differences (acceleration)
* `jerk_rms`      — RMS of third differences (jerk; lower = smoother)
* `path_length`   — sum of L2 deltas, summed across all 6 arm joints
* `joint_range`   — per-joint (max - min) action span
* `gripper_flips` — number of state transitions on the binary gripper channel
* `direction_reversals` — count of sign flips in per-joint velocity (a proxy
  for jitter / control oscillation independent of magnitude)
* `sparc`         — Spectral Arc Length smoothness on the joint-velocity norm
  (more negative = less smooth; -1.6 to -2.0 is a smooth human reach)

Outputs a JSON summary with per-episode rows + aggregate stats so it can be
shared with reviewers without rerunning the analysis.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Default action layout (UR + single gripper). The analyzer auto-detects the
# actual layout from each dataset's `meta/info.json` so dual-arm or gripperless
# robots (e.g., ur5e_dual_arm_schaeffler) are handled without code changes.
ARM_JOINT_DIMS = 6
GRIPPER_DIM = 6  # 7th channel index 6
GRIPPER_FEATURE = "observation.gripper.is_closed"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--datasets",
        nargs="+",
        required=True,
        help="One or more LeRobot dataset roots to analyze (e.g., datasets/hybrid-hack-vla-train-full).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the JSON summary.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=15.0,
        help="Frame rate for SPARC computation (default: 15.0; matches dataset metadata).",
    )
    return parser.parse_args()


def _read_jsonl_concat(files: list[Path]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in files:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return pd.DataFrame(rows)


def _load_dataset(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Return (episodes_df, frames_df, info) for a LeRobot v3.0 dataset on disk.

    Supports both parquet-format (cnc_lerobot-style) and jsonl-format
    (schaeffler_sim_avc1-style) v3 datasets.
    """
    info = json.loads((root / "meta" / "info.json").read_text())

    ep_meta_root = root / "meta" / "episodes"
    episode_parquet = sorted(ep_meta_root.rglob("*.parquet"))
    episode_jsonl = sorted(ep_meta_root.rglob("*.jsonl"))
    flat_episodes_jsonl = root / "meta" / "episodes.jsonl"
    if episode_parquet:
        episodes = pd.concat([pd.read_parquet(p) for p in episode_parquet], ignore_index=True)
    elif episode_jsonl:
        episodes = _read_jsonl_concat(episode_jsonl)
    elif flat_episodes_jsonl.exists():
        episodes = _read_jsonl_concat([flat_episodes_jsonl])
    else:
        raise SystemExit(f"[ERROR] No episode metadata found under {root}/meta/episodes")

    data_root = root / "data"
    data_parquet = sorted(data_root.rglob("*.parquet"))
    data_jsonl = sorted(data_root.rglob("*.jsonl"))
    if data_parquet:
        frames = pd.concat([pd.read_parquet(p) for p in data_parquet], ignore_index=True)
    elif data_jsonl:
        frames = _read_jsonl_concat(data_jsonl)
    else:
        raise SystemExit(f"[ERROR] No frame data found under {root}/data")

    print(
        f"[INFO] {root.name}: {len(episodes)} episodes, {len(frames)} frames, "
        f"fps={info.get('fps')}, robot={info.get('robot_type')}"
    )
    return episodes, frames, info


def _sparc(velocity_magnitude: np.ndarray, fps: float) -> float:
    """Spectral Arc Length smoothness metric.

    Reference: Balasubramanian et al., 2015. Returns a negative number;
    closer to 0 is smoother. Returns NaN for sequences shorter than 8 samples.
    """
    if velocity_magnitude.size < 8:
        return float("nan")

    # Pad to next power of two for stable FFT
    n = int(2 ** np.ceil(np.log2(velocity_magnitude.size)))
    spectrum = np.abs(np.fft.rfft(velocity_magnitude, n=n))
    if spectrum.max() <= 0:
        return float("nan")
    spectrum = spectrum / spectrum.max()
    freqs = np.fft.rfftfreq(n, d=1.0 / fps)

    # Cut spectrum at the highest frequency above 5% magnitude (or 20 Hz cap)
    cutoff_amp = 0.05
    idx_above = np.where(spectrum >= cutoff_amp)[0]
    if idx_above.size == 0:
        return float("nan")
    cutoff_idx = min(idx_above[-1] + 1, np.searchsorted(freqs, 20.0) + 1, len(freqs))
    spectrum = spectrum[:cutoff_idx]
    freqs = freqs[:cutoff_idx]
    if freqs.size < 2:
        return float("nan")

    # Arc length on the (frequency, normalized magnitude) curve, normalized by frequency band width
    df = np.diff(freqs / freqs[-1])
    da = np.diff(spectrum)
    arc_length = -float(np.sum(np.sqrt(df * df + da * da)))
    return arc_length


def _direction_reversals(arm_velocity: np.ndarray) -> int:
    """Count sign flips per joint, summed across joints. Ignores zero-velocity samples."""
    if arm_velocity.shape[0] < 2:
        return 0
    sign = np.sign(arm_velocity)
    # Replace zero with previous non-zero to avoid spurious flip detection
    for j in range(sign.shape[1]):
        prev = 0.0
        for i in range(sign.shape[0]):
            if sign[i, j] == 0:
                sign[i, j] = prev
            else:
                prev = sign[i, j]
    flips = (sign[1:] * sign[:-1]) < 0
    return int(np.sum(flips))


def _episode_metrics(
    actions: np.ndarray,
    gripper: np.ndarray | None,
    fps: float,
    arm_dims: int,
    gripper_dim: int | None,
) -> dict[str, Any]:
    """Compute smoothness metrics for a single episode's action sequence.

    Args:
        actions: shape (T, D) — full action vector for the robot.
        gripper: optional shape (T,) observed gripper open/close booleans.
        fps: dataset frame rate.
        arm_dims: number of leading dims to treat as arm-joint targets.
        gripper_dim: optional column index of the action gripper target.
    """
    arm = actions[:, :arm_dims]
    if arm.shape[0] < 2:
        return {"length": int(arm.shape[0]), "skipped_too_short": True}

    delta = np.diff(arm, axis=0)
    accel = np.diff(delta, axis=0) if arm.shape[0] >= 3 else np.zeros_like(delta[:0])
    jerk = np.diff(accel, axis=0) if accel.shape[0] >= 2 else np.zeros_like(delta[:0])
    velocity_magnitude = np.linalg.norm(delta, axis=1)

    metrics: dict[str, Any] = {
        "length": int(arm.shape[0]),
        "delta_rms": float(np.sqrt(np.mean(delta**2))),
        "accel_rms": float(np.sqrt(np.mean(accel**2))) if accel.size else float("nan"),
        "jerk_rms": float(np.sqrt(np.mean(jerk**2))) if jerk.size else float("nan"),
        "path_length": float(np.sum(velocity_magnitude)),
        "max_velocity": float(velocity_magnitude.max()) if velocity_magnitude.size else 0.0,
        "p95_velocity": float(np.quantile(velocity_magnitude, 0.95)) if velocity_magnitude.size else 0.0,
        "joint_range": [float(arm[:, j].max() - arm[:, j].min()) for j in range(arm_dims)],
        "direction_reversals": _direction_reversals(delta),
        "sparc_velocity_norm": _sparc(velocity_magnitude, fps),
    }

    if gripper_dim is not None and gripper_dim < actions.shape[1]:
        gripper_action = actions[:, gripper_dim]
        metrics["gripper_action_range"] = float(gripper_action.max() - gripper_action.min())
    if gripper is not None and gripper.size >= 2:
        metrics["gripper_flips"] = int(np.sum(np.diff(gripper.astype(np.int8)) != 0))

    return metrics


def _aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate stats over a collection of episode metric rows."""
    df = pd.DataFrame(list(rows))
    numeric_cols = [
        "length",
        "delta_rms",
        "accel_rms",
        "jerk_rms",
        "path_length",
        "max_velocity",
        "p95_velocity",
        "direction_reversals",
        "sparc_velocity_norm",
        "gripper_action_range",
        "gripper_flips",
    ]
    summary: dict[str, Any] = {"episode_count": len(df)}
    for col in numeric_cols:
        if col not in df.columns:
            continue
        series = df[col].dropna()
        if series.empty:
            continue
        summary[col] = {
            "mean": float(series.mean()),
            "std": float(series.std()),
            "min": float(series.min()),
            "p25": float(series.quantile(0.25)),
            "p50": float(series.quantile(0.50)),
            "p75": float(series.quantile(0.75)),
            "max": float(series.max()),
        }
    return summary


def _resolve_layout(info: dict[str, Any], frames: pd.DataFrame) -> tuple[int, int | None]:
    """Return (arm_dims, gripper_dim) derived from info.json action feature.

    Assumes any joint with a name like '*gripper*' is the gripper. Otherwise
    treats every action dim as an arm joint and returns gripper_dim=None.
    """
    action_feat = info.get("features", {}).get("action", {})
    names = action_feat.get("names") or []
    if not names:
        sample = frames["action"].iloc[0]
        return (len(sample), None)
    gripper_idx = next(
        (i for i, n in enumerate(names) if "gripper" in str(n).lower()),
        None,
    )
    if gripper_idx is None:
        return (len(names), None)
    return (gripper_idx, gripper_idx)


def _episode_task_label(ep_meta: pd.Series, frames_group: pd.DataFrame, tasks_by_index: dict[int, str]) -> str:
    tasks_field = ep_meta.get("tasks") if "tasks" in ep_meta.index else None
    if tasks_field is not None:
        try:
            if hasattr(tasks_field, "tolist"):
                tasks_field = tasks_field.tolist()
            if isinstance(tasks_field, (list, tuple)) and tasks_field:
                return str(tasks_field[0])
            if isinstance(tasks_field, str) and tasks_field:
                return tasks_field
        except (TypeError, AttributeError):
            # Malformed optional task metadata falls back to the task_index lookup below.
            pass
    if "task_index" in frames_group.columns:
        idx = int(frames_group["task_index"].iloc[0])
        return tasks_by_index.get(idx, "")
    return ""


def _load_tasks_index(root: Path) -> dict[int, str]:
    tasks_jsonl = root / "meta" / "tasks.jsonl"
    tasks_parquet = root / "meta" / "tasks.parquet"
    if tasks_jsonl.exists():
        out: dict[int, str] = {}
        with tasks_jsonl.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                out[int(row["task_index"])] = str(row["task"])
        return out
    if tasks_parquet.exists():
        df = pd.read_parquet(tasks_parquet)
        return {int(r["task_index"]): str(r["task"]) for _, r in df.iterrows()}
    return {}


def _analyze_dataset(root: Path, fps: float) -> dict[str, Any]:
    episodes, frames, info = _load_dataset(root)
    frames = frames.sort_values(["episode_index", "frame_index"], kind="stable")
    arm_dims, gripper_dim = _resolve_layout(info, frames)
    tasks_by_index = _load_tasks_index(root)
    has_gripper_obs = GRIPPER_FEATURE in frames.columns

    rows: list[dict[str, Any]] = []
    for episode_index, group in frames.groupby("episode_index", sort=True):
        actions = np.stack(list(group["action"].to_numpy())).astype(np.float32)
        gripper: np.ndarray | None = None
        if has_gripper_obs:
            gripper_raw = group[GRIPPER_FEATURE].to_numpy()
            gripper = gripper_raw.astype(bool) if gripper_raw.dtype != bool else gripper_raw
        ep_meta_rows = episodes[episodes["episode_index"] == episode_index]
        ep_meta = ep_meta_rows.iloc[0] if not ep_meta_rows.empty else pd.Series(dtype=object)
        episode_id = int(str(episode_index))
        row = {
            "episode_index": episode_id,
            "task": _episode_task_label(ep_meta, group, tasks_by_index),
            **_episode_metrics(actions, gripper, fps, arm_dims, gripper_dim),
        }
        rows.append(row)

    return {
        "dataset": root.name,
        "dataset_path": str(root),
        "fps": fps,
        "arm_dims": arm_dims,
        "gripper_dim": gripper_dim,
        "episodes": rows,
        "aggregate": _aggregate(rows),
    }


def main() -> int:
    args = _parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {"datasets": []}
    for raw in args.datasets:
        root = Path(raw)
        if not root.exists():
            print(f"[ERROR] Dataset not found: {root}", file=sys.stderr)
            return 2
        results["datasets"].append(_analyze_dataset(root, args.fps))

    args.output.write_text(json.dumps(results, indent=2))
    print(f"[INFO] Wrote summary to {args.output}")

    print("\n=== Aggregate summary ===")
    for entry in results["datasets"]:
        agg = entry["aggregate"]
        print(f"\n[{entry['dataset']}] episodes={agg['episode_count']}")
        for key in ["delta_rms", "accel_rms", "jerk_rms", "path_length", "sparc_velocity_norm", "gripper_flips"]:
            stats = agg.get(key)
            if stats is None:
                continue
            print(
                f"  {key:24s} mean={stats['mean']:.4f}  std={stats['std']:.4f}  "
                f"p50={stats['p50']:.4f}  p95={stats['max']:.4f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
