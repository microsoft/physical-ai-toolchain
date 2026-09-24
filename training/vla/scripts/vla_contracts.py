"""Validate and fingerprint VLA lifecycle evidence records."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CALIBRATION_WORKLOAD_SCHEMA_VERSION = 1
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2

_FULL_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_PATTERN = re.compile(r"(?:authorization|password|secret|token)", re.IGNORECASE)


class ContractError(ValueError):
    """Raised when a lifecycle record violates its contract."""


class RecordKind(StrEnum):
    """Supported lifecycle record kinds."""

    SOURCE_MODEL = "source_model"
    DATASET = "dataset"
    CODE = "code"
    RUNTIME = "runtime"
    ADAPTER = "adapter"
    COMPUTE = "compute"
    CALIBRATION = "calibration"
    APPROVAL = "approval"
    RUN = "run"
    EVALUATION = "evaluation"
    INCIDENT = "incident"
    PROMOTION = "promotion"


_REQUIRED_FIELDS: dict[RecordKind, tuple[str, ...]] = {
    RecordKind.SOURCE_MODEL: ("repository", "revision", "manifest_sha256"),
    RecordKind.DATASET: ("uri", "version", "features_sha256"),
    RecordKind.CODE: ("repository", "revision"),
    RecordKind.RUNTIME: ("image", "lock_sha256"),
    RecordKind.ADAPTER: ("name", "version", "config_sha256"),
    RecordKind.COMPUTE: ("target", "gpu_type", "gpu_count"),
    RecordKind.CALIBRATION: (
        "workload_fingerprint",
        "candidate_results",
        "recommendation",
        "headroom_fraction",
    ),
    RecordKind.APPROVAL: (
        "calibration_report_sha256",
        "workload_fingerprint",
        "approver",
        "approved_at",
    ),
    RecordKind.RUN: ("run_id", "identities", "effective_batch"),
    RecordKind.EVALUATION: ("evaluation_id", "run_fingerprint", "status", "outputs_complete"),
    RecordKind.INCIDENT: ("stage", "category", "terminal_state", "retry_count"),
    RecordKind.PROMOTION: ("model_name", "training_fingerprint", "evaluation_fingerprint"),
}


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically."""
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 digest for bytes."""
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(record: Mapping[str, Any]) -> str:
    """Validate and return the canonical SHA-256 fingerprint for a record."""
    validate_record(record)
    return sha256_bytes(canonical_json(record).encode("utf-8"))


def calibration_workload_fingerprint(workload: Mapping[str, Any]) -> str:
    """Validate and fingerprint a calibration workload."""
    validate_calibration_workload(workload)
    return sha256_bytes(canonical_json(workload).encode("utf-8"))


def validate_calibration_workload(workload: Mapping[str, Any]) -> None:
    """Validate the immutable inputs that determine calibration compatibility."""
    _reject_secret_fields(workload, "workload")
    _reject_non_finite_numbers(workload, "workload")
    if workload.get("schema_version") != CALIBRATION_WORKLOAD_SCHEMA_VERSION:
        raise ContractError(
            f"workload.schema_version must equal {CALIBRATION_WORKLOAD_SCHEMA_VERSION}"
        )

    source_model = _require_mapping(workload, "source_model")
    _require_string(source_model, "repository")
    revision = _require_string(source_model, "revision")
    if not _FULL_COMMIT_PATTERN.fullmatch(revision):
        raise ContractError("workload.source_model.revision must be a full lowercase 40-character Git commit")

    dataset = _require_mapping(workload, "dataset")
    _require_string(dataset, "uri")
    _require_string(dataset, "version")
    _require_sha256(dataset, "features_sha256")

    code = _require_mapping(workload, "code")
    _require_string(code, "repository")
    code_revision = _require_string(code, "revision")
    if not _FULL_COMMIT_PATTERN.fullmatch(code_revision):
        raise ContractError("workload.code.revision must be a full lowercase 40-character Git commit")

    runtime = _require_mapping(workload, "runtime")
    image = _require_string(runtime, "image")
    if "@sha256:" not in image:
        raise ContractError("workload.runtime.image must include a sha256 digest")
    _require_sha256(runtime, "lock_sha256")

    adapter = _require_mapping(workload, "adapter")
    _require_string(adapter, "name")
    _require_string(adapter, "version")
    _require_sha256(adapter, "config_sha256")

    compute = _require_mapping(workload, "compute")
    _require_string(compute, "target")
    _require_string(compute, "gpu_type")
    gpu_count = compute.get("gpu_count")
    if not isinstance(gpu_count, int) or isinstance(gpu_count, bool) or gpu_count < 1:
        raise ContractError("workload.compute.gpu_count must be a positive integer")

    if workload.get("precision") not in {"no", "fp16", "bf16"}:
        raise ContractError("workload.precision must be no, fp16, or bf16")
    _require_string(workload, "trainable_scope")
    if not isinstance(workload.get("gradient_checkpointing"), bool):
        raise ContractError("workload.gradient_checkpointing must be a boolean")
    world_size = workload.get("world_size")
    if not isinstance(world_size, int) or isinstance(world_size, bool) or world_size < 1:
        raise ContractError("workload.world_size must be a positive integer")
    if world_size != gpu_count:
        raise ContractError("workload.world_size must match workload.compute.gpu_count")

    input_shapes = _require_mapping(workload, "input_shapes")
    if not input_shapes:
        raise ContractError("workload.input_shapes must not be empty")
    for name, shape in input_shapes.items():
        if not isinstance(name, str) or not name:
            raise ContractError("workload.input_shapes keys must be non-empty strings")
        if not isinstance(shape, Sequence) or isinstance(shape, (str, bytes, bytearray)) or not shape:
            raise ContractError(f"workload.input_shapes.{name} must be a non-empty array")
        if any(not isinstance(size, int) or isinstance(size, bool) or size < 1 for size in shape):
            raise ContractError(f"workload.input_shapes.{name} must contain positive integers")


def load_record(path: Path, expected_kind: RecordKind | None = None) -> dict[str, Any]:
    """Load and validate a lifecycle record from a JSON file."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Unable to load lifecycle record {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise ContractError(f"Lifecycle record {path} must be a JSON object")
    validate_record(record, expected_kind)
    return record


def write_record(path: Path, record: Mapping[str, Any]) -> str:
    """Validate and write a lifecycle record, returning its file digest."""
    validate_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return sha256_file(path)


def validate_record(record: Mapping[str, Any], expected_kind: RecordKind | None = None) -> None:
    """Validate common and kind-specific lifecycle record invariants."""
    _reject_secret_fields(record)
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(f"schema_version must equal {SCHEMA_VERSION}")
    kind = _parse_kind(record.get("kind"))
    if expected_kind is not None and kind != expected_kind:
        raise ContractError(f"Expected {expected_kind.value} record, received {kind.value}")
    _require_timestamp(record, "created_at")
    _require_fields(record, _REQUIRED_FIELDS[kind])
    _reject_non_finite_numbers(record)

    validators = {
        RecordKind.SOURCE_MODEL: _validate_source_model,
        RecordKind.DATASET: _validate_dataset,
        RecordKind.CODE: _validate_code,
        RecordKind.RUNTIME: _validate_runtime,
        RecordKind.ADAPTER: _validate_adapter,
        RecordKind.COMPUTE: _validate_compute,
        RecordKind.CALIBRATION: _validate_calibration,
        RecordKind.APPROVAL: _validate_approval,
        RecordKind.RUN: _validate_run,
        RecordKind.EVALUATION: _validate_evaluation,
        RecordKind.INCIDENT: _validate_incident,
        RecordKind.PROMOTION: _validate_promotion,
    }
    validators[kind](record)


def verify_approval(calibration_report: Path, approval_record: Mapping[str, Any]) -> None:
    """Verify that approval is bound to the exact calibration report bytes."""
    report = load_record(calibration_report, RecordKind.CALIBRATION)
    validate_record(approval_record, RecordKind.APPROVAL)
    expected_digest = approval_record["calibration_report_sha256"]
    actual_digest = sha256_file(calibration_report)
    if actual_digest != expected_digest:
        raise ContractError("Approval does not match the calibration report SHA-256")
    if approval_record["workload_fingerprint"] != report["workload_fingerprint"]:
        raise ContractError("Approval workload fingerprint does not match the calibration report")


def _parse_kind(value: Any) -> RecordKind:
    if not isinstance(value, str):
        raise ContractError("kind must be a string")
    try:
        return RecordKind(value)
    except ValueError as exc:
        raise ContractError(f"Unsupported lifecycle record kind: {value}") from exc


def _require_fields(record: Mapping[str, Any], fields: Sequence[str]) -> None:
    missing = [field for field in fields if field not in record or record[field] in (None, "")]
    if missing:
        raise ContractError(f"Missing required fields: {', '.join(missing)}")


def _require_mapping(record: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = record.get(field)
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    return value


def _require_string(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value


def _require_sha256(record: Mapping[str, Any], field: str) -> None:
    value = _require_string(record, field)
    if not _SHA256_PATTERN.fullmatch(value):
        raise ContractError(f"{field} must be a lowercase SHA-256 digest")


def _require_timestamp(record: Mapping[str, Any], field: str) -> None:
    value = _require_string(record, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{field} must include a timezone")


def _reject_secret_fields(value: Any, path: str = "record") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _SECRET_KEY_PATTERN.search(str(key)):
                raise ContractError(f"Secret-shaped field is forbidden: {path}.{key}")
            _reject_secret_fields(nested, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _reject_secret_fields(nested, f"{path}[{index}]")


def _reject_non_finite_numbers(value: Any, path: str = "record") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractError(f"Non-finite number is forbidden: {path}")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_non_finite_numbers(nested, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _reject_non_finite_numbers(nested, f"{path}[{index}]")


def _validate_source_model(record: Mapping[str, Any]) -> None:
    _require_string(record, "repository")
    revision = _require_string(record, "revision")
    if not _FULL_COMMIT_PATTERN.fullmatch(revision):
        raise ContractError("revision must be a full lowercase 40-character Git commit")
    _require_sha256(record, "manifest_sha256")


def _validate_dataset(record: Mapping[str, Any]) -> None:
    _require_string(record, "uri")
    _require_string(record, "version")
    _require_sha256(record, "features_sha256")


def _validate_code(record: Mapping[str, Any]) -> None:
    _require_string(record, "repository")
    revision = _require_string(record, "revision")
    if not _FULL_COMMIT_PATTERN.fullmatch(revision):
        raise ContractError("code revision must be a full lowercase 40-character Git commit")


def _validate_runtime(record: Mapping[str, Any]) -> None:
    image = _require_string(record, "image")
    if "@sha256:" not in image:
        raise ContractError("runtime image must include a sha256 digest")
    _require_sha256(record, "lock_sha256")


def _validate_adapter(record: Mapping[str, Any]) -> None:
    _require_string(record, "name")
    _require_string(record, "version")
    _require_sha256(record, "config_sha256")


def _validate_compute(record: Mapping[str, Any]) -> None:
    _require_string(record, "target")
    _require_string(record, "gpu_type")
    if not isinstance(record.get("gpu_count"), int) or record["gpu_count"] < 1:
        raise ContractError("gpu_count must be a positive integer")


def _validate_calibration(record: Mapping[str, Any]) -> None:
    _require_sha256(record, "workload_fingerprint")
    workload = _require_mapping(record, "workload")
    actual_fingerprint = calibration_workload_fingerprint(workload)
    if record["workload_fingerprint"] != actual_fingerprint:
        raise ContractError("workload_fingerprint does not match workload")
    candidates = record.get("candidate_results")
    if not isinstance(candidates, list) or not candidates:
        raise ContractError("candidate_results must be a non-empty list")
    seen_batch_sizes: set[int] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise ContractError(f"candidate_results[{index}] must be an object")
        batch_size = candidate.get("micro_batch_size")
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ContractError(f"candidate_results[{index}].micro_batch_size must be a positive integer")
        if batch_size in seen_batch_sizes:
            raise ContractError("candidate_results contains duplicate micro_batch_size values")
        seen_batch_sizes.add(batch_size)
        outcome = candidate.get("outcome")
        if outcome not in {"success", "oom", "timeout", "failed"}:
            raise ContractError(f"candidate_results[{index}].outcome is unsupported")
        if outcome == "success":
            for field in ("peak_allocated_bytes", "peak_reserved_bytes", "total_bytes", "optimizer_steps"):
                value = candidate.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise ContractError(f"candidate_results[{index}].{field} must be a positive integer")
            for field in ("duration_seconds", "samples_per_second"):
                value = candidate.get(field)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                    raise ContractError(f"candidate_results[{index}].{field} must be positive")
            if candidate["optimizer_steps"] != 1:
                raise ContractError(f"candidate_results[{index}].optimizer_steps must equal 1")
            if candidate["peak_allocated_bytes"] > candidate["peak_reserved_bytes"]:
                raise ContractError(f"candidate_results[{index}] allocated memory exceeds reserved memory")
            if candidate["peak_reserved_bytes"] > candidate["total_bytes"]:
                raise ContractError(f"candidate_results[{index}] reserved memory exceeds total memory")
    recommendation = _require_mapping(record, "recommendation")
    if recommendation.get("status") not in {"recommended", "infeasible"}:
        raise ContractError("recommendation.status must be recommended or infeasible")
    recommended_batch_size = recommendation.get("micro_batch_size")
    if recommendation["status"] == "recommended":
        successful_sizes = {
            candidate["micro_batch_size"] for candidate in candidates if candidate["outcome"] == "success"
        }
        if recommended_batch_size not in successful_sizes:
            raise ContractError("recommendation.micro_batch_size must identify a successful candidate")
    elif recommended_batch_size is not None:
        raise ContractError("an infeasible recommendation must use a null micro_batch_size")
    headroom = record.get("headroom_fraction")
    if not isinstance(headroom, (int, float)) or isinstance(headroom, bool) or not 0 <= headroom < 1:
        raise ContractError("headroom_fraction must be in the range [0, 1)")


def _validate_approval(record: Mapping[str, Any]) -> None:
    _require_sha256(record, "calibration_report_sha256")
    _require_sha256(record, "workload_fingerprint")
    _require_string(record, "approver")
    _require_timestamp(record, "approved_at")


def _validate_run(record: Mapping[str, Any]) -> None:
    _require_string(record, "run_id")
    identities = record.get("identities")
    if not isinstance(identities, Mapping) or not identities:
        raise ContractError("identities must be a non-empty object")
    for name, value in identities.items():
        if not isinstance(name, str) or not _SHA256_PATTERN.fullmatch(str(value)):
            raise ContractError("every run identity must be a named SHA-256 fingerprint")
    effective_batch = record.get("effective_batch")
    if not isinstance(effective_batch, Mapping):
        raise ContractError("effective_batch must be an object")
    for field in ("micro_batch_per_rank", "world_size", "accumulation_steps", "total"):
        value = effective_batch.get(field)
        if not isinstance(value, int) or value < 1:
            raise ContractError(f"effective_batch.{field} must be a positive integer")
    expected = (
        effective_batch["micro_batch_per_rank"]
        * effective_batch["world_size"]
        * effective_batch["accumulation_steps"]
    )
    if effective_batch["total"] != expected:
        raise ContractError("effective_batch.total does not match its factors")


def _validate_evaluation(record: Mapping[str, Any]) -> None:
    _require_string(record, "evaluation_id")
    _require_sha256(record, "run_fingerprint")
    if record.get("status") not in {"passed", "failed", "inconclusive"}:
        raise ContractError("evaluation status must be passed, failed, or inconclusive")
    if not isinstance(record.get("outputs_complete"), bool):
        raise ContractError("outputs_complete must be a boolean")


def _validate_incident(record: Mapping[str, Any]) -> None:
    _require_string(record, "stage")
    _require_string(record, "category")
    _require_string(record, "terminal_state")
    if not isinstance(record.get("retry_count"), int) or record["retry_count"] < 0:
        raise ContractError("retry_count must be a non-negative integer")


def _validate_promotion(record: Mapping[str, Any]) -> None:
    _require_string(record, "model_name")
    _require_sha256(record, "training_fingerprint")
    _require_sha256(record, "evaluation_fingerprint")


def _base_record(kind: RecordKind) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "kind": kind.value, "created_at": "2026-09-22T00:00:00Z"}


def _run_self_check() -> None:
    digest = "a" * 64
    commit = "b" * 40
    workload = {
        "schema_version": CALIBRATION_WORKLOAD_SCHEMA_VERSION,
        "source_model": {"repository": "org/model", "revision": commit},
        "dataset": {"uri": "azureml:data:1", "version": "1", "features_sha256": digest},
        "code": {"repository": "org/repo", "revision": commit},
        "runtime": {"image": f"image@sha256:{digest}", "lock_sha256": digest},
        "adapter": {"name": "lerobot-pi", "version": "1", "config_sha256": digest},
        "compute": {"target": "arc-gpu", "gpu_type": "test-gpu", "gpu_count": 1},
        "precision": "bf16",
        "trainable_scope": "expert-only",
        "gradient_checkpointing": True,
        "world_size": 1,
        "input_shapes": {"observation.images.base_0_rgb": [3, 224, 224]},
    }
    workload_fingerprint = calibration_workload_fingerprint(workload)
    records: list[dict[str, Any]] = [
        {
            **_base_record(RecordKind.SOURCE_MODEL),
            "repository": "org/model",
            "revision": commit,
            "manifest_sha256": digest,
        },
        {**_base_record(RecordKind.DATASET), "uri": "azureml:data:1", "version": "1", "features_sha256": digest},
        {**_base_record(RecordKind.CODE), "repository": "org/repo", "revision": commit},
        {**_base_record(RecordKind.RUNTIME), "image": f"image@sha256:{digest}", "lock_sha256": digest},
        {**_base_record(RecordKind.ADAPTER), "name": "lerobot-pi", "version": "1", "config_sha256": digest},
        {**_base_record(RecordKind.COMPUTE), "target": "arc-gpu", "gpu_type": "test-gpu", "gpu_count": 1},
        {
            **_base_record(RecordKind.CALIBRATION),
            "workload_fingerprint": workload_fingerprint,
            "workload": workload,
            "candidate_results": [
                {
                    "micro_batch_size": 1,
                    "outcome": "success",
                    "peak_allocated_bytes": 8,
                    "peak_reserved_bytes": 10,
                    "total_bytes": 16,
                    "duration_seconds": 1.0,
                    "samples_per_second": 1.0,
                    "optimizer_steps": 1,
                }
            ],
            "recommendation": {"status": "recommended", "micro_batch_size": 1},
            "headroom_fraction": 0.1,
        },
        {
            **_base_record(RecordKind.APPROVAL),
            "calibration_report_sha256": digest,
            "workload_fingerprint": workload_fingerprint,
            "approver": "release-operator",
            "approved_at": "2026-09-22T00:01:00Z",
        },
        {
            **_base_record(RecordKind.RUN),
            "run_id": "run-1",
            "identities": {"source_model": digest},
            "effective_batch": {
                "micro_batch_per_rank": 1,
                "world_size": 1,
                "accumulation_steps": 2,
                "total": 2,
            },
        },
        {
            **_base_record(RecordKind.EVALUATION),
            "evaluation_id": "eval-1",
            "run_fingerprint": digest,
            "status": "passed",
            "outputs_complete": True,
        },
        {
            **_base_record(RecordKind.INCIDENT),
            "stage": "delivery",
            "category": "network",
            "terminal_state": "failed",
            "retry_count": 1,
        },
        {
            **_base_record(RecordKind.PROMOTION),
            "model_name": "pi05-candidate",
            "training_fingerprint": digest,
            "evaluation_fingerprint": digest,
        },
    ]
    for record in records:
        validate_record(record)
        if fingerprint(record) != fingerprint(json.loads(canonical_json(record))):
            raise ContractError(f"Canonical fingerprint changed for {record['kind']}")

    invalid_revision = dict(records[0], revision="main")
    try:
        validate_record(invalid_revision)
    except ContractError:
        pass
    else:
        raise ContractError("Mutable source revision passed validation")

    secret_record = dict(records[0], access_token="forbidden")
    try:
        validate_record(secret_record)
    except ContractError:
        pass
    else:
        raise ContractError("Secret-shaped field passed validation")

    calibration = records[6]
    with tempfile.TemporaryDirectory() as temporary_directory:
        report_path = Path(temporary_directory) / "calibration.json"
        report_digest = write_record(report_path, calibration)
        approval = dict(records[7], calibration_report_sha256=report_digest)
        verify_approval(report_path, approval)
        report_path.write_text(report_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        try:
            verify_approval(report_path, approval)
        except ContractError:
            pass
        else:
            raise ContractError("Modified calibration report retained approval")


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="Validate built-in positive and negative cases")
    parser.add_argument("--record", type=Path, help="Validate a lifecycle record and print its fingerprint")
    parser.add_argument("--kind", choices=[kind.value for kind in RecordKind], help="Require a specific record kind")
    parser.add_argument(
        "--calibration-report",
        type=Path,
        help="Calibration report to verify against an approval record",
    )
    parser.add_argument("--approval", type=Path, help="Approval record bound to --calibration-report")
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the selected validation operation."""
    if args.self_check:
        _run_self_check()
        print("VLA lifecycle contract self-check passed")
        return EXIT_SUCCESS
    if args.calibration_report or args.approval:
        if not args.calibration_report or not args.approval:
            raise ContractError("--calibration-report and --approval must be provided together")
        approval = load_record(args.approval, RecordKind.APPROVAL)
        verify_approval(args.calibration_report, approval)
        print("Calibration approval is valid")
        return EXIT_SUCCESS
    if args.record:
        expected_kind = RecordKind(args.kind) if args.kind else None
        record = load_record(args.record, expected_kind)
        print(fingerprint(record))
        return EXIT_SUCCESS
    raise ContractError("Select --self-check, --record, or the calibration approval inputs")


def main() -> int:
    """Run the lifecycle contract CLI."""
    try:
        return run(create_parser().parse_args())
    except ContractError as exc:
        print(f"Contract validation failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
