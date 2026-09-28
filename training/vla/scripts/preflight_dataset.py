"""Validate a mounted LeRobot dataset and emit immutable identity evidence."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from .vla_contracts import (
        SCHEMA_VERSION,
        ContractError,
        RecordKind,
        canonical_json,
        dataset_identity_fingerprint,
        sha256_bytes,
        write_record,
    )
except ImportError:
    from vla_contracts import (
        SCHEMA_VERSION,
        ContractError,
        RecordKind,
        canonical_json,
        dataset_identity_fingerprint,
        sha256_bytes,
        write_record,
    )

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ERROR = 2
_MAX_METADATA_FILES = 1_000
_MAX_METADATA_BYTES = 16 * 1024 * 1024


def _load_info(dataset_path: Path) -> dict[str, Any]:
    info_path = dataset_path / "meta" / "info.json"
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Unable to read meta/info.json: {exc}") from exc
    if not isinstance(info, dict):
        raise ContractError("meta/info.json must be a JSON object")
    return info


def _validate_repo_id(dataset_repo_id: str) -> None:
    repo_path = Path(dataset_repo_id)
    if not dataset_repo_id.strip() or repo_path.is_absolute() or ".." in repo_path.parts:
        raise ContractError("dataset_repo_id must be a safe relative identifier")


def _validate_features(info: dict[str, Any]) -> dict[str, Any]:
    features = info.get("features")
    if not isinstance(features, dict) or not features:
        raise ContractError("meta/info.json features must be a non-empty object")
    has_visual_feature = any(
        isinstance(value, dict)
        and (value.get("dtype") in {"image", "video"} or name.startswith("observation.images."))
        for name, value in features.items()
    )
    if not has_visual_feature:
        raise ContractError("meta/info.json must declare at least one image or video observation feature")
    return features


def _metadata_fingerprint(dataset_path: Path) -> str:
    meta_path = dataset_path / "meta"
    if not meta_path.is_dir():
        raise ContractError("Dataset is missing required meta directory")
    files = sorted(path for path in meta_path.rglob("*") if path.is_file())
    if not files:
        raise ContractError("Dataset metadata directory is empty")
    if len(files) > _MAX_METADATA_FILES:
        raise ContractError(f"Dataset metadata exceeds {_MAX_METADATA_FILES} files")

    total_bytes = 0
    entries: list[dict[str, str]] = []
    for path in files:
        if path.is_symlink():
            raise ContractError(f"Dataset metadata must not contain symbolic links: {path.relative_to(meta_path)}")
        content = path.read_bytes()
        total_bytes += len(content)
        if total_bytes > _MAX_METADATA_BYTES:
            raise ContractError(f"Dataset metadata exceeds {_MAX_METADATA_BYTES} bytes")
        entries.append(
            {
                "path": path.relative_to(meta_path).as_posix(),
                "sha256": sha256_bytes(content),
            }
        )
    return sha256_bytes(canonical_json(entries).encode("utf-8"))


def build_dataset_record(
    dataset_path: Path,
    asset_id: str,
    dataset_repo_id: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Validate a dataset mount and return its lifecycle record."""
    if not dataset_path.is_dir():
        raise ContractError(f"Dataset path is not a directory: {dataset_path}")
    _validate_repo_id(dataset_repo_id)
    data_path = dataset_path / "data"
    if not data_path.is_dir() or not any(path.is_file() for path in data_path.rglob("*")):
        raise ContractError("Dataset data directory must contain at least one file")

    info = _load_info(dataset_path)
    total_episodes = info.get("total_episodes")
    if not isinstance(total_episodes, int) or isinstance(total_episodes, bool) or total_episodes < 1:
        raise ContractError("meta/info.json total_episodes must be a positive integer")
    features = _validate_features(info)
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": RecordKind.DATASET.value,
        "created_at": created_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "asset_id": asset_id,
        "dataset_repo_id": dataset_repo_id,
        "features_sha256": sha256_bytes(canonical_json(features).encode("utf-8")),
        "metadata_sha256": _metadata_fingerprint(dataset_path),
        "total_episodes": total_episodes,
    }
    dataset_identity_fingerprint(record)
    return record


def verify_dataset_manifest(
    dataset_path: Path,
    manifest_path: Path,
    asset_id: str,
    dataset_repo_id: str,
) -> dict[str, Any]:
    """Verify that a stored manifest describes the mounted dataset and declared identity."""
    try:
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Unable to read dataset manifest {manifest_path}: {exc}") from exc
    if not isinstance(expected, dict):
        raise ContractError("Dataset manifest must be a JSON object")
    actual = build_dataset_record(dataset_path, asset_id, dataset_repo_id, expected.get("created_at"))
    if dataset_identity_fingerprint(actual) != dataset_identity_fingerprint(expected):
        raise ContractError("Dataset manifest does not match the mounted dataset")
    return expected


def create_parser() -> argparse.ArgumentParser:
    """Create the dataset preflight command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True, help="Mounted LeRobot dataset directory")
    parser.add_argument("--asset-id", required=True, help="Immutable Azure ML data asset ID")
    parser.add_argument("--dataset-repo-id", required=True, help="Safe LeRobot dataset repository ID")
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument("--manifest-output", type=Path, help="Output directory for dataset.json")
    output_group.add_argument("--verify-manifest", type=Path, help="Existing dataset.json to verify")
    return parser


def run(args: argparse.Namespace) -> int:
    """Validate the mounted dataset and write its manifest."""
    if getattr(args, "verify_manifest", None):
        record = verify_dataset_manifest(args.dataset, args.verify_manifest, args.asset_id, args.dataset_repo_id)
        print(dataset_identity_fingerprint(record))
        return EXIT_SUCCESS
    record = build_dataset_record(args.dataset, args.asset_id, args.dataset_repo_id)
    output_path = args.manifest_output / "dataset.json"
    write_record(output_path, record)
    print(dataset_identity_fingerprint(record))
    return EXIT_SUCCESS


def main() -> int:
    """Run dataset preflight."""
    try:
        return run(create_parser().parse_args())
    except ContractError as exc:
        print(f"Dataset preflight failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
