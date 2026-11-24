"""Tests for adapter-aware VLA candidate finalization."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from conftest import load_training_module

from training.vla.scripts.finalize_candidate import finalize_candidate, resolve_policy_root, verify_candidate
from training.vla.scripts.preflight_dataset import build_dataset_record
from training.vla.scripts.vla_contracts import ContractError, RecordKind, write_record

_DIGEST = "a" * 64


def _write_evidence(tmp_path: Path) -> tuple[Path, Path]:
    dataset = tmp_path / "dataset"
    (dataset / "meta").mkdir(parents=True)
    (dataset / "data").mkdir()
    info = {
        "total_episodes": 1,
        "features": {"observation.images.base": {"dtype": "video", "shape": [224, 224, 3]}},
    }
    (dataset / "meta/info.json").write_text(json.dumps(info), encoding="utf-8")
    (dataset / "data/episode.parquet").write_bytes(b"episode")
    dataset_record = build_dataset_record(dataset, "azureml:robot-data:7", "org/data", "2026-09-28T00:00:00Z")
    dataset_manifest = tmp_path / "dataset-manifest.json"
    write_record(dataset_manifest, dataset_record)
    training_record = tmp_path / "training-record.json"
    write_record(
        training_record,
        {
            "schema_version": 1,
            "kind": RecordKind.RUN.value,
            "created_at": "2026-09-28T00:00:00Z",
            "run_id": "run-1",
            "identities": {"dataset": _DIGEST},
            "effective_batch": {
                "micro_batch_per_rank": 1,
                "world_size": 1,
                "accumulation_steps": 1,
                "total": 1,
            },
        },
    )
    return training_record, dataset_manifest


def _write_policy(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "config.json").write_text('{"type":"policy"}\n', encoding="utf-8")
    (root / "model.safetensors").write_bytes(b"weights")


@pytest.mark.parametrize(
    ("adapter_name", "policy_type"),
    [("lerobot-pi", "pi0"), ("lerobot-pi", "pi0_fast"), ("lerobot-pi", "pi05"), ("lerobot-smolvla", "smolvla")],
)
def test_given_adapter_valid_checkpoint_when_finalized_then_manifest_verifies(
    tmp_path: Path, adapter_name: str, policy_type: str
) -> None:
    checkpoints = tmp_path / "checkpoints"
    _write_policy(checkpoints / "last/pretrained_model")
    training_record, dataset_manifest = _write_evidence(tmp_path)
    candidate = tmp_path / "candidate"
    manifest_dir = tmp_path / "candidate-manifest"

    finalize_candidate(
        checkpoints,
        training_record,
        dataset_manifest,
        candidate,
        manifest_dir,
        adapter_name,
        policy_type,
        "evidence-job",
    )

    assert verify_candidate(candidate, manifest_dir / "candidate-manifest.json")["policy_type"] == policy_type


def test_given_ambiguous_fallback_when_resolved_then_it_is_rejected(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    _write_policy(checkpoints / "step-1/policy")
    _write_policy(checkpoints / "step-2/policy")

    with pytest.raises(ContractError, match="ambiguous"):
        resolve_policy_root(checkpoints, ("config.json", "model.safetensors"))


def test_given_tampered_candidate_when_verified_then_it_is_rejected(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    _write_policy(checkpoints / "last/pretrained_model")
    training_record, dataset_manifest = _write_evidence(tmp_path)
    candidate = tmp_path / "candidate"
    manifest_dir = tmp_path / "candidate-manifest"
    finalize_candidate(
        checkpoints,
        training_record,
        dataset_manifest,
        candidate,
        manifest_dir,
        "lerobot-pi",
        "pi0",
        "evidence-job",
    )
    (candidate / "model.safetensors").write_bytes(b"tampered")

    with pytest.raises(ContractError, match="does not match"):
        verify_candidate(candidate, manifest_dir / "candidate-manifest.json")


def test_given_finalized_pi05_candidate_when_staged_for_ur10e_then_the_runtime_verifier_accepts_it(
    tmp_path: Path,
) -> None:
    """The UR10e image carries no training code, so its GPU stage re-implements this contract."""
    pytest.importorskip("torch")
    ur10e_offload = load_training_module("ur10e_offload_contract", "gpu-offload/examples/ur10e-single/ur10e_offload.py")
    checkpoints = tmp_path / "checkpoints"
    _write_policy(checkpoints / "last/pretrained_model")
    training_record, dataset_manifest = _write_evidence(tmp_path)
    candidate = tmp_path / "candidate"
    manifest_dir = tmp_path / "candidate-manifest"
    record = finalize_candidate(
        checkpoints,
        training_record,
        dataset_manifest,
        candidate,
        manifest_dir,
        "lerobot-pi",
        "pi05",
        "evidence-job",
    )
    shutil.copy2(manifest_dir / ur10e_offload.CANDIDATE_MANIFEST_NAME, candidate)

    lineage = ur10e_offload.verify_candidate_manifest(str(candidate))

    assert lineage["artifact_manifest_sha256"] == record["artifact_manifest_sha256"]
    (candidate / "model.safetensors").write_bytes(b"other weights")
    with pytest.raises(ur10e_offload.CheckpointVerificationError, match=r"changed: model\.safetensors"):
        ur10e_offload.verify_candidate_manifest(str(candidate))
