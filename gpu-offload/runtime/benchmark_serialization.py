"""Compare pickle and remoter codec performance for get_action arguments.

By default, this script builds a lightweight Checkpoint, three
MetaRemotedUUID collaborators, and an observation containing a 7 MiB tensor.
Use ``--factory module:function`` to supply the five real get_action arguments.

Examples:
    python benchmark_serialization.py
    python benchmark_serialization.py --device cuda --iterations 50
    python benchmark_serialization.py --factory benchmark_inputs:create_args
"""

from __future__ import annotations

import argparse
import gc
import importlib
import math
import pickle
import statistics
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from remoter import remoter

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2
_MIB = 1024 * 1024


@dataclass
class SyntheticCheckpointConfig:
    device: str
    path: str | None = None


class SyntheticCheckpoint:
    def __init__(self, config: SyntheticCheckpointConfig) -> None:
        self._cfg = config


class SyntheticRemoteObject:
    def __init__(self, name: str) -> None:
        self.uuid_rmt0bf = uuid.uuid4()
        self.rmtloc_rmt0bf = "tcp://127.0.0.1:9000"
        self.name = name


@dataclass(frozen=True)
class Measurement:
    codec: str
    operation: str
    payload_bytes: int
    samples_seconds: list[float]


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark pickle against the remoter MessagePack codec for get_action arguments."
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--factory", help="Optional module:function returning the five get_action arguments")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--pickle-protocol", type=int, default=pickle.HIGHEST_PROTOCOL)
    parser.add_argument("--tensor-mib", type=float, default=7.0)
    parser.add_argument("--warmups", type=int, default=5)
    return parser


def _load_factory(path: str) -> Callable[[], tuple[Any, Any, Any, Any, Any]]:
    module_name, separator, function_name = path.partition(":")
    if not separator or not module_name or not function_name:
        raise ValueError("--factory must use the format module:function")
    factory = getattr(importlib.import_module(module_name), function_name)
    if not callable(factory):
        raise TypeError(f"Factory {path!r} is not callable")
    return factory


def _as_meta_remoted(obj: Any) -> remoter.MetaRemotedUUID:
    if isinstance(obj, remoter.MetaRemotedUUID):
        return obj
    if not hasattr(obj, "uuid_rmt0bf"):
        obj.uuid_rmt0bf = uuid.uuid4()
    if not hasattr(obj, "rmtloc_rmt0bf"):
        obj.rmtloc_rmt0bf = "tcp://127.0.0.1:9000"
    return remoter.MetaRemotedUUID(obj)


def _create_synthetic_args(device: str, tensor_mib: float) -> tuple[Any, Any, Any, Any, Any]:
    if tensor_mib <= 0:
        raise ValueError("--tensor-mib must be greater than zero")
    torch = importlib.import_module("torch")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")

    tensor_bytes = math.ceil(tensor_mib * _MIB)
    observation = torch.arange(tensor_bytes, dtype=torch.uint8, device=device)
    checkpoint = SyntheticCheckpoint(SyntheticCheckpointConfig(device=device))
    preprocessor = SyntheticRemoteObject("preprocessor")
    policy = SyntheticRemoteObject("policy")
    postprocessor = SyntheticRemoteObject("postprocessor")
    return checkpoint, preprocessor, policy, postprocessor, {"observation": observation}


def _build_payload(args: argparse.Namespace) -> tuple[Any, Callable[[], None]]:
    if args.factory:
        checkpoint, preprocessor, policy, postprocessor, obs_dict = _load_factory(args.factory)()
    else:
        checkpoint, preprocessor, policy, postprocessor, obs_dict = _create_synthetic_args(args.device, args.tensor_mib)

    payload = (
        checkpoint,
        _as_meta_remoted(preprocessor),
        _as_meta_remoted(policy),
        _as_meta_remoted(postprocessor),
        obs_dict,
    )

    torch = sys.modules.get("torch")
    should_synchronize = torch is not None and torch.cuda.is_available() and _contains_cuda_tensor(payload, torch)

    def synchronize() -> None:
        if should_synchronize:
            torch.cuda.synchronize()

    return payload, synchronize


def _contains_cuda_tensor(value: Any, torch: Any, seen: set[int] | None = None) -> bool:
    if isinstance(value, torch.Tensor):
        return value.is_cuda
    if seen is None:
        seen = set()
    value_id = id(value)
    if value_id in seen:
        return False
    seen.add(value_id)
    if isinstance(value, dict):
        return any(
            _contains_cuda_tensor(key, torch, seen) or _contains_cuda_tensor(item, torch, seen)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_cuda_tensor(item, torch, seen) for item in value)
    if hasattr(value, "__dict__"):
        return any(_contains_cuda_tensor(item, torch, seen) for item in vars(value).values())
    return False


def _measure(
    operation: Callable[[], Any],
    *,
    iterations: int,
    synchronize: Callable[[], None],
    warmups: int,
) -> list[float]:
    for _ in range(warmups):
        operation()
        synchronize()

    samples: list[float] = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(iterations):
            synchronize()
            start = time.perf_counter_ns()
            operation()
            synchronize()
            samples.append((time.perf_counter_ns() - start) / 1_000_000_000)
    finally:
        if gc_enabled:
            gc.enable()
    return samples


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def _print_measurement(measurement: Measurement) -> None:
    samples = measurement.samples_seconds
    median = statistics.median(samples)
    throughput = measurement.payload_bytes / median / _MIB
    print(
        f"{measurement.codec:<11} {measurement.operation:<11} "
        f"median={median * 1000:9.3f} ms  "
        f"p95={_percentile(samples, 0.95) * 1000:9.3f} ms  "
        f"mean={statistics.fmean(samples) * 1000:9.3f} ms  "
        f"throughput={throughput:9.2f} MiB/s"
    )


def run(args: argparse.Namespace) -> int:
    if args.iterations < 1 or args.warmups < 0:
        raise ValueError("--iterations must be positive and --warmups must be non-negative")

    payload, synchronize = _build_payload(args)
    pickle_payload = pickle.dumps(payload, protocol=args.pickle_protocol)
    messagepack_payload = remoter.serialize_payload(payload)

    pickle.loads(pickle_payload)
    remoter.deserialize_payload(messagepack_payload)
    synchronize()

    measurements = [
        Measurement(
            codec="pickle",
            operation="serialize",
            payload_bytes=len(pickle_payload),
            samples_seconds=_measure(
                lambda: pickle.dumps(payload, protocol=args.pickle_protocol),
                iterations=args.iterations,
                synchronize=synchronize,
                warmups=args.warmups,
            ),
        ),
        Measurement(
            codec="pickle",
            operation="deserialize",
            payload_bytes=len(pickle_payload),
            samples_seconds=_measure(
                lambda: pickle.loads(pickle_payload),
                iterations=args.iterations,
                synchronize=synchronize,
                warmups=args.warmups,
            ),
        ),
        Measurement(
            codec="messagepack",
            operation="serialize",
            payload_bytes=len(messagepack_payload),
            samples_seconds=_measure(
                lambda: remoter.serialize_payload(payload),
                iterations=args.iterations,
                synchronize=synchronize,
                warmups=args.warmups,
            ),
        ),
        Measurement(
            codec="messagepack",
            operation="deserialize",
            payload_bytes=len(messagepack_payload),
            samples_seconds=_measure(
                lambda: remoter.deserialize_payload(messagepack_payload),
                iterations=args.iterations,
                synchronize=synchronize,
                warmups=args.warmups,
            ),
        ),
    ]

    print(f"iterations: {args.iterations} (warmups: {args.warmups})")
    print(f"pickle payload:      {len(pickle_payload) / _MIB:.3f} MiB")
    print(f"messagepack payload: {len(messagepack_payload) / _MIB:.3f} MiB")
    print()
    for measurement in measurements:
        _print_measurement(measurement)

    pickle_serialize = statistics.median(measurements[0].samples_seconds)
    pickle_deserialize = statistics.median(measurements[1].samples_seconds)
    messagepack_serialize = statistics.median(measurements[2].samples_seconds)
    messagepack_deserialize = statistics.median(measurements[3].samples_seconds)
    print()
    print(f"serialize slowdown:   {messagepack_serialize / pickle_serialize:.2f}x")
    print(f"deserialize slowdown: {messagepack_deserialize / pickle_deserialize:.2f}x")
    return EXIT_SUCCESS


def main() -> int:
    try:
        return run(create_parser().parse_args())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
