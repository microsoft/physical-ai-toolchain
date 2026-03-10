#!/usr/bin/env python3
"""Run local LeRobot ACT inference evaluation.

Evaluates a trained ACT policy against dataset episodes by replaying
observations and comparing predicted actions to ground truth. Generates
per-episode trajectory plots and aggregate metrics.

Supports local checkpoints, AzureML model registry, or HuggingFace Hub.

Usage:
    # Local checkpoint against local blob-downloaded dataset
    python scripts/run-local-lerobot-inference.py \
        --policy-path tmp/lerobot-checkpoint/houston-lerobot-act/pretrained_model \
        --dataset-dir tmp/houston_lerobot/houston_lerobot \
        --episodes 5 --output-dir outputs/local-eval

    # Download from AzureML model registry
    python scripts/run-local-lerobot-inference.py \
        --model-name hex-pickup-act --model-version 3 \
        --dataset-dir /path/to/hexagon_lerobot \
        --episodes 10

    # HuggingFace repo
    python scripts/run-local-lerobot-inference.py \
        --policy-path user/trained-act \
        --dataset-dir /path/to/dataset
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch


def resolve_device(requested: str) -> str:
    if requested == "cuda" and torch.cuda.is_available():
        return "cuda"
    if requested in ("cuda", "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def find_data_file(dataset_dir: str, ep_idx: int, info: dict) -> str | None:
    chunks_size = info.get("chunks_size", 1000)
    ep_chunk = ep_idx // chunks_size
    candidates = [
        os.path.join(dataset_dir, "data", f"chunk-{ep_chunk:03d}", f"episode_{ep_idx:06d}.parquet"),
        os.path.join(dataset_dir, "data", f"chunk-{ep_idx:03d}", f"file-{ep_idx:03d}.parquet"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def find_video_file(dataset_dir: str, video_key: str, ep_idx: int, info: dict) -> str | None:
    chunks_size = info.get("chunks_size", 1000)
    ep_chunk = ep_idx // chunks_size
    candidates = [
        os.path.join(dataset_dir, "videos", video_key, f"chunk-{ep_chunk:03d}", f"episode_{ep_idx:06d}.mp4"),
        os.path.join(dataset_dir, "videos", video_key, f"chunk-{ep_idx:03d}", f"file-{ep_idx:03d}.mp4"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def load_video_frames(video_path: str) -> list[np.ndarray]:
    import av

    container = av.open(video_path)
    stream = container.streams.video[0]
    frames = [f.to_ndarray(format="rgb24") for f in container.decode(stream)]
    container.close()
    return frames


def download_aml_model(model_name: str, model_version: str) -> Path:
    from azure.ai.ml import MLClient
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    client = MLClient(
        credential,
        os.environ["AZURE_SUBSCRIPTION_ID"],
        os.environ["AZURE_RESOURCE_GROUP"],
        os.environ["AZUREML_WORKSPACE_NAME"],
    )

    download_dir = Path("tmp/aml-model-download")
    download_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {model_name}:{model_version} from AzureML...")
    client.models.download(name=model_name, version=model_version, download_path=str(download_dir))

    model_path = download_dir / model_name
    if not model_path.exists():
        model_path = download_dir

    for candidate in [model_path] + (list(model_path.iterdir()) if model_path.is_dir() else []):
        if candidate.is_dir() and (list(candidate.glob("*.safetensors")) or list(candidate.glob("*.bin"))):
            model_path = candidate
            break

    print(f"Model downloaded to: {model_path}")
    return model_path


def _load_normalizer_stats(policy: torch.nn.Module, model_dir: Path) -> None:
    """Load normalization stats from preprocessor/postprocessor safetensors into policy buffers.

    Older LeRobot checkpoints store stats in separate processor files rather than
    in the model weights. This bridges the gap by reading those files and populating
    the policy's normalize_inputs/unnormalize_outputs buffers.
    """
    import glob

    import safetensors.torch as st

    # Collect stats from all processor safetensors
    stats: dict[str, torch.Tensor] = {}
    for sf in sorted(glob.glob(str(model_dir / "*.safetensors"))):
        if "processor" in Path(sf).name:
            stats.update(st.load_file(sf))

    if not stats:
        return

    # Map processor stat keys to buffer names
    # Processor format: "observation.state.mean" -> buffer: "normalize_inputs.buffer_observation_state.mean"
    def _to_buffer_name(stat_key: str, module_prefix: str) -> str:
        parts = stat_key.rsplit(".", 1)
        feature_name = parts[0].replace(".", "_")
        stat_type = parts[1]
        return f"{module_prefix}.buffer_{feature_name}.{stat_type}"

    loaded = 0
    state_dict = policy.state_dict()

    for stat_key, tensor in stats.items():
        parts = stat_key.rsplit(".", 1)
        if len(parts) != 2:
            continue
        _feature, stat_type = parts
        if stat_type not in ("mean", "std", "min", "max"):
            continue

        # Try normalize_inputs for observation.* keys and action keys
        for module_prefix in ("normalize_inputs", "normalize_targets", "unnormalize_outputs"):
            buf_name = _to_buffer_name(stat_key, module_prefix)
            if buf_name in state_dict:
                state_dict[buf_name] = tensor
                loaded += 1

    if loaded > 0:
        policy.load_state_dict(state_dict, strict=False)
        print(f"  Loaded {loaded} normalizer stats from preprocessor files")


def run_evaluation(args: argparse.Namespace) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from lerobot.policies.act.modeling_act import ACTPolicy

    device = resolve_device(args.device)
    print(f"Device: {device}")

    # Resolve policy path
    if args.model_name and args.model_version:
        policy_path = str(download_aml_model(args.model_name, args.model_version))
    else:
        policy_path = args.policy_path

    # Load policy
    print(f"Loading policy from: {policy_path}")

    # Strip incompatible config fields from older checkpoints
    config_path = Path(policy_path) / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
        strip_fields = ["use_peft", "pretrained_path", "peft_config"]
        removed = [k for k in strip_fields if k in cfg]
        if removed:
            for k in removed:
                del cfg[k]
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
            print(f"  Stripped incompatible config fields: {removed}")

    t0 = time.time()
    policy = ACTPolicy.from_pretrained(policy_path)

    # Load normalization stats from preprocessor files if normalizer buffers are missing
    _load_normalizer_stats(policy, Path(policy_path))

    policy.to(device)
    print(f"  Loaded in {time.time() - t0:.1f}s ({sum(p.numel() for p in policy.parameters()) / 1e6:.1f}M params)")

    # Load dataset info
    info_path = os.path.join(args.dataset_dir, "meta", "info.json")
    with open(info_path) as f:
        info = json.load(f)
    fps = info["fps"]

    features = info.get("features", {})
    video_keys = [k for k, v in features.items() if v.get("dtype") in ("video", "image")]
    image_key = video_keys[0] if video_keys else "observation.images.color"

    action_dim = features.get("action", {}).get("shape", [0])[0]
    state_dim = features.get("observation.state", {}).get("shape", [0])[0]
    print(f"  state_dim={state_dim}, action_dim={action_dim}, image_key={image_key}, fps={fps}")

    # Determine episodes
    episodes_meta = os.path.join(args.dataset_dir, "meta", "episodes.jsonl")
    if os.path.exists(episodes_meta):
        with open(episodes_meta) as f:
            total_episodes = sum(1 for _ in f)
    else:
        total_episodes = info.get("total_episodes", args.episodes)
    num_episodes = min(args.episodes, total_episodes)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plots_dir = output_dir / "plots"
    plots_dir.mkdir(exist_ok=True)

    all_metrics = []

    for ep in range(num_episodes):
        print(f"\n{'=' * 60}")
        print(f"Episode {ep}")
        print(f"{'=' * 60}")

        data_file = find_data_file(args.dataset_dir, ep, info)
        if not data_file:
            print(f"  [SKIP] No data file for episode {ep}")
            continue

        table = pq.read_table(data_file)
        data = {col: table[col].to_pylist() for col in table.column_names}
        n_frames = len(data["timestamp"])

        video_file = find_video_file(args.dataset_dir, image_key, ep, info)
        if not video_file:
            print(f"  [SKIP] No video for episode {ep} ({image_key})")
            continue

        frames = load_video_frames(video_file)

        policy.reset()
        actions_predicted = []
        actions_ground_truth = []
        inference_times = []

        num_steps = min(n_frames - 1, len(frames))
        for step in range(num_steps):
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
            inference_times.append(t_inf)

            action_np = action.squeeze(0).cpu().numpy()
            actions_predicted.append(action_np)
            actions_ground_truth.append(gt_action)

            if step < 3 or step == num_steps - 1:
                print(
                    f"  step {step:3d}: pred=[{', '.join(f'{a:7.3f}' for a in action_np[:6])}]  ({t_inf * 1000:.1f}ms)"
                )

        pred = np.array(actions_predicted)
        gt = np.array(actions_ground_truth)
        inf_times = np.array(inference_times)

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
        all_metrics.append(ep_metrics)

        # Save predictions
        np.savez(output_dir / f"ep{ep:03d}_predictions.npz", predicted=pred, ground_truth=gt, inference_times=inf_times)

        # Plot: action deltas
        n_dims = pred.shape[1]
        dim_labels = [f"dim_{j}" for j in range(n_dims)]
        t = np.arange(len(pred)) / fps

        fig, axes = plt.subplots(min(n_dims, 6), 1, figsize=(14, 2.5 * min(n_dims, 6)), sharex=True)
        fig.suptitle(f"Episode {ep} — Action Deltas: Predicted vs Ground Truth", fontsize=14, fontweight="bold")
        if min(n_dims, 6) == 1:
            axes = [axes]
        for j, ax in enumerate(axes):
            ax.plot(t, gt[:, j], color="#2196F3", alpha=0.8, linewidth=1.2, label="Ground Truth")
            ax.plot(t, pred[:, j], color="#FF5722", alpha=0.8, linewidth=1.2, label="Predicted")
            ax.fill_between(t, gt[:, j], pred[:, j], alpha=0.15, color="#9C27B0")
            ax.set_ylabel(dim_labels[j], fontsize=9)
            ax.grid(True, alpha=0.3)
            if j == 0:
                ax.legend(loc="upper right", fontsize=8)
        axes[-1].set_xlabel("Time (s)", fontsize=10)
        fig.tight_layout()
        fig.savefig(plots_dir / f"ep{ep:03d}_action_deltas.png", dpi=150)
        plt.close(fig)

        # Plot: summary panel
        error = np.abs(pred - gt)
        colors = plt.cm.tab10(np.linspace(0, 1, n_dims))
        fig, axes_panel = plt.subplots(2, 2, figsize=(14, 8))
        fig.suptitle(f"Episode {ep} — Inference Summary", fontsize=14, fontweight="bold")

        ax = axes_panel[0, 0]
        for j in range(n_dims):
            ax.plot(t, gt[:, j], color=colors[j], alpha=0.6, linewidth=1.0)
            ax.plot(t, pred[:, j], color=colors[j], alpha=0.6, linewidth=1.0, linestyle="--")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Action delta")
        ax.set_title("All Dimensions (solid=GT, dashed=pred)")
        ax.grid(True, alpha=0.3)

        ax = axes_panel[0, 1]
        ax.boxplot([error[:, j] for j in range(n_dims)], tick_labels=dim_labels, patch_artist=True)
        ax.set_ylabel("Absolute Error")
        ax.set_title("Error Distribution per Dimension")
        ax.tick_params(axis="x", rotation=45, labelsize=6 if n_dims > 8 else 8)
        ax.grid(True, alpha=0.3, axis="y")

        ax = axes_panel[1, 0]
        inf_ms = inf_times * 1000
        ax.plot(inf_ms, color="#4CAF50", alpha=0.7, linewidth=0.8)
        ax.axhline(y=1000 / fps, color="#F44336", linestyle="--", alpha=0.7, label=f"Realtime ({1000 / fps:.1f}ms)")
        ax.set_xlabel("Step")
        ax.set_ylabel("Inference time (ms)")
        ax.set_title("Inference Latency")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

        ax = axes_panel[1, 1]
        bars = ax.bar(dim_labels, per_dim_mae, color=colors[:n_dims], alpha=0.7)
        ax.set_ylabel("MAE")
        ax.set_title("Per-Dimension MAE")
        ax.tick_params(axis="x", rotation=45, labelsize=6 if n_dims > 8 else 8)
        ax.grid(True, alpha=0.3, axis="y")
        for bar, val in zip(bars, per_dim_mae, strict=False):
            ax.text(
                bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.4f}", ha="center", va="bottom", fontsize=7
            )

        fig.tight_layout()
        fig.savefig(plots_dir / f"ep{ep:03d}_summary.png", dpi=150)
        plt.close(fig)

        print(f"  Saved plots to {plots_dir}/ep{ep:03d}_*.png")

    # Aggregate
    if not all_metrics:
        print("\nNo episodes evaluated.")
        return

    agg_mse = float(np.mean([m["mse"] for m in all_metrics]))
    agg_mae = float(np.mean([m["mae"] for m in all_metrics]))
    agg_inf_ms = float(np.mean([m["avg_inference_ms"] for m in all_metrics]))
    agg_throughput = float(np.mean([m["throughput_hz"] for m in all_metrics]))

    results = {
        "policy_path": str(policy_path),
        "dataset_dir": str(args.dataset_dir),
        "device": device,
        "episodes_evaluated": len(all_metrics),
        "aggregate_mse": agg_mse,
        "aggregate_mae": agg_mae,
        "aggregate_avg_inference_ms": agg_inf_ms,
        "aggregate_throughput_hz": agg_throughput,
        "per_episode": all_metrics,
    }

    results_path = output_dir / "eval_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'=' * 60}")
    print("Aggregate Results")
    print(f"{'=' * 60}")
    print(f"  Episodes:      {len(all_metrics)}")
    print(f"  MSE:           {agg_mse:.6f}")
    print(f"  MAE:           {agg_mae:.6f}")
    print(f"  Avg inference: {agg_inf_ms:.1f}ms")
    print(f"  Throughput:    {agg_throughput:.1f} Hz")
    print(f"  Results:       {results_path}")
    print(f"  Plots:         {plots_dir}/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    policy_group = parser.add_argument_group("Policy source (choose one)")
    policy_group.add_argument("--policy-path", help="Local path or HuggingFace repo ID")
    policy_group.add_argument("--model-name", help="AzureML model registry name")
    policy_group.add_argument("--model-version", help="AzureML model registry version")

    parser.add_argument("--dataset-dir", required=True, help="Path to LeRobot dataset root")
    parser.add_argument("--episodes", type=int, default=5, help="Number of episodes to evaluate (default: 5)")
    parser.add_argument(
        "--output-dir", default="outputs/local-eval", help="Output directory (default: outputs/local-eval)"
    )
    parser.add_argument(
        "--device", default="cpu", choices=["cuda", "cpu", "mps"], help="Inference device (default: cpu)"
    )

    args = parser.parse_args()

    if not args.policy_path and not (args.model_name and args.model_version):
        parser.error("Either --policy-path or --model-name/--model-version is required")

    if not os.path.isdir(args.dataset_dir):
        print(f"Dataset directory not found: {args.dataset_dir}")
        sys.exit(1)

    run_evaluation(args)


if __name__ == "__main__":
    main()
