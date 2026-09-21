from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import torch
from lerobot.configs import FeatureType, PreTrainedConfig
from lerobot.rollout.context import _load_pretrained_policy
from raw_observation_inference import RawObservationSyncInferenceEngine


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark synthetic LeRobot policy inference")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--calls", type=int, default=100)
    parser.add_argument("--warmup-calls", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--task", default="Synthetic inference benchmark")
    parser.add_argument("--robot-type", default="so101_follower")
    args = parser.parse_args()
    if args.calls <= 0:
        parser.error("--calls must be greater than zero")
    if args.warmup_calls < 0:
        parser.error("--warmup-calls must be zero or greater")
    return args


def _percentile(sorted_values: list[float], percentile: float) -> float:
    index = (len(sorted_values) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = index - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _summarize(values_ms: list[float]) -> dict[str, float | int]:
    sorted_values = sorted(values_ms)
    total_ms = sum(sorted_values)
    return {
        "calls": len(sorted_values),
        "mean_ms": total_ms / len(sorted_values),
        "p50_ms": _percentile(sorted_values, 0.50),
        "p95_ms": _percentile(sorted_values, 0.95),
        "p99_ms": _percentile(sorted_values, 0.99),
        "max_ms": sorted_values[-1],
        "throughput_hz": len(sorted_values) / (total_ms / 1000),
    }


def _build_observation(config: PreTrainedConfig, seed: int) -> dict[str, torch.Tensor]:
    generator = torch.Generator().manual_seed(seed)
    observation = {}
    for name, feature in config.input_features.items():
        shape = tuple(feature.shape)
        if feature.type == FeatureType.VISUAL:
            channels, height, width = shape
            observation[name] = torch.randint(
                0,
                256,
                (height, width, channels),
                dtype=torch.uint8,
                generator=generator,
            )
        else:
            observation[name] = torch.rand(shape, dtype=torch.float32, generator=generator).mul_(2).sub_(1)
    return observation


def _build_engine(
    policy_path: str,
    task: str,
    robot_type: str,
) -> tuple[RawObservationSyncInferenceEngine, PreTrainedConfig]:
    config = PreTrainedConfig.from_pretrained(policy_path)
    config.pretrained_path = Path(policy_path)
    policy = _load_pretrained_policy(config).to(config.device)
    policy.eval()

    action_dimension = config.output_features["action"].shape[0]
    action_keys = getattr(config, "action_feature_names", None) or [
        f"action_{index}" for index in range(action_dimension)
    ]
    engine = RawObservationSyncInferenceEngine(
        policy=policy,
        dataset_features={"action": {"names": action_keys}},
        ordered_action_keys=action_keys,
        task=task,
        device=config.device,
        robot_type=robot_type,
    )
    engine.start()
    return engine, config


def _run_call(
    engine: RawObservationSyncInferenceEngine,
    observation: dict[str, torch.Tensor],
    action_dimension: int,
) -> tuple[torch.Tensor, float]:
    started_at = time.perf_counter()
    action = engine.get_action(observation.copy())
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    if action is None or tuple(action.shape) != (action_dimension,) or not torch.isfinite(action).all():
        raise RuntimeError("Synthetic inference returned an invalid action")
    return action, elapsed_ms


def _print_result(name: str, values_ms: list[float]) -> None:
    if values_ms:
        print(f"SYNTHETIC_BENCHMARK {name}={json.dumps(_summarize(values_ms), sort_keys=True)}", flush=True)


def main() -> None:
    args = _parse_args()
    torch.manual_seed(args.seed)
    engine, config = _build_engine(args.policy, args.task, args.robot_type)
    observation = _build_observation(config, args.seed)
    action_dimension = config.output_features["action"].shape[0]
    n_action_steps = int(getattr(config, "n_action_steps", 1))
    if getattr(config, "temporal_ensemble_coeff", None) is not None:
        n_action_steps = 1

    try:
        for _ in range(args.warmup_calls):
            _run_call(engine, observation, action_dimension)
        engine.reset()

        all_latencies = []
        refill_latencies = []
        queued_latencies = []
        last_action: torch.Tensor | None = None
        for index in range(args.calls):
            last_action, elapsed_ms = _run_call(engine, observation, action_dimension)
            all_latencies.append(elapsed_ms)
            if index % n_action_steps == 0:
                refill_latencies.append(elapsed_ms)
            else:
                queued_latencies.append(elapsed_ms)
    finally:
        engine.stop()

    metadata: dict[str, Any] = {
        "policy_type": config.type,
        "calls": args.calls,
        "warmup_calls": args.warmup_calls,
        "n_action_steps": n_action_steps,
        "synthetic_payload_bytes": sum(value.numel() * value.element_size() for value in observation.values()),
        "action_shape": list(last_action.shape) if last_action is not None else [],
    }
    print(f"SYNTHETIC_BENCHMARK metadata={json.dumps(metadata, sort_keys=True)}", flush=True)
    _print_result("all", all_latencies)
    _print_result("chunk_refill", refill_latencies)
    _print_result("queued_action", queued_latencies)


if __name__ == "__main__":
    main()
