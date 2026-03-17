"""Trajectory plotting for LeRobot inference evaluation.

Generates per-joint action delta overlays, reconstructed joint positions,
error heatmaps, summary panels, and cross-episode aggregate comparisons
from predicted vs ground truth action arrays.

All plot functions return matplotlib Figure objects without saving to disk,
suitable for both local file output and MLflow ``log_figure()`` calls.
"""

from __future__ import annotations

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

matplotlib.use("Agg")

JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow",
    "wrist_1",
    "wrist_2",
    "wrist_3",
]


def plot_action_deltas(
    predicted: np.ndarray,
    ground_truth: np.ndarray,
    episode: int,
    fps: float,
    joint_names: list[str] | None = None,
) -> plt.Figure:
    """Per-joint action deltas: predicted vs ground truth overlay.

    Args:
        predicted: Array of shape ``(N, J)`` with predicted action deltas.
        ground_truth: Array of shape ``(N, J)`` with ground truth action deltas.
        episode: Episode index for the plot title.
        fps: Frames per second for the time axis.
        joint_names: Joint labels. Defaults to UR10E 6-DOF names.

    Returns:
        Matplotlib Figure (caller must close).
    """
    names = joint_names or JOINT_NAMES
    n_steps, n_joints = predicted.shape
    t = np.arange(n_steps) / fps

    fig, axes = plt.subplots(n_joints, 1, figsize=(14, 2.5 * n_joints), sharex=True)
    fig.suptitle(f"Episode {episode} — Action Deltas: Predicted vs Ground Truth", fontsize=14, fontweight="bold")

    for j, ax in enumerate(axes):
        ax.plot(t, ground_truth[:, j], color="#2196F3", alpha=0.8, linewidth=1.2, label="Ground Truth")
        ax.plot(t, predicted[:, j], color="#FF5722", alpha=0.8, linewidth=1.2, label="Predicted")
        ax.fill_between(t, ground_truth[:, j], predicted[:, j], alpha=0.15, color="#9C27B0")
        ax.set_ylabel(f"{names[j]}\n(rad)", fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=8)
        if j == 0:
            ax.legend(loc="upper right", fontsize=8)

    axes[-1].set_xlabel("Time (s)", fontsize=10)
    fig.tight_layout()
    return fig


def plot_cumulative_positions(
    predicted: np.ndarray,
    ground_truth: np.ndarray,
    episode: int,
    fps: float,
    joint_names: list[str] | None = None,
) -> plt.Figure:
    """Reconstructed absolute joint positions from cumulative action deltas.

    Args:
        predicted: Array of shape ``(N, J)`` with predicted action deltas.
        ground_truth: Array of shape ``(N, J)`` with ground truth action deltas.
        episode: Episode index for the plot title.
        fps: Frames per second for the time axis.
        joint_names: Joint labels. Defaults to UR10E 6-DOF names.

    Returns:
        Matplotlib Figure (caller must close).
    """
    names = joint_names or JOINT_NAMES
    pred_pos = np.cumsum(predicted, axis=0)
    gt_pos = np.cumsum(ground_truth, axis=0)
    n_steps, n_joints = predicted.shape
    t = np.arange(n_steps) / fps

    fig, axes = plt.subplots(n_joints, 1, figsize=(14, 2.5 * n_joints), sharex=True)
    fig.suptitle(f"Episode {episode} — Reconstructed Joint Positions", fontsize=14, fontweight="bold")

    for j, ax in enumerate(axes):
        ax.plot(t, gt_pos[:, j], color="#2196F3", alpha=0.8, linewidth=1.2, label="Ground Truth")
        ax.plot(t, pred_pos[:, j], color="#FF5722", alpha=0.8, linewidth=1.2, label="Predicted")
        ax.fill_between(t, gt_pos[:, j], pred_pos[:, j], alpha=0.15, color="#9C27B0")
        ax.set_ylabel(f"{names[j]}\n(rad)", fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.tick_params(labelsize=8)
        if j == 0:
            ax.legend(loc="upper right", fontsize=8)

    axes[-1].set_xlabel("Time (s)", fontsize=10)
    fig.tight_layout()
    return fig


def plot_error_heatmap(
    predicted: np.ndarray,
    ground_truth: np.ndarray,
    episode: int,
    fps: float,
    joint_names: list[str] | None = None,
) -> plt.Figure:
    """Absolute error heatmap across joints and time.

    Args:
        predicted: Array of shape ``(N, J)`` with predicted action deltas.
        ground_truth: Array of shape ``(N, J)`` with ground truth action deltas.
        episode: Episode index for the plot title.
        fps: Frames per second for the time axis.
        joint_names: Joint labels. Defaults to UR10E 6-DOF names.

    Returns:
        Matplotlib Figure (caller must close).
    """
    names = joint_names or JOINT_NAMES
    error = np.abs(predicted - ground_truth)
    n_steps = error.shape[0]
    t = np.arange(n_steps) / fps

    fig, ax = plt.subplots(figsize=(14, 3))
    im = ax.imshow(
        error.T,
        aspect="auto",
        cmap="hot",
        interpolation="nearest",
        extent=[t[0], t[-1], len(names) - 0.5, -0.5],
    )
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Time (s)", fontsize=10)
    ax.set_title(f"Episode {episode} — Absolute Error Heatmap", fontsize=12, fontweight="bold")
    fig.colorbar(im, ax=ax, label="Error (rad)")
    fig.tight_layout()
    return fig


def plot_summary_panel(
    predicted: np.ndarray,
    ground_truth: np.ndarray,
    inference_times: np.ndarray,
    episode: int,
    fps: float,
    joint_names: list[str] | None = None,
) -> plt.Figure:
    """2x2 summary: all joints overlay, error boxplots, latency, per-joint MAE.

    Args:
        predicted: Array of shape ``(N, J)`` with predicted action deltas.
        ground_truth: Array of shape ``(N, J)`` with ground truth action deltas.
        inference_times: Array of shape ``(N,)`` with per-step wall-clock seconds.
        episode: Episode index for the plot title.
        fps: Frames per second for the time axis and realtime threshold.
        joint_names: Joint labels. Defaults to UR10E 6-DOF names.

    Returns:
        Matplotlib Figure (caller must close).
    """
    names = joint_names or JOINT_NAMES
    error = np.abs(predicted - ground_truth)
    n_steps, n_joints = predicted.shape
    t = np.arange(n_steps) / fps
    colors = plt.cm.tab10(np.linspace(0, 1, n_joints))

    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle(f"Episode {episode} — Inference Summary", fontsize=14, fontweight="bold")

    # Top-left: all joints overlaid
    ax = axes[0, 0]
    for j in range(n_joints):
        ax.plot(t, ground_truth[:, j], color=colors[j], alpha=0.6, linewidth=1.0)
        ax.plot(t, predicted[:, j], color=colors[j], alpha=0.6, linewidth=1.0, linestyle="--")
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Action delta (rad)", fontsize=9)
    ax.set_title("All Joints (solid=GT, dashed=pred)", fontsize=10)
    ax.grid(True, alpha=0.3)

    # Top-right: error distribution per joint
    ax = axes[0, 1]
    ax.boxplot([error[:, j] for j in range(n_joints)], tick_labels=names, patch_artist=True)
    ax.set_ylabel("Absolute Error (rad)", fontsize=9)
    ax.set_title("Error Distribution per Joint", fontsize=10)
    ax.tick_params(axis="x", rotation=30, labelsize=8)
    ax.grid(True, alpha=0.3, axis="y")

    # Bottom-left: inference timing
    ax = axes[1, 0]
    inf_ms = inference_times * 1000
    ax.plot(inf_ms, color="#4CAF50", alpha=0.7, linewidth=0.8)
    ax.axhline(y=1000 / fps, color="#F44336", linestyle="--", alpha=0.7, label=f"Realtime ({1000 / fps:.1f}ms)")
    ax.set_xlabel("Step", fontsize=9)
    ax.set_ylabel("Inference time (ms)", fontsize=9)
    ax.set_title("Inference Latency", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, min(np.percentile(inf_ms, 99) * 2, inf_ms.max() * 1.1))

    # Bottom-right: per-joint MAE bar chart
    ax = axes[1, 1]
    per_joint_mae = np.mean(error, axis=0)
    bars = ax.bar(names, per_joint_mae, color=colors[:n_joints], alpha=0.7)
    ax.set_ylabel("MAE (rad)", fontsize=9)
    ax.set_title("Per-Joint Mean Absolute Error", fontsize=10)
    ax.tick_params(axis="x", rotation=30, labelsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, per_joint_mae, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{val:.4f}",
            ha="center",
            va="bottom",
            fontsize=7,
        )

    fig.tight_layout()
    return fig


def plot_aggregate_summary(
    episode_metrics: list[dict],
    joint_names: list[str] | None = None,
) -> plt.Figure:
    """Cross-episode aggregate comparison across all evaluated episodes.

    Produces a 2x2 panel:
    - Top-left: per-episode MAE bar chart
    - Top-right: per-joint MAE averaged across episodes (with per-episode scatter)
    - Bottom-left: per-episode throughput bar chart with realtime threshold
    - Bottom-right: per-episode MSE bar chart

    Args:
        episode_metrics: List of dicts, each containing ``episode``, ``mse``,
            ``mae``, ``throughput_hz``, ``avg_inference_ms``, and
            ``per_joint_mae`` (list of floats).
        joint_names: Joint labels. Defaults to UR10E 6-DOF names.

    Returns:
        Matplotlib Figure (caller must close).
    """
    names = joint_names or JOINT_NAMES
    episodes = [m["episode"] for m in episode_metrics]
    ep_labels = [str(e) for e in episodes]
    maes = [m["mae"] for m in episode_metrics]
    mses = [m["mse"] for m in episode_metrics]
    throughputs = [m["throughput_hz"] for m in episode_metrics]
    per_joint = np.array([m["per_joint_mae"] for m in episode_metrics])
    n_joints = per_joint.shape[1]
    colors = plt.cm.tab10(np.linspace(0, 1, n_joints))

    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle(f"Aggregate Inference Summary ({len(episodes)} episodes)", fontsize=14, fontweight="bold")

    # Top-left: per-episode MAE
    ax = axes[0, 0]
    bars = ax.bar(ep_labels, maes, color="#2196F3", alpha=0.7)
    ax.axhline(y=np.mean(maes), color="#F44336", linestyle="--", alpha=0.7, label=f"Mean: {np.mean(maes):.6f}")
    ax.set_xlabel("Episode", fontsize=9)
    ax.set_ylabel("MAE (rad)", fontsize=9)
    ax.set_title("Per-Episode MAE", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, maes, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.5f}", ha="center", va="bottom", fontsize=6)

    # Top-right: per-joint MAE (mean ± spread across episodes)
    ax = axes[0, 1]
    mean_per_joint = np.mean(per_joint, axis=0)
    bar_handles = ax.bar(names, mean_per_joint, color=colors[:n_joints], alpha=0.7)
    for j in range(n_joints):
        ax.scatter([names[j]] * len(episodes), per_joint[:, j], color=colors[j], s=15, alpha=0.5, zorder=3)
    ax.set_ylabel("MAE (rad)", fontsize=9)
    ax.set_title("Per-Joint MAE (mean + per-episode scatter)", fontsize=10)
    ax.tick_params(axis="x", rotation=30, labelsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bar_handles, mean_per_joint, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.4f}", ha="center", va="bottom", fontsize=7)

    # Bottom-left: per-episode throughput
    ax = axes[1, 0]
    bar_colors = ["#4CAF50" if t >= 30 else "#FF5722" for t in throughputs]
    ax.bar(ep_labels, throughputs, color=bar_colors, alpha=0.7)
    ax.axhline(y=30, color="#F44336", linestyle="--", alpha=0.7, label="Realtime (30 Hz)")
    ax.set_xlabel("Episode", fontsize=9)
    ax.set_ylabel("Throughput (Hz)", fontsize=9)
    ax.set_title("Per-Episode Throughput", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")

    # Bottom-right: per-episode MSE
    ax = axes[1, 1]
    bars = ax.bar(ep_labels, mses, color="#9C27B0", alpha=0.7)
    ax.axhline(y=np.mean(mses), color="#F44336", linestyle="--", alpha=0.7, label=f"Mean: {np.mean(mses):.7f}")
    ax.set_xlabel("Episode", fontsize=9)
    ax.set_ylabel("MSE (rad²)", fontsize=9)
    ax.set_title("Per-Episode MSE", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, mses, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.6f}", ha="center", va="bottom", fontsize=6)

    fig.tight_layout()
    return fig
