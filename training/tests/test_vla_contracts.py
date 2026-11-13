"""Tests for VLA lifecycle evidence contracts."""

from __future__ import annotations

import copy
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from training.vla.scripts.vla_contracts import (
    CALIBRATION_WORKLOAD_SCHEMA_VERSION,
    ContractError,
    RecordKind,
    code_content_fingerprint,
    validate_calibration_workload,
    validate_record,
)

_DIGEST = "a" * 64
_OUTPUT_PREFIX = "azureml://jobs/evidence-job/outputs"


def _base_record(kind: RecordKind) -> dict[str, Any]:
    return {"schema_version": 1, "kind": kind.value, "created_at": "2026-09-28T00:00:00Z"}


@pytest.mark.parametrize(
    "record",
    [
        {
            **_base_record(RecordKind.DATASET),
            "asset_id": "azureml:robot-dataset:7",
            "dataset_repo_id": "org/robot-dataset",
            "features_sha256": _DIGEST,
            "metadata_sha256": _DIGEST,
            "total_episodes": 3,
        },
        {
            **_base_record(RecordKind.CANDIDATE),
            "policy_type": "pi0",
            "training_fingerprint": _DIGEST,
            "dataset_fingerprint": _DIGEST,
            "artifact_manifest_sha256": _DIGEST,
            "evidence_job": "evidence-job",
            "candidate_output": f"{_OUTPUT_PREFIX}/candidate",
            "manifest_output": f"{_OUTPUT_PREFIX}/candidate_manifest",
        },
    ],
)
def test_given_complete_training_evidence_when_validated_then_it_is_accepted(record: dict[str, Any]) -> None:
    validate_record(record)


def _calibration_workload() -> dict[str, Any]:
    commit = "b" * 40
    return {
        "schema_version": CALIBRATION_WORKLOAD_SCHEMA_VERSION,
        "source_model": {"repository": "org/model", "revision": commit},
        "dataset": {
            "asset_id": "azureml:robot-dataset:7",
            "dataset_fingerprint": _DIGEST,
            "features_sha256": _DIGEST,
            "metadata_sha256": _DIGEST,
            "total_episodes": 3,
        },
        "code": {"repository": "org/repo", "revision": commit, "content_sha256": _DIGEST},
        "runtime": {"image": f"runtime@sha256:{_DIGEST}", "lock_sha256": _DIGEST},
        "adapter": {"name": "lerobot-pi", "version": "1", "config_sha256": _DIGEST},
        "compute": {"target": "arc-gpu", "gpu_type": "test-gpu", "gpu_count": 1},
        "precision": "bf16",
        "trainable_scope": "expert-only",
        "gradient_checkpointing": True,
        "world_size": 1,
        "input_shapes": {"observation.images.base_0_rgb": [3, 224, 224]},
    }


def test_given_uploaded_code_digest_when_workload_validated_then_it_is_accepted() -> None:
    validate_calibration_workload(_calibration_workload())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(lambda workload: workload["code"].pop("content_sha256"), "content_sha256", id="missing-digest"),
        pytest.param(
            lambda workload: workload["code"].update(content_sha256="not-a-digest"),
            "content_sha256",
            id="malformed-digest",
        ),
        pytest.param(lambda workload: workload.update(schema_version=1), "schema_version", id="revision-only-schema"),
    ],
)
def test_given_unverified_code_identity_when_workload_validated_then_it_is_rejected(
    change: Callable[[dict[str, Any]], object], message: str
) -> None:
    workload = copy.deepcopy(_calibration_workload())
    change(workload)

    with pytest.raises(ContractError, match=message):
        validate_calibration_workload(workload)


def _write_code_root(root: Path) -> Path:
    (root / "vla/scripts").mkdir(parents=True)
    (root / "vla/scripts/entry.sh").write_text("#!/usr/bin/env bash\necho train\n", encoding="utf-8")
    (root / "vla/scripts/train.py").write_text("print('train')\n", encoding="utf-8")
    (root / "vla/workflow.yaml").write_text("name: vla\n", encoding="utf-8")
    return root


def test_given_runtime_and_documentation_files_when_code_fingerprinted_then_digest_is_unchanged(
    tmp_path: Path,
) -> None:
    root = _write_code_root(tmp_path / "code")
    expected = code_content_fingerprint(root)
    (root / "vla/scripts/__pycache__").mkdir()
    (root / "vla/scripts/__pycache__/train.cpython-312.pyc").write_bytes(b"bytecode")
    (root / ".venv/lib").mkdir(parents=True)
    (root / ".venv/lib/site.py").write_text("print('environment')\n", encoding="utf-8")
    (root / ".amlignore").write_text("__pycache__\n", encoding="utf-8")
    (root / "README.md").write_text("# Notes\n", encoding="utf-8")
    os.symlink(".", root / "training")
    os.symlink("vla/scripts/train.py", root / "linked.py")

    assert code_content_fingerprint(root) == expected


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            lambda root: (root / "vla/scripts/train.py").write_text("print('changed')\n", encoding="utf-8"),
            id="modified-source",
        ),
        pytest.param(
            lambda root: (root / "vla/added.toml").write_text("[tool]\n", encoding="utf-8"),
            id="added-config",
        ),
        pytest.param(
            lambda root: (root / "vla/scripts/entry.sh").rename(root / "vla/scripts/launch.sh"),
            id="renamed-script",
        ),
        pytest.param(lambda root: (root / "vla/workflow.yaml").unlink(), id="deleted-config"),
    ],
)
def test_given_changed_executable_content_when_code_fingerprinted_then_digest_changes(
    change: Callable[[Path], object], tmp_path: Path
) -> None:
    root = _write_code_root(tmp_path / "code")
    original = code_content_fingerprint(root)

    change(root)

    assert code_content_fingerprint(root) != original


def test_given_identical_content_in_different_roots_when_fingerprinted_then_digests_match(tmp_path: Path) -> None:
    assert code_content_fingerprint(_write_code_root(tmp_path / "first")) == code_content_fingerprint(
        _write_code_root(tmp_path / "second")
    )


@pytest.mark.parametrize(
    ("create", "message"),
    [
        pytest.param(lambda root: None, "not a directory", id="missing-root"),
        pytest.param(lambda root: root.mkdir(), "no source or configuration files", id="empty-root"),
    ],
)
def test_given_unusable_code_root_when_fingerprinted_then_it_is_rejected(
    create: Callable[[Path], object], message: str, tmp_path: Path
) -> None:
    root = tmp_path / "code"
    create(root)

    with pytest.raises(ContractError, match=message):
        code_content_fingerprint(root)
