"""LeRobot replay-based inference evaluation."""

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch

JOINT_NAMES: list[str] = []


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    return plt


def _dim_label(j: int) -> str:
    return JOINT_NAMES[j] if j < len(JOINT_NAMES) else f"dim_{j}"


def plot_action_deltas(predicted, ground_truth, episode, fps):
    plt = _setup_matplotlib()
    n_steps, n_joints = predicted.shape
    t = np.arange(n_steps) / fps
    fig, axes = plt.subplots(n_joints, 1, figsize=(14, 2.5 * n_joints), sharex=True)
    fig.suptitle(f"Episode {episode} — Action Deltas: Predicted vs Ground Truth", fontsize=14, fontweight="bold")
    if n_joints == 1:
        axes = [axes]
    for j, ax in enumerate(axes):
        ax.plot(t, ground_truth[:, j], color="#2196F3", alpha=0.8, linewidth=1.2, label="Ground Truth")
        ax.plot(t, predicted[:, j], color="#FF5722", alpha=0.8, linewidth=1.2, label="Predicted")
        ax.fill_between(t, ground_truth[:, j], predicted[:, j], alpha=0.15, color="#9C27B0")
        ax.set_ylabel(_dim_label(j), fontsize=9)
        ax.grid(True, alpha=0.3)
        if j == 0:
            ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Time (s)", fontsize=10)
    fig.tight_layout()
    return fig


def plot_cumulative_positions(predicted, ground_truth, episode, fps):
    plt = _setup_matplotlib()
    pred_pos = np.cumsum(predicted, axis=0)
    gt_pos = np.cumsum(ground_truth, axis=0)
    n_steps, n_joints = predicted.shape
    t = np.arange(n_steps) / fps
    fig, axes = plt.subplots(n_joints, 1, figsize=(14, 2.5 * n_joints), sharex=True)
    fig.suptitle(f"Episode {episode} — Reconstructed Positions", fontsize=14, fontweight="bold")
    if n_joints == 1:
        axes = [axes]
    for j, ax in enumerate(axes):
        ax.plot(t, gt_pos[:, j], color="#2196F3", alpha=0.8, linewidth=1.2, label="Ground Truth")
        ax.plot(t, pred_pos[:, j], color="#FF5722", alpha=0.8, linewidth=1.2, label="Predicted")
        ax.fill_between(t, gt_pos[:, j], pred_pos[:, j], alpha=0.15, color="#9C27B0")
        ax.set_ylabel(_dim_label(j), fontsize=9)
        ax.grid(True, alpha=0.3)
        if j == 0:
            ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Time (s)", fontsize=10)
    fig.tight_layout()
    return fig


def plot_error_heatmap(predicted, ground_truth, episode, fps):
    plt = _setup_matplotlib()
    error = np.abs(predicted - ground_truth)
    n_steps, n_joints = error.shape
    labels = [_dim_label(j) for j in range(n_joints)]
    t = np.arange(n_steps) / fps
    fig, ax = plt.subplots(figsize=(14, 3))
    im = ax.imshow(
        error.T,
        aspect="auto",
        cmap="hot",
        interpolation="nearest",
        extent=[t[0], t[-1], n_joints - 0.5, -0.5],
    )
    ax.set_yticks(range(n_joints))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Time (s)", fontsize=10)
    ax.set_title(f"Episode {episode} — Absolute Error Heatmap", fontsize=12, fontweight="bold")
    fig.colorbar(im, ax=ax, label="Absolute Error")
    fig.tight_layout()
    return fig


def plot_summary_panel(predicted, ground_truth, inference_times, episode, fps):
    plt = _setup_matplotlib()
    error = np.abs(predicted - ground_truth)
    n_steps, n_joints = predicted.shape
    labels = [_dim_label(j) for j in range(n_joints)]
    t = np.arange(n_steps) / fps
    colors = plt.cm.tab10(np.linspace(0, 1, n_joints))
    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle(f"Episode {episode} — Inference Summary", fontsize=14, fontweight="bold")
    ax = axes[0, 0]
    for j in range(n_joints):
        ax.plot(t, ground_truth[:, j], color=colors[j], alpha=0.6, linewidth=1.0)
        ax.plot(t, predicted[:, j], color=colors[j], alpha=0.6, linewidth=1.0, linestyle="--")
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Action delta", fontsize=9)
    ax.set_title("All Dimensions (solid=GT, dashed=pred)", fontsize=10)
    ax.grid(True, alpha=0.3)
    ax = axes[0, 1]
    bp = ax.boxplot([error[:, j] for j in range(n_joints)], tick_labels=labels, patch_artist=True)
    for patch, c in zip(bp["boxes"], colors[:n_joints]):
        patch.set_facecolor(c)
        patch.set_alpha(0.6)
    ax.set_ylabel("Absolute Error", fontsize=9)
    ax.set_title("Error Distribution per Dimension", fontsize=10)
    ax.tick_params(axis="x", rotation=45, labelsize=6 if n_joints > 8 else 8)
    ax.grid(True, alpha=0.3, axis="y")
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
    ax = axes[1, 1]
    per_joint_mae = np.mean(error, axis=0)
    bars = ax.bar(labels, per_joint_mae, color=colors[:n_joints], alpha=0.7)
    ax.set_ylabel("MAE", fontsize=9)
    ax.set_title("Per-Dimension Mean Absolute Error", fontsize=10)
    ax.tick_params(axis="x", rotation=45, labelsize=6 if n_joints > 8 else 8)
    ax.grid(True, alpha=0.3, axis="y")
    for bar, val in zip(bars, per_joint_mae):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.4f}", ha="center", va="bottom", fontsize=7)
    fig.tight_layout()
    return fig


def plot_aggregate_summary(episode_metrics):
    plt = _setup_matplotlib()
    episodes = [m["episode"] for m in episode_metrics]
    ep_labels = [str(e) for e in episodes]
    maes = [m["mae"] for m in episode_metrics]
    mses = [m["mse"] for m in episode_metrics]
    throughputs = [m["throughput_hz"] for m in episode_metrics]
    per_dim = np.array([m["per_dim_mae"] for m in episode_metrics])
    n_dims = per_dim.shape[1]
    labels = [_dim_label(j) for j in range(n_dims)]
    colors = plt.cm.tab10(np.linspace(0, 1, n_dims))
    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle(f"Aggregate Inference Summary ({len(episodes)} episodes)", fontsize=14, fontweight="bold")
    ax = axes[0, 0]
    ax.bar(ep_labels, maes, color="#2196F3", alpha=0.7)
    ax.axhline(y=np.mean(maes), color="#F44336", linestyle="--", alpha=0.7, label=f"Mean: {np.mean(maes):.6f}")
    ax.set_xlabel("Episode")
    ax.set_ylabel("MAE")
    ax.set_title("Per-Episode MAE")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    ax = axes[0, 1]
    mean_per_dim = np.mean(per_dim, axis=0)
    ax.bar(labels, mean_per_dim, color=colors[:n_dims], alpha=0.7)
    for j in range(n_dims):
        ax.scatter([labels[j]] * len(episodes), per_dim[:, j], color=colors[j], s=15, alpha=0.5, zorder=3)
    ax.set_ylabel("MAE")
    ax.set_title("Per-Dimension MAE (mean + scatter)")
    ax.tick_params(axis="x", rotation=45, labelsize=6 if n_dims > 8 else 8)
    ax.grid(True, alpha=0.3, axis="y")
    ax = axes[1, 0]
    bar_colors = ["#4CAF50" if t >= 30 else "#FF5722" for t in throughputs]
    ax.bar(ep_labels, throughputs, color=bar_colors, alpha=0.7)
    ax.axhline(y=30, color="#F44336", linestyle="--", alpha=0.7, label="Realtime (30 Hz)")
    ax.set_xlabel("Episode")
    ax.set_ylabel("Throughput (Hz)")
    ax.set_title("Per-Episode Throughput")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    ax = axes[1, 1]
    ax.bar(ep_labels, mses, color="#CE93D8", alpha=0.7)
    ax.axhline(y=np.mean(mses), color="#F44336", linestyle="--", alpha=0.7, label=f"Mean: {np.mean(mses):.7f}")
    ax.set_xlabel("Episode")
    ax.set_ylabel("MSE")
    ax.set_title("Per-Episode MSE")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    return fig


def _find_data_file(ds_dir: str, ep_idx: int) -> str | None:
    info_path = os.path.join(ds_dir, "meta", "info.json")
    with open(info_path) as f:
        ds_info = json.load(f)
    chunks_size = ds_info.get("chunks_size", 1000)
    ep_chunk = ep_idx // chunks_size
    candidates = [
        os.path.join(ds_dir, "data", f"chunk-{ep_chunk:03d}", f"episode_{ep_idx:06d}.parquet"),
        os.path.join(ds_dir, "data", f"chunk-{ep_idx:03d}", f"file-{ep_idx:03d}.parquet"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _find_video_file(ds_dir: str, vk: str, ep_idx: int) -> str | None:
    info_path = os.path.join(ds_dir, "meta", "info.json")
    with open(info_path) as f:
        ds_info = json.load(f)
    chunks_size = ds_info.get("chunks_size", 1000)
    ep_chunk = ep_idx // chunks_size
    candidates = [
        os.path.join(ds_dir, "videos", vk, f"chunk-{ep_chunk:03d}", f"episode_{ep_idx:06d}.mp4"),
        os.path.join(ds_dir, "videos", vk, f"chunk-{ep_idx:03d}", f"file-{ep_idx:03d}.mp4"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def main() -> int:
    import av
    import pyarrow.parquet as pq
    from lerobot.policies.act.modeling_act import ACTPolicy

    policy_repo_id = os.environ["POLICY_REPO_ID"]
    policy_type = os.environ.get("POLICY_TYPE", "act")
    dataset_repo_id = os.environ.get("DATASET_REPO_ID", "")
    eval_episodes = int(os.environ.get("EVAL_EPISODES", "10"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/workspace/outputs/eval"))
    job_name = os.environ.get("JOB_NAME", "lerobot-eval")
    mlflow_enable = os.environ.get("MLFLOW_ENABLE", "false") == "true"

    output_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[INFO] Using device: {device}")

    # Resolve dataset source
    dataset_dir_env = os.environ.get("DATASET_DIR", "")
    if dataset_dir_env and os.path.isdir(dataset_dir_env):
        dataset_dir = dataset_dir_env
        print(f"[INFO] Using blob-downloaded dataset: {dataset_dir}")
    elif dataset_repo_id and dataset_repo_id != "none":
        from huggingface_hub import snapshot_download

        dataset_dir = snapshot_download(repo_id=dataset_repo_id, repo_type="dataset")
        print(f"[INFO] Dataset downloaded from HuggingFace: {dataset_dir}")
    else:
        print("[ERROR] Dataset source required: set DATASET_REPO_ID or blob storage params")
        return 1

    # Load dataset info
    with open(os.path.join(dataset_dir, "meta", "info.json")) as f:
        info = json.load(f)
    fps = info["fps"]

    # Identify video key from features
    features = info.get("features", {})
    video_keys = [k for k, v in features.items() if v.get("dtype") in ("video", "image")]
    image_key = video_keys[0] if video_keys else "observation.images.color"

    # Load policy (normalization is handled internally by select_action)
    print(f"[INFO] Loading policy from: {policy_repo_id}")
    policy = ACTPolicy.from_pretrained(policy_repo_id)
    policy.to(device)

    # Determine episode range
    episodes_meta_path = os.path.join(dataset_dir, "meta", "episodes.jsonl")
    if os.path.exists(episodes_meta_path):
        with open(episodes_meta_path) as f:
            total_episodes = sum(1 for _ in f)
    else:
        total_episodes = eval_episodes
    num_episodes = min(eval_episodes, total_episodes)

    # Start MLflow run
    if mlflow_enable:
        import mlflow

        mlflow.start_run(run_name=job_name)
        mlflow.log_params(
            {
                "policy_repo_id": policy_repo_id,
                "policy_type": policy_type,
                "dataset_repo_id": dataset_repo_id,
                "eval_episodes": num_episodes,
                "device": str(device),
                "fps": fps,
            }
        )

    all_episode_metrics = []

    for ep in range(num_episodes):
        print(f"\n{'=' * 60}")
        print(f"Episode {ep}")
        print(f"{'=' * 60}")

        data_file = _find_data_file(dataset_dir, ep)
        if not data_file:
            print(f"  [WARNING] No data file for episode {ep}, skipping")
            continue
        table = pq.read_table(data_file)
        data = {col: table[col].to_pylist() for col in table.column_names}
        n_frames = len(data["timestamp"])

        # Load video frames
        video_file = _find_video_file(dataset_dir, image_key, ep)
        if not video_file:
            print(f"  [WARNING] No video for episode {ep} ({image_key}), skipping")
            continue

        container = av.open(video_file)
        stream = container.streams.video[0]
        frames = [av_frame.to_ndarray(format="rgb24") for av_frame in container.decode(stream)]
        container.close()

        policy.reset()
        actions_predicted = []
        actions_ground_truth = []
        inference_times_list = []

        for step in range(min(n_frames - 1, len(frames))):
            state = np.array(data["observation.state"][step], dtype=np.float32)
            gt_action = np.array(data["action"][step], dtype=np.float32)
            image = frames[step]

            obs = {
                "observation.state": torch.from_numpy(state).float().unsqueeze(0).to(device),
                image_key: (torch.from_numpy(image).float().permute(2, 0, 1) / 255.0).unsqueeze(0).to(device),
            }

            t_start = time.time()
            with torch.inference_mode():
                action = policy.select_action(obs)
            t_inf = time.time() - t_start
            inference_times_list.append(t_inf)

            action_np = action.squeeze(0).cpu().numpy()
            actions_predicted.append(action_np)
            actions_ground_truth.append(gt_action)

        pred = np.array(actions_predicted)
        gt = np.array(actions_ground_truth)
        inf_times = np.array(inference_times_list)

        mse = float(np.mean((pred - gt) ** 2))
        mae = float(np.mean(np.abs(pred - gt)))
        per_dim_mae = np.mean(np.abs(pred - gt), axis=0)
        avg_inf_ms = float(np.mean(inf_times) * 1000)
        throughput = float(1.0 / np.mean(inf_times))

        print(f"  Steps: {len(pred)}, MSE: {mse:.6f}, MAE: {mae:.6f}")
        print(f"  Avg inference: {avg_inf_ms:.1f}ms, Throughput: {throughput:.1f} Hz")

        ep_metrics = {
            "episode": ep,
            "steps": len(pred),
            "mse": mse,
            "mae": mae,
            "avg_inference_ms": avg_inf_ms,
            "throughput_hz": throughput,
            "per_dim_mae": per_dim_mae.tolist(),
        }
        all_episode_metrics.append(ep_metrics)

        npz_path = output_dir / f"ep{ep:03d}_predictions.npz"
        np.savez(npz_path, predicted=pred, ground_truth=gt, inference_times=inf_times)

        if mlflow_enable:
            import matplotlib
            import mlflow

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            mlflow.log_metrics(
                {
                    f"ep{ep}_mse": mse,
                    f"ep{ep}_mae": mae,
                    f"ep{ep}_avg_inference_ms": avg_inf_ms,
                    f"ep{ep}_throughput_hz": throughput,
                }
            )

            for plot_fn, name in [
                (plot_action_deltas, "action_deltas"),
                (plot_cumulative_positions, "cumulative_positions"),
                (plot_error_heatmap, "error_heatmap"),
            ]:
                fig = plot_fn(pred, gt, ep, fps)
                mlflow.log_figure(fig, f"plots/episode_{ep:03d}/{name}.png")
                plt.close(fig)

            fig = plot_summary_panel(pred, gt, inf_times, ep, fps)
            mlflow.log_figure(fig, f"plots/episode_{ep:03d}/summary_panel.png")
            plt.close(fig)

            mlflow.log_artifact(str(npz_path), "predictions")
            print(f"  Logged 4 plots + metrics to MLflow for episode {ep}")

    # Aggregate metrics
    if all_episode_metrics:
        agg_mse = float(np.mean([m["mse"] for m in all_episode_metrics]))
        agg_mae = float(np.mean([m["mae"] for m in all_episode_metrics]))
        agg_inf_ms = float(np.mean([m["avg_inference_ms"] for m in all_episode_metrics]))
        agg_throughput = float(np.mean([m["throughput_hz"] for m in all_episode_metrics]))
    else:
        agg_mse = agg_mae = agg_inf_ms = agg_throughput = 0.0

    results = {
        "job_name": job_name,
        "policy_repo_id": policy_repo_id,
        "policy_type": policy_type,
        "dataset_repo_id": dataset_repo_id,
        "device": str(device),
        "episodes_evaluated": len(all_episode_metrics),
        "aggregate_mse": agg_mse,
        "aggregate_mae": agg_mae,
        "aggregate_avg_inference_ms": agg_inf_ms,
        "aggregate_throughput_hz": agg_throughput,
        "per_episode": all_episode_metrics,
        "status": "completed",
    }

    results_path = output_dir / "eval_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[INFO] Results saved to: {results_path}")

    if mlflow_enable:
        import mlflow

        mlflow.log_metrics(
            {
                "aggregate_mse": agg_mse,
                "aggregate_mae": agg_mae,
                "aggregate_avg_inference_ms": agg_inf_ms,
                "aggregate_throughput_hz": agg_throughput,
            }
        )
        mlflow.log_artifact(str(results_path))

        if len(all_episode_metrics) >= 2:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig = plot_aggregate_summary(all_episode_metrics)
            mlflow.log_figure(fig, "plots/aggregate_summary.png")
            plt.close(fig)
            print("[INFO] Logged aggregate summary plot to MLflow")

        mlflow.end_run()
        print("[INFO] MLflow run completed with plots and metrics")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[ERROR] Evaluation failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
