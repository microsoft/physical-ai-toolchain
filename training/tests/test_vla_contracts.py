"""Tests for VLA lifecycle evidence contracts."""

from __future__ import annotations

from typing import Any

import pytest

from training.vla.scripts.vla_contracts import (
    RecordKind,
    validate_record,
)

_DIGEST = "a" * 64
_COMMIT = "b" * 40
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
