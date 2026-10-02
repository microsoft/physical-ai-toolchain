"""Select and fingerprint one adapter-valid VLA policy candidate."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from .model_adapters import AdapterRequest, get_adapter
    from .vla_contracts import (
        SCHEMA_VERSION,
        ContractError,
        RecordKind,
        canonical_json,
        dataset_identity_fingerprint,
        fingerprint,
        load_record,
        sha256_bytes,
        sha256_file,
        write_record,
    )
except ImportError:
    from model_adapters import AdapterRequest, get_adapter
    from vla_contracts import (
        SCHEMA_VERSION,
        ContractError,
        RecordKind,
        canonical_json,
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


def _required_files(adapter_name: str, policy_type: str) -> tuple[str, ...]:
    resolution = get_adapter(adapter_name).resolve(AdapterRequest(policy_type=policy_type))
    return resolution.required_model_files


def resolve_policy_root(checkpoints: Path, required_files: tuple[str, ...]) -> Path:
    """Resolve exactly one policy root using preferred checkpoint layouts first."""
    preferred = (checkpoints / "last/pretrained_model", checkpoints / "pretrained_model", checkpoints)
    for candidate in preferred:
        if candidate.is_dir() and all((candidate / name).is_file() for name in required_files):
            return candidate
    candidates = sorted(
        {
            config.parent
            for config in checkpoints.rglob("config.json")
            if all((config.parent / name).is_file() for name in required_files)
        }
    )
    if not candidates:
        raise ContractError("Checkpoint output contains no adapter-valid policy root")
    if len(candidates) != 1:
        paths = ", ".join(path.relative_to(checkpoints).as_posix() for path in candidates)
        raise ContractError(f"Checkpoint output contains ambiguous policy roots: {paths}")
    return candidates[0]


def _file_manifest(candidate: Path) -> list[dict[str, str]]:
    entries = [
        {"path": path.relative_to(candidate).as_posix(), "sha256": sha256_file(path)}
        for path in sorted(candidate.rglob("*"))
        if path.is_file()
    ]
    if not entries:
        raise ContractError("Candidate contains no files")
    return entries


def verify_candidate(candidate: Path, manifest_path: Path) -> dict[str, Any]:
    """Verify candidate files against a previously emitted candidate manifest."""
    record = load_record(manifest_path, RecordKind.CANDIDATE)
    actual = sha256_bytes(canonical_json(_file_manifest(candidate)).encode("utf-8"))
    if actual != record["artifact_manifest_sha256"]:
        raise ContractError("Candidate artifact manifest does not match candidate files")
    return record


def finalize_candidate(
    checkpoints: Path,
    training_record_path: Path,
    dataset_manifest_path: Path,
    candidate_output: Path,
    manifest_output: Path,
    adapter_name: str,
    policy_type: str,
    evidence_job: str,
) -> dict[str, Any]:
    """Copy one validated policy root and write its immutable candidate record."""
    required_files = _required_files(adapter_name, policy_type)
    source = resolve_policy_root(checkpoints, required_files)
    if candidate_output.exists():
        shutil.rmtree(candidate_output)
    shutil.copytree(source, candidate_output)
    files = _file_manifest(candidate_output)
    training_record = load_record(training_record_path, RecordKind.RUN)
    dataset_record = load_record(dataset_manifest_path, RecordKind.DATASET)
    output_prefix = f"azureml://jobs/{evidence_job}/outputs"
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": RecordKind.CANDIDATE.value,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "policy_type": policy_type,
        "training_fingerprint": fingerprint(training_record),
        "dataset_fingerprint": dataset_identity_fingerprint(dataset_record),
        "artifact_manifest_sha256": sha256_bytes(canonical_json(files).encode("utf-8")),
        "evidence_job": evidence_job,
        "candidate_output": f"{output_prefix}/candidate",
        "manifest_output": f"{output_prefix}/candidate_manifest",
        "files": files,
    }
    write_record(manifest_output / "candidate-manifest.json", record)
    return record


def create_parser() -> argparse.ArgumentParser:
    """Create the candidate-finalization command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--training-record", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--adapter-name", required=True)
    parser.add_argument("--policy-type", required=True)
    return parser


def run(args: argparse.Namespace) -> int:
    """Finalize one candidate from component inputs."""
    evidence_job = os.environ.get("AZUREML_ROOT_RUN_ID") or os.environ.get("AZUREML_RUN_ID", "")
    if not evidence_job:
        raise ContractError("Azure ML evidence job identity is required")
    record = finalize_candidate(
        args.checkpoints,
        args.training_record / "training-record.json",
        args.dataset_manifest / "dataset.json",
        args.candidate_output,
        args.manifest_output,
        args.adapter_name,
        args.policy_type,
        evidence_job,
    )
    print(fingerprint(record))
    return EXIT_SUCCESS


def main() -> int:
    """Run candidate finalization."""
    try:
        return run(create_parser().parse_args())
    except ContractError as exc:
        print(f"Candidate finalization failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
