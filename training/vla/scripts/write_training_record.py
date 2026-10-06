"""Write a VLA training record after successful checkpoint publication."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    from .vla_contracts import (
        SCHEMA_VERSION,
        ContractError,
        RecordKind,
        dataset_identity_fingerprint,
        fingerprint,
        load_record,
        sha256_bytes,
        sha256_file,
        write_record,
    )
except ImportError:
    from vla_contracts import (
        SCHEMA_VERSION,
        ContractError,
        RecordKind,
        dataset_identity_fingerprint,
        fingerprint,
        load_record,
        sha256_bytes,
        sha256_file,
        write_record,
    )

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2


def checkpoint_manifest_fingerprint(checkpoints: Path) -> str:
    """Fingerprint all checkpoint files by relative path and content digest."""
    if not checkpoints.is_dir():
        raise ContractError(f"Checkpoint output is not a directory: {checkpoints}")
    entries = [
        {"path": path.relative_to(checkpoints).as_posix(), "sha256": sha256_file(path)}
        for path in sorted(checkpoints.rglob("*"))
        if path.is_file()
    ]
    if not entries:
        raise ContractError("Checkpoint output contains no files")
    return sha256_bytes(json.dumps(entries, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def build_training_record(
    checkpoints: Path,
    workload_path: Path,
    dataset_manifest_path: Path,
    run_id: str,
    micro_batch_size: int,
    world_size: int,
    accumulation_steps: int,
) -> dict[str, object]:
    """Build a validated run record from immutable training evidence."""
    if not run_id.strip():
        raise ContractError("Azure ML or MLflow run identity is required")
    try:
        workload = json.loads(workload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Unable to read workload contract: {exc}") from exc
    if not isinstance(workload, dict):
        raise ContractError("Workload contract must be a JSON object")
    dataset_record = load_record(dataset_manifest_path, RecordKind.DATASET)
    effective_total = micro_batch_size * world_size * accumulation_steps
    record: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": RecordKind.RUN.value,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "identities": {
            "workload": sha256_bytes(json.dumps(workload, separators=(",", ":"), sort_keys=True).encode("utf-8")),
            "dataset": dataset_identity_fingerprint(dataset_record),
            "checkpoints": checkpoint_manifest_fingerprint(checkpoints),
        },
        "effective_batch": {
            "micro_batch_per_rank": micro_batch_size,
            "world_size": world_size,
            "accumulation_steps": accumulation_steps,
            "total": effective_total,
        },
    }
    fingerprint(record)
    return record


def create_parser() -> argparse.ArgumentParser:
    """Create the training-record command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--workload-contract", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    """Create and write the post-training lifecycle record."""
    run_id = os.environ.get("AZUREML_RUN_ID") or os.environ.get("MLFLOW_RUN_ID", "")
    record = build_training_record(
        args.checkpoints,
        args.workload_contract,
        args.dataset_manifest,
        run_id,
        int(os.environ.get("BATCH_SIZE", "1")),
        int(os.environ.get("WORLD_SIZE", "1")),
        int(os.environ.get("GRADIENT_ACCUMULATION_STEPS", "1")),
    )
    write_record(args.output / "training-record.json", record)
    print(fingerprint(record))
    return EXIT_SUCCESS


def main() -> int:
    """Run the training-record writer."""
    try:
        return run(create_parser().parse_args())
    except (ContractError, ValueError) as exc:
        print(f"Training record failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
