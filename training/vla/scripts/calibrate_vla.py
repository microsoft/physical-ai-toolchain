"""Calibrate VLA micro-batch sizes through isolated LeRobot optimizer steps."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_adapters import AdapterError, AdapterRequest, get_adapter
from vla_contracts import (
    SCHEMA_VERSION,
    ContractError,
    RecordKind,
    calibration_workload_fingerprint,
    canonical_json,
    resolve_recommended_batch_size,
    sha256_bytes,
    sha256_file,
    validate_calibration_workload,
    validate_record,
    write_record,
)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2
EXIT_OOM = 10

_OWNED_TRAINING_ARGUMENTS = (
    "--batch_size",
    "--env_eval_freq",
    "--eval_steps",
    "--log_freq",
    "--output_dir",
    "--save_checkpoint",
    "--steps",
)


class CalibrationError(ValueError):
    """Raised when calibration configuration or execution is invalid."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_candidate_batch_sizes(value: str) -> list[int]:
    try:
        candidates = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise CalibrationError("Candidate batch sizes must be comma-separated integers") from exc
    if not candidates or any(candidate < 1 for candidate in candidates):
        raise CalibrationError("Candidate batch sizes must contain positive integers")
    if len(candidates) != len(set(candidates)):
        raise CalibrationError("Candidate batch sizes must not contain duplicates")
    if candidates != sorted(candidates):
        raise CalibrationError("Candidate batch sizes must be in ascending order")
    return candidates


def _has_argument(arguments: Sequence[str], name: str) -> bool:
    return any(argument == name or argument.startswith(f"{name}=") for argument in arguments)


def _training_arguments(arguments: Sequence[str]) -> list[str]:
    normalized = list(arguments)
    if normalized and normalized[0] == "--":
        normalized.pop(0)
    return normalized


def _argument_value(arguments: Sequence[str], name: str) -> str | None:
    for index, argument in enumerate(arguments):
        if argument.startswith(f"{name}="):
            return argument.split("=", 1)[1]
        if argument == name and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


def _parse_boolean(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise CalibrationError(f"{name} must be true or false")
    return normalized == "true"


def _load_dataset_info(training_arguments: Sequence[str]) -> dict[str, Any]:
    dataset_root = _argument_value(training_arguments, "--dataset.root")
    if dataset_root:
        root = Path(dataset_root)
        candidates = (root / "meta" / "info.json", root / "info.json")
        info_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if info_path is None:
            raise CalibrationError(f"Prepared dataset is missing info.json under {root}")
    else:
        from huggingface_hub import hf_hub_download

        repository = os.environ.get("DATASET_REPO_ID", "")
        revision = os.environ.get("DATASET_REVISION", "")
        if not repository or not revision:
            raise CalibrationError("DATASET_REPO_ID and DATASET_REVISION are required")
        info_path = Path(
            hf_hub_download(
                repo_id=repository,
                revision=revision,
                repo_type="dataset",
                filename="meta/info.json",
            )
        )
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"Unable to read dataset metadata {info_path}: {exc}") from exc
    if not isinstance(info, dict):
        raise CalibrationError(f"Dataset metadata must be a JSON object: {info_path}")
    return info


def _parse_rename_map(training_arguments: Sequence[str]) -> dict[str, str]:
    raw_value = _argument_value(training_arguments, "--rename_map") or "{}"
    try:
        rename_map = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"Training rename map is invalid JSON: {exc}") from exc
    if not isinstance(rename_map, dict) or any(
        not isinstance(source, str)
        or not source
        or not isinstance(target, str)
        or not target
        for source, target in rename_map.items()
    ):
        raise CalibrationError("Training rename map must contain non-empty string keys and values")
    return rename_map


def _dataset_contract(
    info: Mapping[str, Any],
    repository: str,
    revision: str,
    rename_map: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, list[int]]]:
    features = info.get("features")
    if not isinstance(features, Mapping) or not features:
        raise CalibrationError("Dataset metadata must contain a non-empty features object")
    input_shapes: dict[str, list[int]] = {}
    for source_name, feature in features.items():
        if not isinstance(source_name, str) or not source_name.startswith("observation.images."):
            continue
        if not isinstance(feature, Mapping):
            raise CalibrationError(f"Dataset feature {source_name} must be an object")
        shape = feature.get("shape")
        if (
            not isinstance(shape, Sequence)
            or isinstance(shape, (str, bytes, bytearray))
            or len(shape) != 3
            or any(not isinstance(size, int) or isinstance(size, bool) or size < 1 for size in shape)
        ):
            raise CalibrationError(f"Dataset image feature {source_name} must have a positive HWC shape")
        target_name = rename_map.get(source_name, source_name)
        if target_name in input_shapes:
            raise CalibrationError(f"Dataset rename map produces duplicate feature {target_name}")
        input_shapes[target_name] = [shape[2], shape[0], shape[1]]
    if not input_shapes:
        raise CalibrationError("Dataset metadata does not contain image observation features")
    dataset = {
        "uri": f"hf://{repository}",
        "version": revision,
        "features_sha256": sha256_bytes(canonical_json(features).encode("utf-8")),
    }
    return dataset, dict(sorted(input_shapes.items()))


def _build_workload(training_arguments: Sequence[str]) -> dict[str, Any]:
    import torch

    if not torch.cuda.is_available():
        raise CalibrationError("CUDA is unavailable while generating the calibration workload")
    gpu_count = torch.cuda.device_count()
    if gpu_count != 1:
        raise CalibrationError(f"Calibration requires exactly one visible GPU, found {gpu_count}")

    repository = os.environ.get("DATASET_REPO_ID", "")
    revision = os.environ.get("DATASET_REVISION", "")
    source_repository = os.environ.get("INIT_FROM_POLICY_HF_REPO_ID", "")
    source_revision = os.environ.get("INIT_FROM_POLICY_HF_REVISION", "")
    code_repository = os.environ.get("CODE_REPOSITORY", "")
    code_revision = os.environ.get("CODE_REVISION", "")
    runtime_image = os.environ.get("RUNTIME_IMAGE", "")
    compute_target = os.environ.get("COMPUTE_TARGET", "")
    adapter_name = os.environ.get("VLA_MODEL_ADAPTER", "")
    required_values = {
        "DATASET_REPO_ID": repository,
        "DATASET_REVISION": revision,
        "INIT_FROM_POLICY_HF_REPO_ID": source_repository,
        "INIT_FROM_POLICY_HF_REVISION": source_revision,
        "CODE_REPOSITORY": code_repository,
        "CODE_REVISION": code_revision,
        "RUNTIME_IMAGE": runtime_image,
        "COMPUTE_TARGET": compute_target,
        "VLA_MODEL_ADAPTER": adapter_name,
        "POLICY_TYPE": os.environ.get("POLICY_TYPE", ""),
    }
    missing = [name for name, value in required_values.items() if not value]
    if missing:
        raise CalibrationError(f"Missing workload inputs: {', '.join(missing)}")

    rename_map = _parse_rename_map(training_arguments)
    train_expert_value = os.environ.get("TRAIN_EXPERT_ONLY", "")
    adapter = get_adapter(adapter_name)
    resolution = adapter.resolve(
        AdapterRequest(
            policy_type=required_values["POLICY_TYPE"],
            mixed_precision=os.environ.get("MIXED_PRECISION", "no"),
            policy_dtype=os.environ.get("POLICY_DTYPE") or None,
            train_expert_only=(
                _parse_boolean(train_expert_value, "TRAIN_EXPERT_ONLY") if train_expert_value else None
            ),
            gradient_checkpointing=_parse_boolean(
                os.environ.get("GRADIENT_CHECKPOINTING", "false"),
                "GRADIENT_CHECKPOINTING",
            ),
            rename_map=rename_map,
        )
    )
    expected_imagenet_stats = resolution.use_imagenet_stats
    actual_imagenet_stats = _parse_boolean(
        os.environ.get("USE_IMAGENET_STATS", str(expected_imagenet_stats).lower()),
        "USE_IMAGENET_STATS",
    )
    if actual_imagenet_stats != expected_imagenet_stats:
        raise CalibrationError("USE_IMAGENET_STATS does not match the registered adapter")

    dataset, input_shapes = _dataset_contract(
        _load_dataset_info(training_arguments), repository, revision, rename_map
    )
    lock_path = Path(os.environ.get("LEROBOT_PROJECT", "training/vla/lerobot")) / "uv.lock"
    if not lock_path.is_file():
        raise CalibrationError(f"LeRobot lockfile is missing: {lock_path}")
    train_expert_only = resolution.environment.get("TRAIN_EXPERT_ONLY") == "true"
    gradient_checkpointing = resolution.environment.get("GRADIENT_CHECKPOINTING") == "true"
    workload = {
        "schema_version": 1,
        "source_model": {"repository": source_repository, "revision": source_revision},
        "dataset": dataset,
        "code": {"repository": code_repository, "revision": code_revision},
        "runtime": {"image": runtime_image, "lock_sha256": sha256_file(lock_path)},
        "adapter": {
            "name": resolution.adapter_name,
            "version": resolution.adapter_version,
            "config_sha256": resolution.config_sha256,
        },
        "compute": {
            "target": compute_target,
            "gpu_type": torch.cuda.get_device_name(),
            "gpu_count": gpu_count,
        },
        "precision": resolution.environment["MIXED_PRECISION"],
        "trainable_scope": "expert-only" if train_expert_only else "all",
        "gradient_checkpointing": gradient_checkpointing,
        "world_size": gpu_count,
        "input_shapes": input_shapes,
    }
    validate_calibration_workload(workload)
    return workload


def _build_probe_arguments(training_arguments: Sequence[str], batch_size: int, output_dir: Path) -> list[str]:
    for argument in training_arguments:
        if any(argument == owned or argument.startswith(f"{owned}=") for owned in _OWNED_TRAINING_ARGUMENTS):
            raise CalibrationError(f"Calibration owns the LeRobot argument {argument.split('=', 1)[0]}")

    arguments = list(training_arguments)
    dataset_repo_id = os.environ.get("DATASET_REPO_ID", "")
    if not _has_argument(arguments, "--dataset.repo_id"):
        if not dataset_repo_id:
            raise CalibrationError("DATASET_REPO_ID is required when --dataset.repo_id is not provided")
        arguments.append(f"--dataset.repo_id={dataset_repo_id}")

    dataset_revision = os.environ.get("DATASET_REVISION", "")
    if dataset_revision and not _has_argument(arguments, "--dataset.revision"):
        arguments.append(f"--dataset.revision={dataset_revision}")

    if not _has_argument(arguments, "--policy.path") and not _has_argument(arguments, "--policy.type"):
        policy_type = os.environ.get("POLICY_TYPE", "")
        if not policy_type:
            raise CalibrationError("POLICY_TYPE is required when no policy path or type is provided")
        arguments.append(f"--policy.type={policy_type}")

    defaults = {
        "--job_name": os.environ.get("JOB_NAME", "vla-calibration"),
        "--policy.device": "cuda",
        "--policy.push_to_hub": "false",
        "--wandb.enable": "false",
    }
    for name, value in defaults.items():
        if not _has_argument(arguments, name):
            arguments.append(f"{name}={value}")

    policy_dtype = os.environ.get("POLICY_DTYPE", "")
    if policy_dtype and not _has_argument(arguments, "--policy.dtype"):
        arguments.append(f"--policy.dtype={policy_dtype}")
    if os.environ.get("TRAIN_EXPERT_ONLY", "").lower() == "true" and not _has_argument(
        arguments, "--policy.train_expert_only"
    ):
        arguments.append("--policy.train_expert_only=true")
    if os.environ.get("GRADIENT_CHECKPOINTING", "").lower() == "true" and not _has_argument(
        arguments, "--policy.gradient_checkpointing"
    ):
        arguments.append("--policy.gradient_checkpointing=true")

    arguments.extend(
        (
            f"--batch_size={batch_size}",
            "--steps=1",
            "--save_checkpoint=false",
            "--env_eval_freq=0",
            "--eval_steps=0",
            "--log_freq=1",
            f"--output_dir={output_dir}",
        )
    )
    return arguments


def _is_cuda_oom(exception: BaseException) -> bool:
    message = str(exception).lower()
    return "cuda" in message and ("out of memory" in message or "memory allocation" in message)


def _run_probe(args: argparse.Namespace) -> int:
    import torch

    if not torch.cuda.is_available():
        raise CalibrationError("CUDA is unavailable in the calibration probe")
    visible_gpus = torch.cuda.device_count()
    if visible_gpus != args.expected_world_size:
        raise CalibrationError(
            f"Expected {args.expected_world_size} visible GPU(s), found {visible_gpus}"
        )
    if visible_gpus != 1:
        raise CalibrationError("The calibration probe currently supports exactly one GPU")

    from lerobot.scripts import lerobot_train

    original_update_policy = lerobot_train.update_policy
    optimizer_steps = 0

    def measured_update_policy(*update_args: Any, **update_kwargs: Any) -> Any:
        nonlocal optimizer_steps
        started = time.perf_counter()
        result = original_update_policy(*update_args, **update_kwargs)
        torch.cuda.synchronize()
        duration_seconds = time.perf_counter() - started
        optimizer_steps += 1
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        _write_json(
            args.probe_output,
            {
                "micro_batch_size": args.probe_batch_size,
                "outcome": "success",
                "device_name": torch.cuda.get_device_name(),
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
                "free_bytes_after_step": free_bytes,
                "total_bytes": total_bytes,
                "duration_seconds": duration_seconds,
                "samples_per_second": args.probe_batch_size / duration_seconds,
                "optimizer_steps": optimizer_steps,
            },
        )
        return result

    lerobot_train.update_policy = measured_update_policy
    sys.argv = ["lerobot-train", *_training_arguments(args.training_arguments)]
    try:
        lerobot_train.main()
    except Exception as exc:
        if _is_cuda_oom(exc) or isinstance(exc, torch.cuda.OutOfMemoryError):
            _write_json(
                args.probe_output,
                {
                    "micro_batch_size": args.probe_batch_size,
                    "outcome": "oom",
                    "error_type": type(exc).__name__,
                },
            )
            return EXIT_OOM
        raise
    finally:
        lerobot_train.update_policy = original_update_policy
        torch.cuda.empty_cache()

    if optimizer_steps != 1:
        raise CalibrationError(f"Calibration probe completed {optimizer_steps} optimizer steps instead of 1")
    return EXIT_SUCCESS


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _run_candidate(
    batch_size: int,
    training_arguments: Sequence[str],
    probe_dir: Path,
    timeout_seconds: int,
    expected_world_size: int,
) -> dict[str, Any]:
    result_path = probe_dir / f"batch-{batch_size}.json"
    training_output = probe_dir / f"training-batch-{batch_size}"
    probe_arguments = _build_probe_arguments(training_arguments, batch_size, training_output)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--probe",
        "--probe-output",
        str(result_path),
        "--probe-batch-size",
        str(batch_size),
        "--expected-world-size",
        str(expected_world_size),
        "--",
        *probe_arguments,
    ]
    process = subprocess.Popen(command, start_new_session=True)
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        return {
            "micro_batch_size": batch_size,
            "outcome": "timeout",
            "timeout_seconds": timeout_seconds,
        }

    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CalibrationError(f"Probe result is invalid JSON for batch size {batch_size}: {exc}") from exc
        if not isinstance(result, dict):
            raise CalibrationError(f"Probe result must be an object for batch size {batch_size}")
        if return_code in {EXIT_SUCCESS, EXIT_OOM}:
            return result
    if return_code in {-signal.SIGKILL, 128 + signal.SIGKILL}:
        return {"micro_batch_size": batch_size, "outcome": "oom", "error_type": "SIGKILL"}
    return {"micro_batch_size": batch_size, "outcome": "failed", "return_code": return_code}


def _build_report(
    workload: Mapping[str, Any],
    candidate_results: list[dict[str, Any]],
    headroom_fraction: float,
) -> dict[str, Any]:
    recommended = [
        candidate
        for candidate in candidate_results
        if candidate.get("outcome") == "success"
        and candidate["peak_reserved_bytes"] <= candidate["total_bytes"] * (1 - headroom_fraction)
    ]
    recommendation: dict[str, Any]
    if recommended:
        selected = max(recommended, key=lambda candidate: candidate["micro_batch_size"])
        recommendation = {
            "status": "recommended",
            "micro_batch_size": selected["micro_batch_size"],
            "peak_reserved_bytes": selected["peak_reserved_bytes"],
        }
    else:
        recommendation = {
            "status": "infeasible",
            "micro_batch_size": None,
            "reason": "No successful candidate retained the required CUDA memory headroom",
        }

    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": RecordKind.CALIBRATION.value,
        "created_at": _utc_now(),
        "workload_fingerprint": calibration_workload_fingerprint(workload),
        "workload": dict(workload),
        "candidate_results": candidate_results,
        "recommendation": recommendation,
        "headroom_fraction": headroom_fraction,
    }
    validate_record(report, RecordKind.CALIBRATION)
    return report


def _run_calibration(args: argparse.Namespace) -> int:
    training_arguments = _training_arguments(args.training_arguments)
    workload = _build_workload(training_arguments)
    if workload["world_size"] != 1:
        raise CalibrationError("Calibration currently supports workloads with world_size equal to 1")
    workload_path = args.workload_output_dir / "workload.json"
    _write_json(workload_path, workload)
    candidates = _parse_candidate_batch_sizes(args.candidate_batch_sizes)

    with tempfile.TemporaryDirectory(prefix="vla-calibration-") as temporary_directory:
        probe_dir = Path(temporary_directory)
        results = [
            _run_candidate(
                batch_size,
                training_arguments,
                probe_dir,
                args.probe_timeout_seconds,
                workload["world_size"],
            )
            for batch_size in candidates
        ]

    report = _build_report(workload, results, args.headroom_fraction)
    report_path = args.output_dir / "calibration-report.json"
    digest = write_record(report_path, report)
    print(
        json.dumps(
            {
                "calibration_report": str(report_path),
                "sha256": digest,
                "workload_contract": str(workload_path),
            },
            sort_keys=True,
        )
    )
    return EXIT_SUCCESS


def _resolve_training_batch_size(args: argparse.Namespace) -> int:
    training_arguments = _training_arguments(args.training_arguments)
    expected_workload = _build_workload(training_arguments)
    batch_size = resolve_recommended_batch_size(
        args.calibration_report,
        args.workload_contract,
        expected_workload,
    )
    print(batch_size)
    return EXIT_SUCCESS


def _sample_workload() -> dict[str, Any]:
    digest = "a" * 64
    revision = "b" * 40
    return {
        "schema_version": 1,
        "source_model": {"repository": "org/model", "revision": revision},
        "dataset": {"uri": "azureml:data:1", "version": "1", "features_sha256": digest},
        "code": {"repository": "org/repo", "revision": revision},
        "runtime": {"image": f"image@sha256:{digest}", "lock_sha256": digest},
        "adapter": {"name": "lerobot-pi", "version": "1", "config_sha256": digest},
        "compute": {"target": "arc-gpu", "gpu_type": "test-gpu", "gpu_count": 1},
        "precision": "bf16",
        "trainable_scope": "expert-only",
        "gradient_checkpointing": True,
        "world_size": 1,
        "input_shapes": {"observation.images.base_0_rgb": [3, 224, 224]},
    }


def _run_self_check() -> None:
    workload = _sample_workload()
    validate_calibration_workload(workload)
    if _parse_candidate_batch_sizes("1,2,4") != [1, 2, 4]:
        raise CalibrationError("Candidate batch-size parsing changed")
    if _training_arguments(("--", "--wandb.enable=false")) != ["--wandb.enable=false"]:
        raise CalibrationError("Training argument separator normalization changed")
    features = {
        "observation.images.camera1": {"dtype": "video", "shape": [480, 640, 3]},
        "observation.state": {"dtype": "float32", "shape": [6]},
    }
    dataset, input_shapes = _dataset_contract(
        {"features": features},
        "org/dataset",
        "b" * 40,
        {"observation.images.camera1": "observation.images.base_0_rgb"},
    )
    if dataset["features_sha256"] != sha256_bytes(canonical_json(features).encode("utf-8")):
        raise CalibrationError("Dataset feature hashing changed")
    if input_shapes != {"observation.images.base_0_rgb": [3, 480, 640]}:
        raise CalibrationError("Dataset image shape normalization changed")
    previous_dataset_revision = os.environ.get("DATASET_REVISION")
    os.environ["DATASET_REVISION"] = "b" * 40
    try:
        arguments = _build_probe_arguments(
            ("--dataset.repo_id=org/dataset", "--dataset.root=/tmp/data", "--policy.type=pi0"),
            2,
            Path("/tmp/probe"),
        )
    finally:
        if previous_dataset_revision is None:
            os.environ.pop("DATASET_REVISION", None)
        else:
            os.environ["DATASET_REVISION"] = previous_dataset_revision
    required_arguments = {
        "--batch_size=2",
        f"--dataset.revision={'b' * 40}",
        "--steps=1",
        "--save_checkpoint=false",
    }
    if not required_arguments.issubset(arguments):
        raise CalibrationError("Probe arguments do not enforce a bounded optimizer step")

    success = {
        "micro_batch_size": 1,
        "outcome": "success",
        "device_name": "test-gpu",
        "peak_allocated_bytes": 60,
        "peak_reserved_bytes": 70,
        "free_bytes_after_step": 30,
        "total_bytes": 100,
        "duration_seconds": 0.5,
        "samples_per_second": 2.0,
        "optimizer_steps": 1,
    }
    oom = {"micro_batch_size": 2, "outcome": "oom", "error_type": "OutOfMemoryError"}
    report = _build_report(workload, [success, oom], 0.1)
    if report["recommendation"]["micro_batch_size"] != 1:
        raise CalibrationError("Calibration recommendation did not select the safe candidate")

    changed_workload = dict(workload, precision="fp16")
    changed_report = dict(report, workload=changed_workload)
    try:
        validate_record(changed_report, RecordKind.CALIBRATION)
    except ContractError:
        pass
    else:
        raise CalibrationError("A changed workload retained the original calibration fingerprint")

    infeasible = dict(success, peak_reserved_bytes=95)
    infeasible_report = _build_report(workload, [infeasible, oom], 0.1)
    if infeasible_report["recommendation"]["status"] != "infeasible":
        raise CalibrationError("Insufficient headroom produced a recommendation")


def create_parser() -> argparse.ArgumentParser:
    """Create the calibration command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="Run deterministic contract checks")
    parser.add_argument("--workload-contract", type=Path, help="Generated calibration workload JSON")
    parser.add_argument("--workload-output-dir", type=Path, help="Directory for workload.json")
    parser.add_argument("--output-dir", type=Path, help="Directory for calibration-report.json")
    parser.add_argument("--calibration-report", type=Path, help="Calibration report used by training")
    parser.add_argument("--resolve-training-batch-size", action="store_true")
    parser.add_argument("--candidate-batch-sizes", default="1", help="Ascending comma-separated batch sizes")
    parser.add_argument("--headroom-fraction", type=float, default=0.1)
    parser.add_argument("--probe-timeout-seconds", type=int, default=3600)
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--probe-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--probe-batch-size", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--expected-world-size", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("training_arguments", nargs=argparse.REMAINDER)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute calibration, a child probe, or self-checks."""
    if args.self_check:
        _run_self_check()
        print("VLA calibration self-check passed")
        return EXIT_SUCCESS
    if args.probe:
        if args.probe_output is None or args.probe_batch_size is None:
            raise CalibrationError("Probe mode requires --probe-output and --probe-batch-size")
        return _run_probe(args)
    if args.resolve_training_batch_size:
        if args.workload_contract is None or args.calibration_report is None:
            raise CalibrationError(
                "Training batch-size resolution requires --workload-contract and --calibration-report"
            )
        return _resolve_training_batch_size(args)
    if args.workload_output_dir is None or args.output_dir is None:
        raise CalibrationError("Calibration requires --workload-output-dir and --output-dir")
    if not 0 <= args.headroom_fraction < 1:
        raise CalibrationError("--headroom-fraction must be in the range [0, 1)")
    if args.probe_timeout_seconds < 1:
        raise CalibrationError("--probe-timeout-seconds must be positive")
    return _run_calibration(args)


def main() -> int:
    """Run the VLA calibration CLI."""
    try:
        return run(create_parser().parse_args())
    except (AdapterError, CalibrationError, ContractError) as exc:
        print(f"Calibration failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
