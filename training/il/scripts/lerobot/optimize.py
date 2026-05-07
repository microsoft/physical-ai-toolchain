"""Optimize an Azure ML registered LeRobot policy for faster inference.

Acceleration strategy
=====================

For SmolVLA (and the closely related pi0 / pi05 flow-matching VLAs) the LeRobot
project supports `torch.compile(mode="max-autotune")` natively on the
`sample_actions` and `forward` methods. Combined with bf16 autocast on Ampere
or newer NVIDIA GPUs and a persistent TorchInductor cache directory, this gives
a 1.5-3x speedup on the iterative denoising loop without any model export.

ONNX / TensorRT export is intentionally not attempted here. SmolVLA combines a
SmolVLM2 prefix encoder, dynamic language token padding, a KV cache mutated
across `num_steps` denoising iterations, and a Python `for` loop over flow
matching steps. None of these compose with a single static export graph, and
HuggingFace Optimum does not currently support the SmolVLM2 backbone. The
practical alternative is to recompile with `torch.compile`, persist the
inductor cache, and pre-warm the kernels via this optimization step so that
the first inference call in production does not pay the autotune cost.

Resulting artifact
==================

This script writes:

* `pretrained_model/` - downloaded weights + config from Azure ML, unchanged.
* `optimization_profile.json` - record of the chosen runtime settings.
* `inductor_cache/` - persistent TorchInductor kernel cache, populated by the
  warmup pass and consumed by `infer.py` to skip recompilation.

Both `optimize.py` and `infer.py` set `TORCHINDUCTOR_CACHE_DIR` to the same
location, so the second `infer.py` run starts in seconds instead of minutes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_ROOT = Path("outputs/optimized")
DEFAULT_DEVICE = "cuda"
DEFAULT_DTYPE = "bf16"
DEFAULT_COMPILE_MODE = "max-autotune"


@dataclass(frozen=True)
class OptimizationProfile:
    """Runtime configuration captured at optimization time and replayed at inference."""

    model_name: str
    model_version: str
    policy_type: str
    device: str
    dtype: str
    compile_mode: str
    num_steps: int
    chunk_size: int
    warmup_iters: int
    bench_iters: int
    inductor_cache_dir: str
    eager_p50_ms: float
    eager_p95_ms: float
    optimized_p50_ms: float
    optimized_p95_ms: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-name", required=True, help="Azure ML registered model name.")
    parser.add_argument(
        "--model-version",
        default="latest",
        help="Azure ML model version, or 'latest' (default) to resolve the highest version.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Output directory (default: {DEFAULT_OUTPUT_ROOT}/<model-name>/v<version>).",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
        choices=["cuda", "cpu"],
        help=f"Inference device (default: {DEFAULT_DEVICE}).",
    )
    parser.add_argument(
        "--dtype",
        default=DEFAULT_DTYPE,
        choices=["bf16", "fp16", "fp32"],
        help=f"Autocast dtype on CUDA (default: {DEFAULT_DTYPE}).",
    )
    parser.add_argument(
        "--compile-mode",
        default=DEFAULT_COMPILE_MODE,
        choices=["max-autotune", "reduce-overhead", "default"],
        help=f"torch.compile mode (default: {DEFAULT_COMPILE_MODE}).",
    )
    parser.add_argument(
        "--warmup-iters",
        type=int,
        default=5,
        help="Warmup iterations for AOT compilation and benchmark stabilization (default: 5).",
    )
    parser.add_argument(
        "--bench-iters",
        type=int,
        default=20,
        help="Benchmark iterations after warmup (default: 20).",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        help="Skip eager-mode baseline benchmark to save time.",
    )
    parser.add_argument(
        "--skip-compile",
        action="store_true",
        help=(
            "Skip torch.compile and benchmark only the bf16 (or fp16) autocast speedup. "
            "Useful when iterating quickly, since SmolVLA compile can take 5-10 minutes."
        ),
    )
    return parser.parse_args()


def _resolve_output_dir(args: argparse.Namespace, version: str) -> Path:
    if args.output_dir is not None:
        return args.output_dir
    return DEFAULT_OUTPUT_ROOT / args.model_name / f"v{version}"


def _resolve_model_version(client: Any, name: str, version: str) -> str:
    if version != "latest":
        return version
    versions = list(client.models.list(name=name))
    if not versions:
        raise SystemExit(f"[ERROR] No versions found for model '{name}'.")
    latest = max(versions, key=lambda m: int(m.version))
    print(f"[INFO] Resolved 'latest' to {name} v{latest.version}")
    return str(latest.version)


def _find_checkpoint_dir(root: Path) -> Path | None:
    """Find the directory under `root` that contains config.json.

    Azure ML's `models.download` may nest the checkpoint under either
    `<root>/<model_name>/...` or `<root>/pretrained_model/...` depending on
    how the model was originally registered.
    """
    for config_path in root.rglob("config.json"):
        return config_path.parent
    return None


def _download_model(client: Any, name: str, version: str, dest: Path) -> Path:
    existing = _find_checkpoint_dir(dest)
    if existing is not None:
        print(f"[INFO] Reusing existing checkpoint at {existing}")
        return existing
    dest.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Downloading {name} v{version} to {dest}")
    client.models.download(name=name, version=version, download_path=str(dest))
    checkpoint_dir = _find_checkpoint_dir(dest)
    if checkpoint_dir is None:
        raise SystemExit(
            f"[ERROR] Could not find config.json under {dest} after download. Contents: {list(dest.rglob('*'))[:20]}"
        )
    print(f"[INFO] Checkpoint resolved at {checkpoint_dir}")
    return checkpoint_dir


def _load_policy(checkpoint_dir: Path, device: str) -> Any:
    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

    config = PreTrainedConfig.from_pretrained(str(checkpoint_dir))
    config.device = device
    if config.type != "smolvla":
        raise SystemExit(
            f"[ERROR] optimize.py currently supports policy type 'smolvla', got '{config.type}'. "
            "Other policy types should fall back to eager inference."
        )
    print(f"[INFO] Loading policy from {checkpoint_dir} (type={config.type}, device={device})")
    return SmolVLAPolicy.from_pretrained(str(checkpoint_dir), config=config)


def _build_dummy_batch(policy: Any, device: str, task: str = "benchmark task") -> dict[str, Any]:
    """Construct a representative batch matching the policy's expected feature shapes.

    Tokenizes `task` via the VLM tokenizer so the batch is shaped like a real
    inference call from the dataset processor pipeline.
    """
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


def _benchmark(
    policy: Any,
    batch: dict[str, Any],
    iters: int,
    device: str,
    dtype: str,
    label: str,
) -> tuple[float, float]:
    """Run `iters` `select_action` calls and return p50, p95 latency in milliseconds."""
    import torch

    timings: list[float] = []
    sync = torch.cuda.synchronize if device == "cuda" else (lambda: None)
    print(f"[INFO] Benchmark[{label}]: {iters} iterations")
    with torch.inference_mode(), _autocast_context(device, dtype):
        for i in range(iters):
            policy.reset()
            sync()
            start = time.perf_counter()
            _ = policy.select_action(batch)
            sync()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            timings.append(elapsed_ms)
            print(f"[INFO] Benchmark[{label}] iter {i + 1}/{iters}: {elapsed_ms:.1f} ms")

    timings.sort()
    p50 = timings[len(timings) // 2]
    p95 = timings[max(int(len(timings) * 0.95) - 1, 0)]
    print(f"[INFO] Benchmark[{label}] p50={p50:.1f} ms p95={p95:.1f} ms")
    return p50, p95


def _apply_optimizations(policy: Any, compile_mode: str) -> None:
    import torch

    print(f"[INFO] Applying torch.compile(mode='{compile_mode}') to model.sample_actions")
    policy.model.sample_actions = torch.compile(policy.model.sample_actions, mode=compile_mode, dynamic=False)


def main() -> int:
    args = _parse_args()

    from training.il.scripts.lerobot.checkpoints import _get_aml_client

    client = _get_aml_client()
    if client is None:
        print(
            "[ERROR] Azure ML credentials missing. Set AZURE_SUBSCRIPTION_ID, "
            "AZURE_RESOURCE_GROUP, AZUREML_WORKSPACE_NAME (and AZURE_USE_CLI_CREDENTIAL=true for local).",
            file=sys.stderr,
        )
        return 2

    version = _resolve_model_version(client, args.model_name, args.model_version)
    output_dir = _resolve_output_dir(args, version)
    output_dir.mkdir(parents=True, exist_ok=True)

    inductor_cache_dir = (output_dir / "inductor_cache").resolve()
    inductor_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TORCHINDUCTOR_CACHE_DIR"] = str(inductor_cache_dir)
    print(f"[INFO] Using TorchInductor cache: {inductor_cache_dir}")

    pretrained_dir = _download_model(client, args.model_name, version, output_dir)

    policy = _load_policy(pretrained_dir, args.device)
    batch = _build_dummy_batch(policy, args.device)

    eager_p50 = eager_p95 = float("nan")
    if not args.skip_baseline:
        eager_p50, eager_p95 = _benchmark(policy, batch, args.bench_iters, args.device, "fp32", label="eager")

    if args.skip_compile:
        print("[INFO] Skipping torch.compile (autocast-only optimization).")
        compile_mode_recorded = "none"
    else:
        _apply_optimizations(policy, args.compile_mode)
        print(f"[INFO] AOT warmup: {args.warmup_iters} iterations (compiles graph)")
        _benchmark(policy, batch, args.warmup_iters, args.device, args.dtype, label="warmup")
        compile_mode_recorded = args.compile_mode

    optimized_p50, optimized_p95 = _benchmark(
        policy, batch, args.bench_iters, args.device, args.dtype, label="optimized"
    )

    profile = OptimizationProfile(
        model_name=args.model_name,
        model_version=version,
        policy_type=policy.config.type,
        device=args.device,
        dtype=args.dtype,
        compile_mode=compile_mode_recorded,
        num_steps=int(getattr(policy.config, "num_steps", 0)),
        chunk_size=int(getattr(policy.config, "chunk_size", 0)),
        warmup_iters=args.warmup_iters,
        bench_iters=args.bench_iters,
        inductor_cache_dir=str(inductor_cache_dir),
        eager_p50_ms=eager_p50,
        eager_p95_ms=eager_p95,
        optimized_p50_ms=optimized_p50,
        optimized_p95_ms=optimized_p95,
    )
    profile_path = output_dir / "optimization_profile.json"
    profile_path.write_text(json.dumps(asdict(profile), indent=2))
    print(f"[INFO] Wrote optimization profile to {profile_path}")

    if not args.skip_baseline and eager_p50 == eager_p50:  # not NaN
        speedup = eager_p50 / max(optimized_p50, 1e-6)
        print(f"[INFO] Speedup p50: {speedup:.2f}x ({eager_p50:.1f} ms -> {optimized_p50:.1f} ms)")

    print(f"[INFO] Run inference with: python -m training.il.scripts.lerobot.infer --optimized-dir {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
