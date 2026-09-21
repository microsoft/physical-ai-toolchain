"""LeRobot replay-based inference evaluation."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch

_EVALUATION_ROOT = Path(__file__).resolve().parents[2]
if str(_EVALUATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVALUATION_ROOT))

from sil.hf_revision import resolve_hf_revision  # noqa: E402

JOINT_NAMES: list[str] = []


_EVALUATION_SCHEMA_VERSION = 1
_VERDICT_PASS = "pass"
_VERDICT_SKIPPED = "skipped"
_BASELINE_NONE = "none"

_TOOLCHAIN_TO_VLA_METRIC = {
    "mse": "action_accuracy_l2",
    "mae": "action_accuracy_l1",
    "avg_inference_ms": "inference_latency_mean_ms",
    "throughput_hz": "throughput_hz",
}


def _write_vla_schema_v1(
    output_dir: Path,
    aggregate: dict[str, float],
    per_episode: list[dict],
    dataset_repo_id: str,
    policy_repo_id: str,
) -> None:
    """Emit evaluation_schema_version=1 artifacts alongside eval_results.json.

    The toolchain has no gate / threshold / baseline system (governance was
    explicitly removed during the upstream port), so every metric is emitted
    with absolute_threshold=inf, absolute_verdict=pass, baseline_value=null,
    regression_verdict=skipped. metrics.json carries the aggregate verdict;
    failure_cases.jsonl is empty unless an episode raised a rollout_error
    during the inference loop.
    """
    metrics_payload = {
        "evaluation_schema_version": _EVALUATION_SCHEMA_VERSION,
        "aggregate_verdict": _VERDICT_PASS,
        "baseline_model_version": _BASELINE_NONE,
        "metrics": [
            {
                "name": _TOOLCHAIN_TO_VLA_METRIC.get(toolchain_name, toolchain_name),
                "value": float(value),
                "absolute_threshold": float("inf"),
                "absolute_verdict": _VERDICT_PASS,
                "baseline_value": None,
                "regression_pct": 0.0,
                "regression_verdict": _VERDICT_SKIPPED,
            }
            for toolchain_name, value in aggregate.items()
        ],
    }

    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics_payload, f, indent=2)
    print(f"[INFO] VLA schema v1 metrics: {metrics_path}")

    failure_cases_path = output_dir / "failure_cases.jsonl"
    with open(failure_cases_path, "w") as f:
        for episode in per_episode:
            if not episode.get("rollout_error"):
                continue
            record = {
                "evaluation_schema_version": _EVALUATION_SCHEMA_VERSION,
                "episode_id": str(episode.get("episode", "unknown")),
                "dataset_id": dataset_repo_id,
                "dataset_version": "unknown",
                "domain_category": None,
                "model_version": policy_repo_id,
                "artifact_refs": [],
                "failure_mode": "rollout_error",
                "metric_values": {k: v for k, v in episode.items() if isinstance(v, (int, float))},
                "metric_thresholds_violated": [],
            }
            f.write(json.dumps(record) + "\n")
    print(f"[INFO] VLA schema v1 failure cases: {failure_cases_path}")


def _setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

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
    ax.boxplot([error[:, j] for j in range(n_joints)], tick_labels=labels, patch_artist=True)
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
    for bar, val in zip(bars, per_joint_mae, strict=False):
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
    ax.bar(ep_labels, mses, color="#9C27B0", alpha=0.7)
    ax.axhline(y=np.mean(mses), color="#F44336", linestyle="--", alpha=0.7, label=f"Mean: {np.mean(mses):.7f}")
    ax.set_xlabel("Episode")
    ax.set_ylabel("MSE")
    ax.set_title("Per-Episode MSE")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    return fig


def _load_episode_records(ds_dir: str) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    episodes_dir = Path(ds_dir) / "meta" / "episodes"
    records = []
    for path in sorted(episodes_dir.glob("chunk-*/file-*.parquet")):
        records.extend(pq.read_table(path).to_pylist())
    return records


def _find_episode_record(
    episode_records: list[dict[str, Any]],
    ep_idx: int,
) -> dict[str, Any] | None:
    return next(
        (record for record in episode_records if record.get("episode_index") == ep_idx),
        None,
    )


def _find_data_file(
    ds_dir: str,
    ep_idx: int,
    ds_info: dict[str, Any],
    episode_records: list[dict[str, Any]],
) -> tuple[str, int, int | None] | None:
    episode_record = _find_episode_record(episode_records, ep_idx)
    data_path_template = ds_info.get("data_path")
    if episode_record and isinstance(data_path_template, str):
        chunk_index = int(episode_record["data/chunk_index"])
        file_index = int(episode_record["data/file_index"])
        path = Path(ds_dir) / data_path_template.format(
            chunk_index=chunk_index,
            file_index=file_index,
        )
        matching_records = [
            record
            for record in episode_records
            if record.get("data/chunk_index") == chunk_index
            and record.get("data/file_index") == file_index
        ]
        file_start = min(int(record["dataset_from_index"]) for record in matching_records)
        row_start = int(episode_record["dataset_from_index"]) - file_start
        row_end = int(episode_record["dataset_to_index"]) - file_start
        if path.exists():
            return str(path), row_start, row_end

    chunks_size = ds_info.get("chunks_size", 1000)
    ep_chunk = ep_idx // chunks_size
    candidates = [
        os.path.join(ds_dir, "data", f"chunk-{ep_chunk:03d}", f"episode_{ep_idx:06d}.parquet"),
        os.path.join(ds_dir, "data", f"chunk-{ep_idx:03d}", f"file-{ep_idx:03d}.parquet"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c, 0, None
    return None


def _find_video_file(
    ds_dir: str,
    video_key: str,
    ep_idx: int,
    ds_info: dict[str, Any],
    episode_records: list[dict[str, Any]],
) -> str | None:
    episode_record = _find_episode_record(episode_records, ep_idx)
    video_path_template = ds_info.get("video_path")
    chunk_key = f"videos/{video_key}/chunk_index"
    file_key = f"videos/{video_key}/file_index"
    if (
        episode_record
        and isinstance(video_path_template, str)
        and chunk_key in episode_record
        and file_key in episode_record
    ):
        path = Path(ds_dir) / video_path_template.format(
            video_key=video_key,
            chunk_index=int(episode_record[chunk_key]),
            file_index=int(episode_record[file_key]),
        )
        if path.exists():
            return str(path)

    chunks_size = ds_info.get("chunks_size", 1000)
    ep_chunk = ep_idx // chunks_size
    candidates = [
        os.path.join(ds_dir, "videos", video_key, f"chunk-{ep_chunk:03d}", f"episode_{ep_idx:06d}.mp4"),
        os.path.join(ds_dir, "videos", video_key, f"chunk-{ep_chunk:03d}", f"file-{ep_idx:03d}.mp4"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _load_policy(
    policy_repo_id: str,
    policy_revision: str | None,
    device: torch.device,
):
    from lerobot.configs import PreTrainedConfig
    from lerobot.policies import get_policy_class, make_pre_post_processors

    policy_config = PreTrainedConfig.from_pretrained(
        policy_repo_id,
        revision=policy_revision,
    )
    policy_config.device = str(device)
    policy_class = get_policy_class(policy_config.type)
    policy = policy_class.from_pretrained(
        policy_repo_id,
        config=policy_config,
        revision=policy_revision,
    )
    policy.to(device)
    policy.eval()
    preprocessor, postprocessor = make_pre_post_processors(
        policy_cfg=policy_config,
        pretrained_path=policy_repo_id,
        pretrained_revision=policy_revision,
        preprocessor_overrides={"device_processor": {"device": str(device)}},
    )
    return policy_config.type, policy, preprocessor, postprocessor


def _prepare_observation(
    state: np.ndarray,
    images: dict[str, np.ndarray],
    device: torch.device,
    task: str,
) -> dict:
    from lerobot.policies.utils import prepare_observation_for_inference

    observation = {"observation.state": state, **images}
    return prepare_observation_for_inference(observation, device, task=task)


def main() -> int:
    global JOINT_NAMES

    import av
    import pyarrow.parquet as pq

    policy_repo_id = os.environ.get("POLICY_REPO_ID", "").strip()
    policy_type = os.environ.get("POLICY_TYPE", "act")
    dataset_repo_id = os.environ.get("DATASET_REPO_ID", "")
    task = os.environ.get("TASK_PROMPT", "").strip()
    policy_revision = os.environ.get("POLICY_REVISION", "").strip() or None
    dataset_revision = os.environ.get("DATASET_REVISION") or None
    eval_episodes = int(os.environ.get("EVAL_EPISODES", "10"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/workspace/outputs/eval"))
    job_name = os.environ.get("JOB_NAME", "lerobot-eval")
    mlflow_enable = os.environ.get("MLFLOW_ENABLE", "false") == "true"

    if not policy_repo_id:
        print("[ERROR] POLICY_REPO_ID is required")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[INFO] Using device: {device}")

    # Resolve dataset source
    dataset_dir_env = os.environ.get("DATASET_DIR", "")
    if dataset_dir_env and os.path.isdir(dataset_dir_env):
        dataset_dir = dataset_dir_env
        print(f"[INFO] Using blob-downloaded dataset: {dataset_dir}")
    elif dataset_repo_id and dataset_repo_id != "none":
        try:
            dataset_revision = resolve_hf_revision(dataset_repo_id, dataset_revision, revision_name="DATASET_REVISION")
        except ValueError as exc:
            print(f"[ERROR] {exc}")
            return 1
        from huggingface_hub import snapshot_download

        dataset_dir = snapshot_download(repo_id=dataset_repo_id, repo_type="dataset", revision=dataset_revision)
        print(f"[INFO] Dataset downloaded from HuggingFace: {dataset_dir}")
    else:
        print("[ERROR] Dataset source required: set DATASET_REPO_ID or blob storage params")
        return 1

    # Load dataset info
    with open(os.path.join(dataset_dir, "meta", "info.json")) as f:
        info = json.load(f)
    fps = info["fps"]
    episode_records = _load_episode_records(dataset_dir)

    # Resolve dimension labels from the dataset's action feature names so plots
    # carry real joint names; fall back to generic dim_N labels otherwise.
    action_names = info.get("features", {}).get("action", {}).get("names")
    JOINT_NAMES = list(action_names) if isinstance(action_names, list) else []

    # Identify video keys from features
    features = info.get("features", {})
    video_keys = [k for k, v in features.items() if v.get("dtype") in ("video", "image")]
    image_keys = [key for key in video_keys if key.startswith("observation.images.")] or video_keys
    if not image_keys:
        print("[ERROR] Dataset has no video or image observation features")
        return 1

    # Load policy and its saved normalization/tokenization processors.
    print(f"[INFO] Loading policy from: {policy_repo_id}")
    try:
        policy_revision = resolve_hf_revision(policy_repo_id, policy_revision, revision_name="POLICY_REVISION")
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1
    loaded_policy_type, policy, preprocessor, postprocessor = _load_policy(
        policy_repo_id,
        policy_revision,
        device,
    )
    if loaded_policy_type != policy_type:
        print(
            f"[ERROR] Requested policy type {policy_type!r} does not match "
            f"checkpoint type {loaded_policy_type!r}"
        )
        return 1
    if loaded_policy_type in {"pi0", "pi0_fast", "pi05"} and not task:
        print("[ERROR] TASK_PROMPT is required for PI-family policy evaluation")
        return 1

    # Determine episode range
    episodes_meta_path = os.path.join(dataset_dir, "meta", "episodes.jsonl")
    if os.path.exists(episodes_meta_path):
        with open(episodes_meta_path) as f:
            total_episodes = sum(1 for _ in f)
    elif episode_records:
        total_episodes = len(episode_records)
    else:
        total_episodes = int(info.get("total_episodes", eval_episodes))
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
                "task": task,
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

        data_source = _find_data_file(dataset_dir, ep, info, episode_records)
        if not data_source:
            print(f"  [WARNING] No data file for episode {ep}, skipping")
            continue
        data_file, row_start, row_end = data_source
        table = pq.read_table(data_file)
        if row_end is not None:
            table = table.slice(row_start, row_end - row_start)
        data = {col: table[col].to_pylist() for col in table.column_names}
        n_frames = len(data["timestamp"])

        # Load all camera streams used by the policy.
        video_files = {
            key: _find_video_file(dataset_dir, key, ep, info, episode_records)
            for key in image_keys
        }
        missing_video_keys = [key for key, path in video_files.items() if not path]
        if missing_video_keys:
            print(f"  [WARNING] Missing videos for episode {ep}: {', '.join(missing_video_keys)}, skipping")
            continue
        frames_by_key = {}
        for image_key, video_file in video_files.items():
            container = av.open(video_file)
            stream = container.streams.video[0]
            frames_by_key[image_key] = [
                av_frame.to_ndarray(format="rgb24") for av_frame in container.decode(stream)
            ]
            container.close()

        policy.reset()
        preprocessor.reset()
        postprocessor.reset()
        actions_predicted = []
        actions_ground_truth = []
        inference_times_list = []

        available_frames = min(len(frames) for frames in frames_by_key.values())
        for step in range(min(n_frames - 1, available_frames)):
            state = np.array(data["observation.state"][step], dtype=np.float32)
            gt_action = np.array(data["action"][step], dtype=np.float32)
            images = {key: frames[step] for key, frames in frames_by_key.items()}
            obs = _prepare_observation(state, images, device, task)

            t_start = time.time()
            with torch.inference_mode():
                obs = preprocessor(obs)
                action = policy.select_action(obs)
                action = postprocessor(action)
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

    if not all_episode_metrics:
        if mlflow_enable:
            import mlflow

            mlflow.end_run(status="FAILED")
        raise RuntimeError("Evaluation produced no episode metrics")

    # Aggregate metrics
    agg_mse = float(np.mean([m["mse"] for m in all_episode_metrics]))
    agg_mae = float(np.mean([m["mae"] for m in all_episode_metrics]))
    agg_inf_ms = float(np.mean([m["avg_inference_ms"] for m in all_episode_metrics]))
    agg_throughput = float(np.mean([m["throughput_hz"] for m in all_episode_metrics]))

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

    _write_vla_schema_v1(
        output_dir=output_dir,
        aggregate={
            "mse": agg_mse,
            "mae": agg_mae,
            "avg_inference_ms": agg_inf_ms,
            "throughput_hz": agg_throughput,
        },
        per_episode=all_episode_metrics,
        dataset_repo_id=dataset_repo_id,
        policy_repo_id=policy_repo_id,
    )

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
