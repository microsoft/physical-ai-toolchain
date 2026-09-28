"""Tests for VLA lifecycle evidence contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from training.vla.scripts.vla_contracts import (
    ContractError,
    RecordKind,
    promotion_policy_fingerprint,
    validate_promotion_policy,
    validate_record,
)

_DIGEST = "a" * 64
_COMMIT = "b" * 40
_OUTPUT_PREFIX = "azureml://jobs/evidence-job/outputs"


def _base_record(kind: RecordKind) -> dict[str, Any]:
    return {"schema_version": 1, "kind": kind.value, "created_at": "2026-09-28T00:00:00Z"}


def _evaluation_record() -> dict[str, Any]:
    return {
        **_base_record(RecordKind.EVALUATION),
        "evaluation_id": "evaluation-1",
        "run_fingerprint": _DIGEST,
        "candidate_fingerprint": _DIGEST,
        "dataset_fingerprint": _DIGEST,
        "status": "complete",
        "outputs_complete": True,
        "episodes_requested": 3,
        "episodes_evaluated": 3,
        "reasons": [],
        "evaluation_output": f"{_OUTPUT_PREFIX}/evaluation",
    }


def _promotion_record() -> dict[str, Any]:
    return {
        **_base_record(RecordKind.PROMOTION),
        "model_name": "vla-candidate",
        "training_fingerprint": _DIGEST,
        "candidate_fingerprint": _DIGEST,
        "evaluation_fingerprint": _DIGEST,
        "policy_fingerprint": _DIGEST,
        "status": "passed",
        "reasons": [],
        "evidence_job": "evidence-job",
        "producer_identity": "trusted-managed-identity",
        "pipeline_contract_fingerprint": _DIGEST,
        "code_revision": _COMMIT,
        "immutable_inputs": {
            "candidate": f"{_OUTPUT_PREFIX}/candidate",
            "candidate_manifest": f"{_OUTPUT_PREFIX}/candidate_manifest",
            "evaluation": f"{_OUTPUT_PREFIX}/evaluation",
            "policy": f"{_OUTPUT_PREFIX}/policy",
            "decision": f"{_OUTPUT_PREFIX}/decision",
        },
    }


def _promotion_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "minimum_episodes": 3,
        "metric_rules": {"mean_mse": {"operator": "lte", "limit": 0.1}},
        "allowed_producer_identity": "trusted-managed-identity",
        "pipeline_contract_fingerprint": _DIGEST,
        "code_revision": _COMMIT,
    }


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
        _evaluation_record(),
        _promotion_record(),
    ],
)
def test_given_complete_lifecycle_evidence_when_validated_then_it_is_accepted(record: dict[str, Any]) -> None:
    validate_record(record)


@pytest.mark.parametrize("status", ["passed", "failed"])
def test_given_quality_status_when_evaluation_validated_then_it_is_rejected(status: str) -> None:
    record = _evaluation_record()
    record["status"] = status

    with pytest.raises(ContractError, match="complete or inconclusive"):
        validate_record(record)


def test_given_zero_coverage_when_evaluation_is_complete_then_it_is_rejected() -> None:
    record = _evaluation_record()
    record["episodes_evaluated"] = 0

    with pytest.raises(ContractError, match="usable complete outputs"):
        validate_record(record)


def test_given_partial_coverage_when_evaluation_is_complete_then_it_is_rejected() -> None:
    record = _evaluation_record()
    record["episodes_evaluated"] = 2

    with pytest.raises(ContractError, match="usable complete outputs"):
        validate_record(record)


@pytest.mark.parametrize("missing_field", ["producer_identity", "pipeline_contract_fingerprint", "immutable_inputs"])
def test_given_missing_provenance_when_promotion_validated_then_it_is_rejected(missing_field: str) -> None:
    record = _promotion_record()
    del record[missing_field]

    with pytest.raises(ContractError):
        validate_record(record)


def test_given_mutable_reference_when_promotion_validated_then_it_is_rejected() -> None:
    record = _promotion_record()
    record["immutable_inputs"]["candidate"] = "azureml:model-candidate:latest"

    with pytest.raises(ContractError, match="immutable Azure ML job output"):
        validate_record(record)


def test_given_valid_policy_when_fingerprinted_then_result_is_deterministic() -> None:
    policy = _promotion_policy()

    assert promotion_policy_fingerprint(policy) == promotion_policy_fingerprint(deepcopy(policy))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("minimum_episodes", 0, "positive integer"),
        ("metric_rules", {}, "non-empty object"),
        ("code_revision", "main", "full lowercase"),
    ],
)
def test_given_permissive_policy_when_validated_then_it_is_rejected(field: str, value: Any, message: str) -> None:
    policy = _promotion_policy()
    policy[field] = value

    with pytest.raises(ContractError, match=message):
        validate_promotion_policy(policy)
