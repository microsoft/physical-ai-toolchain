#!/usr/bin/env python3
"""Test inference with a trained LeRobot ACT policy.

Loads a trained policy from HuggingFace Hub (or local checkpoint), feeds real
observations from the dataset, and validates that the model produces
reasonable action outputs. Supports both single-step and multi-step (action
chunk) inference.

Usage:
    python scripts/test-lerobot-inference.py \\
        --policy-repo alizaidi/hve-robo-act-train \\
        --dataset-dir /path/to/hve-robo-cell \\
        --device cuda

    python scripts/test-lerobot-inference.py \\
        --policy-repo alizaidi/hve-robo-act-train \\
        --episode 0 --num-steps 30
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pyarrow.parquet as pq
import torch


def load_video_frame(dataset_dir: str, episode: int, frame: int) -> np.ndarray:
    """Extract a single frame from an episode video using av."""
    import av

    video_path = os.path.join(
        dataset_dir,
        "videos",
        "observation.images.color",
        f"chunk-{episode:03d}",
        f"file-{episode:03d}.mp4",
    )
    container = av.open(video_path)
    stream = container.streams.video[0]
    for i, av_frame in enumerate(container.decode(stream)):
        if i == frame:
            img = av_frame.to_ndarray(format="rgb24")
            container.close()
            return img
    container.close()
    raise IndexError(f"Frame {frame} not found in {video_path}")


def load_episode_data(dataset_dir: str, episode: int) -> dict:
    """Load parquet data for a specific episode."""
    data_path = os.path.join(dataset_dir, "data", f"chunk-{episode:03d}", f"file-{episode:03d}.parquet")
    table = pq.read_table(data_path)
    return {col: table[col].to_pylist() for col in table.column_names}


def build_observation(state: np.ndarray, image: np.ndarray) -> dict[str, torch.Tensor]:
    """Construct observation dict from raw numpy arrays.

    Returns unbatched tensors; the preprocessor handles batching, device
    transfer, and normalization.
    """
    return {
        "observation.state": torch.from_numpy(state).float(),
        "observation.images.color": (torch.from_numpy(image).float().permute(2, 0, 1) / 255.0),
    }


def resolve_device(requested: str) -> str:
    """Resolve the best available device."""
    if requested == "cuda" and torch.cuda.is_available():
        return "cuda"
    if requested in ("cuda", "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def run_inference_test(args: argparse.Namespace) -> None:
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.processor.pipeline import PolicyProcessorPipeline

    device = resolve_device(args.device)
    print(f"Using device: {device}")

    # Load policy
    print(f"Loading policy from: {args.policy_repo}")
    t0 = time.time()
    policy = ACTPolicy.from_pretrained(args.policy_repo)
    policy.to(device)
    load_time = time.time() - t0
    print(f"  loaded in {load_time:.1f}s ({sum(p.numel() for p in policy.parameters()) / 1e6:.1f}M params)")

    # Load pre/post processors for normalization
    device_override = {"device_processor": {"device": device}}
    preprocessor = PolicyProcessorPipeline.from_pretrained(
        args.policy_repo,
        "policy_preprocessor.json",
        overrides=device_override,
    )
    postprocessor = PolicyProcessorPipeline.from_pretrained(
        args.policy_repo,
        "policy_postprocessor.json",
        overrides=device_override,
    )
    print(f"  preprocessor: {[type(s).__name__ for s in preprocessor.steps]}")
    print(f"  postprocessor: {[type(s).__name__ for s in postprocessor.steps]}")

    # Load dataset info
    info_path = os.path.join(args.dataset_dir, "meta", "info.json")
    with open(info_path) as f:
        info = json.load(f)
    fps = info["fps"]
    action_dim = info["features"]["action"]["shape"][0]
    state_dim = info["features"]["observation.state"]["shape"][0]
    img_shape = info["features"]["observation.images.color"]["shape"]
    print(f"  state_dim={state_dim}, action_dim={action_dim}, image={img_shape}, fps={fps}")

    # Load episode data
    episode = args.episode
    data = load_episode_data(args.dataset_dir, episode)
    n_frames = len(data["timestamp"])
    start = args.start_frame
    num_steps = min(args.num_steps, n_frames - start - 1)
    print(f"\nEpisode {episode}: {n_frames} frames, starting at frame {start}, testing {num_steps} steps")

    # Run inference loop
    policy.reset()
    actions_predicted = []
    actions_ground_truth = []
    inference_times = []

    for step in range(num_steps):
        frame_idx = start + step
        state = np.array(data["observation.state"][frame_idx], dtype=np.float32)
        gt_action = np.array(data["action"][frame_idx], dtype=np.float32)
        image = load_video_frame(args.dataset_dir, episode, frame_idx)

        obs = build_observation(state, image)
        obs = preprocessor(obs)

        t_start = time.time()
        with torch.inference_mode():
            action = policy.select_action(obs)
        t_inf = time.time() - t_start
        inference_times.append(t_inf)

        action = postprocessor({"action": action})
        action_np = action["action"].squeeze(0).cpu().numpy()
        actions_predicted.append(action_np)
        actions_ground_truth.append(gt_action)

        if step < 5 or step == num_steps - 1:
            print(
                f"  step {step:3d}: "
                f"pred=[{', '.join(f'{a:7.3f}' for a in action_np)}]  "
                f"gt=[{', '.join(f'{a:7.3f}' for a in gt_action)}]  "
                f"({t_inf * 1000:.1f}ms)"
            )

    # Compute metrics
    pred = np.array(actions_predicted)
    gt = np.array(actions_ground_truth)
    mse = np.mean((pred - gt) ** 2)
    mae = np.mean(np.abs(pred - gt))
    per_joint_mae = np.mean(np.abs(pred - gt), axis=0)

    avg_inf_ms = np.mean(inference_times) * 1000
    p95_inf_ms = np.percentile(inference_times, 95) * 1000
    throughput = 1.0 / np.mean(inference_times)

    print(f"\n{'=' * 60}")
    print("Inference Results")
    print(f"{'=' * 60}")
    print(f"  Steps evaluated:    {num_steps}")
    print(f"  MSE (all joints):   {mse:.6f}")
    print(f"  MAE (all joints):   {mae:.6f}")
    print(f"  Per-joint MAE:      [{', '.join(f'{m:.4f}' for m in per_joint_mae)}]")
    print(f"  Avg inference:      {avg_inf_ms:.1f}ms")
    print(f"  P95 inference:      {p95_inf_ms:.1f}ms")
    print(f"  Throughput:         {throughput:.1f} steps/s")
    print(f"  Realtime capable:   {'yes' if throughput >= fps else 'no'} (need {fps} Hz)")

    # Action range sanity check
    pred_range = np.ptp(pred, axis=0)
    gt_range = np.ptp(gt, axis=0)
    print(f"\n  Predicted range:    [{', '.join(f'{r:.3f}' for r in pred_range)}]")
    print(f"  Ground truth range: [{', '.join(f'{r:.3f}' for r in gt_range)}]")

    # Check for degenerate outputs
    if np.all(pred_range < 1e-4):
        print("\n  WARNING: Predicted actions have near-zero variance (mode collapse)")
    if np.any(np.isnan(pred)):
        print("\n  ERROR: NaN values in predicted actions")
    if np.any(np.isinf(pred)):
        print("\n  ERROR: Inf values in predicted actions")

    # Save predictions if requested
    if args.output:
        np.savez(
            args.output,
            predicted=pred,
            ground_truth=gt,
            inference_times=np.array(inference_times),
        )
        print(f"\n  Saved predictions to {args.output}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--policy-repo",
        default="alizaidi/hve-robo-act-train",
        help="HuggingFace repo ID or local path to trained policy",
    )
    parser.add_argument(
        "--dataset-dir",
        required=True,
        help="Path to LeRobot v3 dataset root (for test observations)",
    )
    parser.add_argument(
        "--episode",
        type=int,
        default=0,
        help="Episode index to use for test observations",
    )
    parser.add_argument(
        "--start-frame",
        type=int,
        default=0,
        help="Starting frame index within the episode",
    )
    parser.add_argument(
        "--num-steps",
        type=int,
        default=30,
        help="Number of inference steps to run",
    )
    parser.add_argument(
        "--device",
        default="cuda",
        choices=["cuda", "cpu", "mps"],
        help="Device for inference",
    )
    parser.add_argument(
        "--output",
        help="Path to save predictions (.npz)",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.dataset_dir):
        print(f"Dataset directory not found: {args.dataset_dir}")
        sys.exit(1)

    run_inference_test(args)


if __name__ == "__main__":
    main()
