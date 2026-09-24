"""Behavior tests for the shared Viewer release verifier."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from conftest import load_training_module

release_verifier = load_training_module(
    "release_verifier",
    "training/il/scripts/lerobot/release_verifier.py",
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(episode_index: int) -> dict[str, object]:
    return {
        "dataset_id": "dataset-1",
        "episode_index": episode_index,
        "source_format": "lerobot",
        "format_version": "3.0",
        "source_digest": f"{episode_index + 1:064x}",
        "files": [{"relative_path": "meta/info.json", "size_bytes": 2, "sha256": "b" * 64}],
    }


def _decision(decision_id: str, disposition: str, episode_index: int) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "decision_id": decision_id,
        "decision": disposition,
        "reason_codes": ["reviewed"],
        "notes": None,
        "actor_id": "reviewer",
        "created_at": "2025-01-01T00:00:00Z",
        "source": _source(episode_index),
        "annotation_revision_id": f"annotation-{episode_index}",
        "edit_revision_id": f"edit-{episode_index}",
        "quality_run_id": f"quality-{episode_index}",
    }


def _finalize_fixture(root: Path, *, required_outcome: str = "pass", manifest_version: str = "2.0.0") -> None:
    accepted = _decision("decision-0", "accept", 0)
    rejected = _decision("decision-1", "reject", 1)
    _write_json(
        root / "metadata/accepted.json",
        {
            "schema_version": "1.0.0",
            "disposition": "accept",
            "decisions": [accepted],
            "episode_index_mapping": {"0": 0},
        },
    )
    _write_json(
        root / "metadata/rejected.json",
        {
            "schema_version": "1.0.0",
            "disposition": "reject",
            "decisions": [rejected],
            "episode_index_mapping": {},
        },
    )
    _write_json(
        root / "metadata/excluded.json",
        {
            "schema_version": "1.0.0",
            "dataset_id": "dataset-1",
            "candidates": [
                {
                    "episode_index": 2,
                    "disposition": "excluded",
                    "reason_codes": ["unreviewed"],
                    "review_reason_codes": [],
                    "decision_id": None,
                    "quality_run_id": None,
                    "failed_check_ids": [],
                    "source": None,
                }
            ],
        },
    )
    _write_json(
        root / "metadata/quality/quality-0.json",
        {
            "schema_version": "1.0.0",
            "run_id": "quality-0",
            "check_set_version": "1.0.0",
            "source": _source(0),
            "actor_id": "reviewer",
            "created_at": "2025-01-01T00:00:00Z",
            "episode_checks": [
                {"check_id": "source.identity", "required": True, "outcome": required_outcome, "reason_codes": []}
            ],
            "package_checks": [],
        },
    )
    _write_json(
        root / "metadata/package-quality.json",
        {
            "schema_version": "1.0.0",
            "target_format": {"name": "lerobot", "version": "3.0"},
            "episode_count": 1,
            "frame_count": 1,
            "episode_frame_counts": {"0": 1},
            "features": ["action"],
            "nonvisual_rows_read_back": 1,
            "visual_samples": [],
            "inventory_verified": True,
            "checksums_verified": True,
        },
    )
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data/episode.parquet").write_bytes(b"episode")
    inventory_paths = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"release-manifest.json", "checksums.sha256", ".published.json"}
    )
    files = [
        {
            "path": relative_path,
            "size_bytes": (root / relative_path).stat().st_size,
            "sha256": _digest(root / relative_path),
        }
        for relative_path in inventory_paths
    ]
    manifest = {
        "schema_version": manifest_version,
        "release_id": "release-1",
        "created_at": "2025-01-01T00:00:00Z",
        "actor_id": "publisher",
        "source_provenance": [_source(0)],
        "accepted_decision_ids": ["decision-0"],
        "rejected_decision_ids": ["decision-1"],
        "excluded_episode_indices": [2],
        "episode_index_mapping": {"0": 0},
        "source_formats": [{"name": "lerobot", "version": "3.0"}],
        "target_format": {"name": "lerobot", "version": "3.0"},
        "adapter_versions": {"lerobot": "0.6.1"},
        "tool_versions": {"dataviewer": "0.1.0"},
        "feature_schema": {"action": {"dtype": "float32", "shape": [1]}},
        "candidate_count": 3,
        "accepted_count": 1,
        "rejected_count": 1,
        "excluded_count": 1,
        "nonincluded_count": 2,
        "episode_count": 1,
        "frame_count": 1,
        "quality_evidence": [
            {
                "source_episode_index": 0,
                "release_episode_index": 0,
                "decision_id": "decision-0",
                "quality_run_id": "quality-0",
                "quality_report_path": "metadata/quality/quality-0.json",
                "check_set_version": "1.0.0",
                "required_outcome": "pass",
            }
        ],
        "package_quality_path": "metadata/package-quality.json",
        "files": files,
    }
    _write_json(root / "metadata/release-manifest.json", manifest)
    covered = ["metadata/release-manifest.json", *inventory_paths]
    (root / "checksums.sha256").write_text(
        "".join(f"{_digest(root / relative_path)}  {relative_path}\n" for relative_path in covered),
        encoding="utf-8",
    )
    _write_json(
        root / ".published.json",
        {"schema_version": "1.0.0", "dataset_id": "dataset-1", "release_id": "release-1", "owner": "job-1"},
    )


def _resign_fixture(root: Path) -> None:
    manifest_path = root / "metadata/release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = root / entry["path"]
        entry["size_bytes"] = path.stat().st_size
        entry["sha256"] = _digest(path)
    _write_json(manifest_path, manifest)
    covered = ["metadata/release-manifest.json", *(entry["path"] for entry in manifest["files"])]
    (root / "checksums.sha256").write_text(
        "".join(f"{_digest(root / relative_path)}  {relative_path}\n" for relative_path in covered),
        encoding="utf-8",
    )


def test_given_valid_release_when_verified_then_bounded_summary_is_returned(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path)

    # Act
    summary = release_verifier.verify_release(tmp_path, expected_target_format=("lerobot", "3.0"))

    # Assert
    assert summary.release_id == "release-1"
    assert summary.episode_count == 1
    assert summary.accepted_decision_ids == ("decision-0",)
    assert summary.source_dataset_ids == ("dataset-1",)
    assert len(summary.manifest_evidence_digest) == 64


def test_given_tampered_payload_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path)
    (tmp_path / "data/episode.parquet").write_bytes(b"tampered")

    # Act and assert
    with pytest.raises(release_verifier.ReleaseVerificationError, match="SHA-256"):
        release_verifier.verify_release(tmp_path)


def test_given_missing_marker_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path)
    (tmp_path / ".published.json").unlink()

    # Act and assert
    with pytest.raises(release_verifier.ReleaseVerificationError, match="publication marker"):
        release_verifier.verify_release(tmp_path)


def test_given_unsupported_manifest_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path, manifest_version="3.0.0")

    # Act and assert
    with pytest.raises(release_verifier.ReleaseVerificationError, match="schema version"):
        release_verifier.verify_release(tmp_path)


def test_given_failed_required_quality_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path, required_outcome="fail")

    # Act and assert
    with pytest.raises(release_verifier.ReleaseVerificationError, match="required quality check"):
        release_verifier.verify_release(tmp_path)


def test_given_extra_file_or_symlink_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path)
    (tmp_path / "extra.bin").write_bytes(b"extra")

    # Act and assert
    with pytest.raises(release_verifier.ReleaseVerificationError, match="Exact release inventory"):
        release_verifier.verify_release(tmp_path)

    (tmp_path / "extra.bin").unlink()
    (tmp_path / "payload-link").symlink_to(tmp_path / "data/episode.parquet")
    with pytest.raises(release_verifier.ReleaseVerificationError, match="symlink"):
        release_verifier.verify_release(tmp_path)


def test_given_traversal_checksum_path_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path)
    checksums = (tmp_path / "checksums.sha256").read_text(encoding="utf-8")
    (tmp_path / "checksums.sha256").write_text(
        checksums.replace("metadata/accepted.json", "../accepted.json"),
        encoding="utf-8",
    )

    # Act and assert
    with pytest.raises(release_verifier.ReleaseVerificationError, match="normalized relative POSIX"):
        release_verifier.verify_release(tmp_path)


def test_given_marker_or_ledger_identity_mismatch_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path)
    marker_path = tmp_path / ".published.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["release_id"] = "release-other"
    _write_json(marker_path, marker)

    # Act and assert
    with pytest.raises(release_verifier.ReleaseVerificationError, match="marker release identity"):
        release_verifier.verify_release(tmp_path)

    marker["release_id"] = "release-1"
    _write_json(marker_path, marker)
    rejected_path = tmp_path / "metadata/rejected.json"
    rejected = json.loads(rejected_path.read_text(encoding="utf-8"))
    rejected["decisions"][0]["source"] = _source(0)
    _write_json(rejected_path, rejected)
    _resign_fixture(tmp_path)
    with pytest.raises(release_verifier.ReleaseVerificationError, match="ledgers overlap"):
        release_verifier.verify_release(tmp_path)


def test_given_incomplete_visual_readback_when_verified_then_release_is_rejected(tmp_path: Path) -> None:
    # Arrange
    _finalize_fixture(tmp_path)
    manifest_path = tmp_path / "metadata/release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["feature_schema"]["observation.images.cam0"] = {"dtype": "video", "shape": [2, 2, 3]}
    _write_json(manifest_path, manifest)
    package_path = tmp_path / "metadata/package-quality.json"
    package_quality = json.loads(package_path.read_text(encoding="utf-8"))
    package_quality["features"].append("observation.images.cam0")
    _write_json(package_path, package_quality)
    _resign_fixture(tmp_path)

    # Act and assert
    with pytest.raises(release_verifier.ReleaseVerificationError, match="visual sample coverage"):
        release_verifier.verify_release(tmp_path)
