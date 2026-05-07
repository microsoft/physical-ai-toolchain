"""Run inference with an optimized LeRobot policy produced by `optimize.py`.

Loads the policy from the local optimized directory (skipping any Azure ML
download), restores the recorded TorchInductor cache so compilation is reused,
and either:

* runs a single observation forward pass (default), printing the predicted
  action chunk; or
* runs a benchmark sweep when `--bench` is set.

The script can also load a directory not produced by `optimize.py`. In that
case it expects `pretrained_model/` to exist and falls back to default runtime
settings (`bf16` autocast, `max-autotune` compile).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--optimized-dir",
        type=Path,
        required=True,
        help="Output directory written by optimize.py (must contain pretrained_model/).",
    )
    parser.add_argument(
        "--device",
        default=None,
        choices=["cuda", "cpu"],
        help="Override device from optimization profile.",
    )
    parser.add_argument(
        "--dtype",
        default=None,
        choices=["bf16", "fp16", "fp32"],
        help="Override autocast dtype from optimization profile.",
    )
    parser.add_argument(
        "--compile-mode",
        default=None,
        choices=["max-autotune", "reduce-overhead", "default"],
        help="Override torch.compile mode from optimization profile.",
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Skip torch.compile (useful for verifying baseline correctness).",
    )
    parser.add_argument(
        "--bench",
        action="store_true",
        help="Run a benchmark sweep instead of a single forward pass.",
    )
    parser.add_argument(
        "--bench-iters",
        type=int,
        default=20,
        help="Benchmark iterations (default: 20). Ignored without --bench.",
    )
    parser.add_argument(
        "--warmup-iters",
        type=int,
        default=3,
        help="Warmup iterations before timing (default: 3).",
    )
    return parser.parse_args()


def _load_profile(optimized_dir: Path) -> dict[str, Any]:
    profile_path = optimized_dir / "optimization_profile.json"
    if profile_path.exists():
        return json.loads(profile_path.read_text())
    print(f"[WARN] No optimization_profile.json in {optimized_dir}; using defaults.")
    return {
        "device": "cuda",
        "dtype": "bf16",
        "compile_mode": "max-autotune",
        "inductor_cache_dir": str((optimized_dir / "inductor_cache").resolve()),
    }


def _resolve_pretrained_dir(optimized_dir: Path) -> Path:
    """Locate the directory containing config.json under `optimized_dir`."""
    if (optimized_dir / "config.json").exists():
        return optimized_dir
    for config_path in optimized_dir.rglob("config.json"):
        return config_path.parent
    raise SystemExit(
        f"[ERROR] Could not find config.json under {optimized_dir}. "
        "Run optimize.py first or pass the directory containing config.json."
    )


def _load_policy(checkpoint_dir: Path, device: str) -> Any:
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

    config = PreTrainedConfig.from_pretrained(str(checkpoint_dir))
    config.device = device
    print(f"[INFO] Loading policy from {checkpoint_dir} (type={config.type}, device={device})")
    return SmolVLAPolicy.from_pretrained(str(checkpoint_dir), config=config)


def _build_dummy_batch(policy: Any, device: str, task: str = "inference task") -> dict[str, Any]:
    import torch
    from transformers import AutoTokenizer

    from lerobot.utils.constants import OBS_LANGUAGE_ATTENTION_MASK, OBS_LANGUAGE_TOKENS

    config = policy.config
    batch_size = 1
    batch: dict[str, Any] = {}

    for key, feature in config.input_features.items():
        if hasattr(feature.type, "name") and feature.type.name in {"VISUAL", "STATE"}:
            shape = (batch_size, *feature.shape)
            batch[key] = torch.zeros(shape, dtype=torch.float32, device=device)

    tokenizer = AutoTokenizer.from_pretrained(config.vlm_model_name)
    seq_len = max(int(getattr(config, "tokenizer_max_length", 48)), 1)
    encoded = tokenizer(
        [task + "\n"] * batch_size,
        padding="max_length",
        truncation=True,
        max_length=seq_len,
        return_tensors="pt",
    )
    batch[OBS_LANGUAGE_TOKENS] = encoded["input_ids"].to(device)
    batch[OBS_LANGUAGE_ATTENTION_MASK] = encoded["attention_mask"].to(dtype=torch.bool, device=device)
    batch["task"] = [task]
    return batch


def _autocast_context(device: str, dtype: str) -> Any:
    import contextlib

    import torch

    if device != "cuda" or dtype == "fp32":
        return contextlib.nullcontext()
    torch_dtype = torch.bfloat16 if dtype == "bf16" else torch.float16
    return torch.autocast(device_type="cuda", dtype=torch_dtype)


def _apply_compile(policy: Any, mode: str) -> None:
    import torch

    print(f"[INFO] torch.compile(mode='{mode}') on model.sample_actions")
    policy.model.sample_actions = torch.compile(policy.model.sample_actions, mode=mode, dynamic=False)


def _run_single(policy: Any, batch: dict[str, Any], device: str, dtype: str) -> None:
    import torch

    sync = torch.cuda.synchronize if device == "cuda" else (lambda: None)
    policy.reset()
    sync()
    start = time.perf_counter()
    with torch.inference_mode(), _autocast_context(device, dtype):
        action = policy.select_action(batch)
    sync()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    print(f"[INFO] select_action latency: {elapsed_ms:.1f} ms")
    print(f"[INFO] action shape: {tuple(action.shape)} dtype: {action.dtype}")
    print(f"[INFO] action sample (first 8 dims): {action.flatten()[:8].tolist()}")


def _run_bench(policy: Any, batch: dict[str, Any], warmup: int, iters: int, device: str, dtype: str) -> None:
    import torch

    sync = torch.cuda.synchronize if device == "cuda" else (lambda: None)

    print(f"[INFO] Warmup {warmup} iterations")
    with torch.inference_mode(), _autocast_context(device, dtype):
        for _ in range(warmup):
            policy.reset()
            _ = policy.select_action(batch)

    timings: list[float] = []
    print(f"[INFO] Benchmark {iters} iterations")
    with torch.inference_mode(), _autocast_context(device, dtype):
        for i in range(iters):
            policy.reset()
            sync()
            start = time.perf_counter()
            _ = policy.select_action(batch)
            sync()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            timings.append(elapsed_ms)
            print(f"[INFO] iter {i + 1}/{iters}: {elapsed_ms:.1f} ms")

    timings.sort()
    p50 = timings[len(timings) // 2]
    p95 = timings[max(int(len(timings) * 0.95) - 1, 0)]
    p99 = timings[max(int(len(timings) * 0.99) - 1, 0)]
    mean = sum(timings) / len(timings)
    throughput = 1000.0 / max(mean, 1e-6)
    print(
        f"[INFO] mean={mean:.1f} ms p50={p50:.1f} ms p95={p95:.1f} ms p99={p99:.1f} ms throughput={throughput:.1f} Hz"
    )


def main() -> int:
    args = _parse_args()
    profile = _load_profile(args.optimized_dir)

    device = args.device or profile.get("device", "cuda")
    dtype = args.dtype or profile.get("dtype", "bf16")
    compile_mode = args.compile_mode or profile.get("compile_mode", "max-autotune")

    inductor_cache_dir = profile.get("inductor_cache_dir")
    if inductor_cache_dir:
        os.environ["TORCHINDUCTOR_CACHE_DIR"] = inductor_cache_dir
        print(f"[INFO] Using TorchInductor cache: {inductor_cache_dir}")

    pretrained_dir = _resolve_pretrained_dir(args.optimized_dir)
    policy = _load_policy(pretrained_dir, device)
    batch = _build_dummy_batch(policy, device)

    if not args.no_compile:
        _apply_compile(policy, compile_mode)

    if args.bench:
        _run_bench(policy, batch, args.warmup_iters, args.bench_iters, device, dtype)
    else:
        _run_single(policy, batch, device, dtype)
    return 0


if __name__ == "__main__":
    sys.exit(main())
